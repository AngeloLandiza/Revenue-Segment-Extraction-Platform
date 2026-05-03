from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from revenue_segment_extractor.extraction.json_response import JsonExtractionError, extract_json_object
from revenue_segment_extractor.extraction.providers import LLMExtractionRequest, LLMProvider
from revenue_segment_extractor.nace.retrieval import NaceMatch


PROMPT_VERSION = "nace_mapping_v2"
LEGACY_PROMPT_VERSION = "nace_reranking_v1"
NaceDecisionKind = Literal["mapped", "not_applicable", "needs_review"]


class StrictNaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NaceRerankItem(StrictNaceModel):
    code: str
    rank: int = Field(ge=1)
    rationale: str


class NaceRerankResponse(StrictNaceModel):
    ranked_candidates: list[NaceRerankItem]


class NaceCandidateDecision(StrictNaceModel):
    code: str
    rank: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str

    @field_validator("code", "rationale")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class NaceMappingResponse(StrictNaceModel):
    decision: NaceDecisionKind
    selected_code: str | None
    confidence: float = Field(ge=0, le=1)
    rationale: str
    ranked_candidates: list[NaceCandidateDecision]

    @field_validator("rationale")
    @classmethod
    def require_non_empty_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @field_validator("selected_code")
    @classmethod
    def clean_selected_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


@dataclass(frozen=True)
class NaceMappingDecision:
    decision: NaceDecisionKind
    selected_code: str | None
    confidence: float
    rationale: str
    ranked_candidates: tuple[NaceMatch, ...]


def classify_nace_mapping(
    *,
    company_name: str,
    document_name: str,
    segment_name: str,
    evidence_text: str,
    context: str,
    candidates: tuple[NaceMatch, ...],
    provider: LLMProvider | None,
    model: str = "claude-sonnet-4-6",
    top_n: int = 5,
) -> NaceMappingDecision:
    if not candidates:
        return NaceMappingDecision(
            decision="needs_review",
            selected_code=None,
            confidence=0.0,
            rationale="No NACE candidates were retrieved for this segment.",
            ranked_candidates=(),
        )

    if provider is None:
        top_candidate = candidates[0]
        decision: NaceDecisionKind = "mapped" if top_candidate.score >= 0.55 else "needs_review"
        return NaceMappingDecision(
            decision=decision,
            selected_code=top_candidate.node.code if decision == "mapped" else None,
            confidence=top_candidate.score,
            rationale=(
                "Deterministic fallback used because no LLM provider was configured. "
                + top_candidate.rationale
            ),
            ranked_candidates=candidates[:top_n],
        )

    request = LLMExtractionRequest(
        prompt=_build_mapping_prompt(
            company_name=company_name,
            document_name=document_name,
            segment_name=segment_name,
            evidence_text=evidence_text,
            context=context,
            candidates=candidates,
            top_n=top_n,
        ),
        model=model,
        prompt_version=PROMPT_VERSION,
        max_tokens=1800,
        temperature=0.0,
        metadata={"candidate_codes": [candidate.node.code for candidate in candidates]},
    )
    response = provider.complete_json(request)
    try:
        parsed = NaceMappingResponse.model_validate_json(extract_json_object(response.content))
    except (JsonExtractionError, ValidationError) as exc:
        raise ValueError("Invalid NACE mapping provider response") from exc

    return _apply_mapping_response(parsed, candidates, top_n=top_n)


def rerank_nace_candidates(
    *,
    segment_name: str,
    evidence_text: str,
    context: str,
    candidates: tuple[NaceMatch, ...],
    provider: LLMProvider | None,
    model: str = "claude-sonnet-4-6",
    top_n: int = 3,
) -> tuple[NaceMatch, ...]:
    if provider is None or not candidates:
        return candidates[:top_n]

    request = LLMExtractionRequest(
        prompt=_build_legacy_rerank_prompt(
            segment_name=segment_name,
            evidence_text=evidence_text,
            context=context,
            candidates=candidates,
            top_n=top_n,
        ),
        model=model,
        prompt_version=LEGACY_PROMPT_VERSION,
        max_tokens=1200,
        temperature=0.0,
        metadata={"candidate_codes": [candidate.node.code for candidate in candidates]},
    )
    response = provider.complete_json(request)
    try:
        parsed = NaceRerankResponse.model_validate_json(extract_json_object(response.content))
    except (JsonExtractionError, ValidationError) as exc:
        raise ValueError("Invalid NACE reranking provider response") from exc

    return _apply_legacy_ranking(parsed, candidates, top_n=top_n)


def _build_mapping_prompt(
    *,
    company_name: str,
    document_name: str,
    segment_name: str,
    evidence_text: str,
    context: str,
    candidates: tuple[NaceMatch, ...],
    top_n: int,
) -> str:
    candidate_payload = _candidate_payload(candidates)
    schema = {
        "decision": "mapped | not_applicable | needs_review",
        "selected_code": "string code from candidates, or null",
        "confidence": "number from 0 to 1",
        "rationale": "short evidence-based explanation",
        "ranked_candidates": [
            {
                "code": "candidate code",
                "rank": "integer starting at 1",
                "confidence": "number from 0 to 1",
                "rationale": "why this candidate fits or is less suitable",
            }
        ],
    }
    return (
        "Classify the best NACE Rev.2 code for one revenue segment.\n"
        "Use the company, segment, evidence, and nearby report context. "
        "Use only codes from the supplied candidate list. Do not invent or edit codes.\n"
        "Choose decision='not_applicable' for total, subtotal, elimination, reconciliation, "
        "reported-only, or administrative roll-up rows that are not operating activities.\n"
        "Choose decision='needs_review' when evidence is too thin or candidates do not fit.\n"
        "Choose decision='mapped' only when the selected code is evidence-supported. "
        "Prefer a specific class code when support is clear; otherwise use group, division, "
        "or section.\n"
        f"Return JSON only using this schema and rank up to {top_n} candidates:\n"
        f"{json.dumps(schema, indent=2, sort_keys=True)}\n\n"
        f"Company: {company_name or 'unknown'}\n"
        f"Document: {document_name or 'unknown'}\n"
        f"Segment name: {segment_name}\n"
        f"Evidence text: {evidence_text or 'none'}\n"
        f"Nearby/document context: {context or 'none'}\n"
        "Candidate list JSON:\n"
        f"{json.dumps(candidate_payload, indent=2, sort_keys=True)}"
    )


def _build_legacy_rerank_prompt(
    *,
    segment_name: str,
    evidence_text: str,
    context: str,
    candidates: tuple[NaceMatch, ...],
    top_n: int,
) -> str:
    return (
        "Rank the best NACE Rev.2 candidates for the revenue segment.\n"
        "Use only codes from the supplied candidate list. Do not invent or edit codes.\n"
        "Prefer the most specific reliable level, but fall back to group, division, "
        "or section when evidence is ambiguous.\n"
        f"Return JSON only with ranked_candidates, up to {top_n} items. Each item "
        "must contain code, rank, and rationale.\n\n"
        f"Segment name: {segment_name}\n"
        f"Evidence text: {evidence_text or 'none'}\n"
        f"Additional context: {context or 'none'}\n"
        "Candidate list JSON:\n"
        f"{json.dumps(_candidate_payload(candidates), indent=2, sort_keys=True)}"
    )


def _candidate_payload(candidates: tuple[NaceMatch, ...]) -> list[dict[str, object]]:
    return [
        {
            "code": candidate.node.code,
            "label": candidate.node.label,
            "level": candidate.node.level,
            "level_depth": candidate.node.level_depth,
            "hierarchy_path": " > ".join(candidate.node.hierarchy_path_names),
            "section_code": candidate.node.section_code,
            "division_code": candidate.node.division_code,
            "deterministic_score": candidate.score,
            "deterministic_rationale": candidate.rationale,
        }
        for candidate in candidates
    ]


def _apply_mapping_response(
    response: NaceMappingResponse,
    candidates: tuple[NaceMatch, ...],
    *,
    top_n: int,
) -> NaceMappingDecision:
    by_code = {candidate.node.code: candidate for candidate in candidates}
    returned_codes = [item.code for item in response.ranked_candidates]
    if response.selected_code:
        returned_codes.append(response.selected_code)
    invalid_codes = [code for code in returned_codes if code not in by_code]
    if invalid_codes:
        raise ValueError(f"LLM returned NACE codes outside the supplied candidate set: {invalid_codes}")
    if response.decision == "mapped" and response.selected_code is None:
        raise ValueError("LLM mapped decision requires selected_code")
    if response.decision != "mapped" and response.selected_code is not None:
        raise ValueError("LLM non-mapped decision must not include selected_code")

    selected = _ordered_matches(response.ranked_candidates, candidates, top_n=top_n)
    if response.decision == "mapped" and response.selected_code:
        selected_code = response.selected_code
        if selected and selected[0].node.code != selected_code:
            selected = (by_code[selected_code],) + tuple(
                match for match in selected if match.node.code != selected_code
            )

    return NaceMappingDecision(
        decision=response.decision,
        selected_code=response.selected_code,
        confidence=response.confidence,
        rationale=response.rationale,
        ranked_candidates=selected,
    )


def _apply_legacy_ranking(
    response: NaceRerankResponse,
    candidates: tuple[NaceMatch, ...],
    *,
    top_n: int,
) -> tuple[NaceMatch, ...]:
    by_code = {candidate.node.code: candidate for candidate in candidates}
    invalid_codes = [item.code for item in response.ranked_candidates if item.code not in by_code]
    if invalid_codes:
        raise ValueError(f"LLM returned NACE codes outside the supplied candidate set: {invalid_codes}")
    return _ordered_matches(response.ranked_candidates, candidates, top_n=top_n)


def _ordered_matches(
    items: list[NaceCandidateDecision] | list[NaceRerankItem],
    candidates: tuple[NaceMatch, ...],
    *,
    top_n: int,
) -> tuple[NaceMatch, ...]:
    by_code = {candidate.node.code: candidate for candidate in candidates}
    ordered_items = sorted(items, key=lambda item: item.rank)
    selected: list[NaceMatch] = []
    seen_codes: set[str] = set()
    for item in ordered_items:
        if item.code in seen_codes:
            continue
        candidate = by_code[item.code]
        selected.append(replace(candidate, rationale=item.rationale.strip()))
        seen_codes.add(item.code)
        if len(selected) == top_n:
            break

    for candidate in candidates:
        if len(selected) == top_n:
            break
        if candidate.node.code not in seen_codes:
            selected.append(candidate)
            seen_codes.add(candidate.node.code)

    return tuple(selected)
