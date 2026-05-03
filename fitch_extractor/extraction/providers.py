from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


ANTHROPIC_JSON_SYSTEM_PROMPT = (
    "You are a strict JSON extraction engine. Return one valid JSON object only. "
    "Do not include Markdown, code fences, explanations, or text before or after the JSON."
)

SENSITIVE_ENV_NAMES = ("ANTHROPIC_API_KEY",)
API_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class LLMExtractionRequest:
    prompt: str
    model: str
    prompt_version: str
    max_tokens: int
    temperature: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMExtractionResponse:
    content: str
    model: str
    provider_name: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    name: str

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        ...


class LLMProviderError(RuntimeError):
    pass


class FakeRevenueExtractionProvider:
    name = "fake"

    def __init__(self, response_text: str | None = None) -> None:
        self._response_text = response_text

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        if self._response_text is not None:
            content = self._response_text
        elif request.prompt_version == "candidate_page_discovery_v1":
            content = json.dumps({"selected_pages": [], "extraction_warnings": []})
        elif request.prompt_version == "second_pass_verification_v1":
            content = _build_fake_verification_response(request.prompt)
        elif request.prompt_version == "revenue_arbitration_v1":
            content = _build_fake_arbitration_response(request.prompt)
        elif request.prompt_version == "nace_mapping_v2":
            content = _build_fake_nace_mapping_response(request.prompt)
        elif request.prompt_version == "nace_reranking_v1":
            content = _build_fake_nace_reranking_response(request.prompt)
        elif request.prompt_version == "esg_extraction_v1":
            content = _build_fake_esg_response(request.prompt)
        else:
            content = _build_fake_response(request.prompt)
        return LLMExtractionResponse(
            content=content,
            model=request.model,
            provider_name=self.name,
        )


class AnthropicRevenueExtractionProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise LLMProviderError("ANTHROPIC_API_KEY is required for Anthropic extraction")

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise LLMProviderError(
                "The anthropic package is required for Anthropic extraction"
            ) from exc

        try:
            client = Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system=ANTHROPIC_JSON_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": request.prompt}],
            )
        except Exception as exc:
            detail = _redact_sensitive(str(exc).strip())
            message = (
                f"Anthropic extraction failed for model {request.model!r}: "
                f"{type(exc).__name__}"
            )
            if detail:
                message = f"{message}: {detail}"
            raise LLMProviderError(message) from exc

        return LLMExtractionResponse(
            content=_anthropic_text(response),
            model=request.model,
            provider_name=self.name,
            input_tokens=_anthropic_usage_tokens(response, "input_tokens"),
            output_tokens=_anthropic_usage_tokens(response, "output_tokens"),
        )


def create_provider(provider_name: str) -> LLMProvider:
    normalized_name = provider_name.strip().lower()
    if normalized_name == "fake":
        return FakeRevenueExtractionProvider()
    if normalized_name == "anthropic":
        return AnthropicRevenueExtractionProvider()
    raise ValueError(f"Unsupported extraction provider: {provider_name}")


def _redact_sensitive(message: str) -> str:
    redacted = API_KEY_PATTERN.sub("[REDACTED_API_KEY]", message)
    for env_name in SENSITIVE_ENV_NAMES:
        secret = os.getenv(env_name)
        if secret:
            redacted = redacted.replace(secret, "[REDACTED_API_KEY]")
    return redacted


def _anthropic_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _anthropic_usage_tokens(response: Any, field_name: str) -> int | None:
    usage = getattr(response, "usage", None)
    value = getattr(usage, field_name, None)
    return int(value) if isinstance(value, int) else None


def _build_fake_response(prompt: str) -> str:
    rows = _extract_rows_from_prompt(prompt)
    context = _extract_context(prompt)
    return json.dumps(
        {
            "company_name": context.get("company_name"),
            "document_name": context.get("document_name"),
            "fiscal_period": context.get("fiscal_period"),
            "reported_total": None,
            "currency": context.get("currency"),
            "scale": context.get("scale"),
            "rows": rows,
            "extraction_warnings": [],
        },
        sort_keys=True,
    )


def _build_fake_verification_response(prompt: str) -> str:
    rows = _extract_json_array_after_marker(prompt, "Extracted rows JSON:")
    confirmed_rows = [
        {
            "segment_name": str(row.get("segment_name", "unknown")),
            "page_ref": row.get("page_ref"),
            "confidence": row.get("confidence"),
            "rationale": "Fake verifier confirmed the row shape and evidence fields.",
        }
        for row in rows
        if isinstance(row, dict)
    ]
    return json.dumps(
        {
            "confirmed_rows": confirmed_rows,
            "suspected_errors": [],
            "missing_rows": [],
            "correction_suggestions": [],
            "rationale": "Fake verifier did not find additional issues.",
        },
        sort_keys=True,
    )


def _build_fake_arbitration_response(prompt: str) -> str:
    rows = _extract_json_array_after_marker(prompt, "Extracted rows JSON:")
    accepted_rows = [
        {
            "segment_name": str(row.get("segment_name", "unknown")),
            "page_ref": row.get("page_ref"),
            "confidence": row.get("confidence"),
            "rationale": "Fake arbitrator accepted the row for continued human review.",
        }
        for row in rows
        if isinstance(row, dict)
    ]
    return json.dumps(
        {
            "accepted_rows": accepted_rows,
            "rejected_rows": [],
            "missing_rows": [],
            "correction_suggestions": [],
            "requires_human_review": bool(rows),
            "rationale": "Fake arbitrator produced deterministic arbitration output.",
        },
        sort_keys=True,
    )


def _build_fake_nace_reranking_response(prompt: str) -> str:
    candidates = _extract_nace_candidates_from_prompt(prompt)
    return json.dumps(
        {
            "ranked_candidates": [
                {
                    "code": str(candidate.get("code", "")),
                    "rank": index,
                    "rationale": "Fake NACE reranker preserved deterministic order.",
                }
                for index, candidate in enumerate(candidates[:3], start=1)
                if isinstance(candidate, dict) and candidate.get("code")
            ]
        },
        sort_keys=True,
    )


def _build_fake_nace_mapping_response(prompt: str) -> str:
    candidates = _extract_nace_candidates_from_prompt(prompt)
    candidate_marker = "Candidate list JSON:"
    candidate_start = prompt.find(candidate_marker)
    evidence_prompt = prompt[:candidate_start] if candidate_start >= 0 else prompt
    segment_name = (_match_context_value(prompt, "Segment name") or "").casefold()
    combined_text = evidence_prompt.casefold()
    if any(
        term in segment_name
        for term in (
            "total",
            "elimination",
            "eliminations",
            "reportable segments",
            "reported",
            "reclassification",
            "reconciling",
            "hedging gains",
            "hedging losses",
        )
    ):
        return json.dumps(
            {
                "decision": "not_applicable",
                "selected_code": None,
                "confidence": 1.0,
                "rationale": "Fake NACE mapper marked this as a non-operating roll-up/reconciliation row.",
                "ranked_candidates": [],
            },
            sort_keys=True,
        )

    selected_code = _fake_nace_selected_code(segment_name, combined_text, candidates)
    decision = "mapped" if selected_code else "needs_review"
    confidence = 0.86 if selected_code else 0.0
    ranked = _fake_ranked_nace_candidates(selected_code, candidates)
    return json.dumps(
        {
            "decision": decision,
            "selected_code": selected_code,
            "confidence": confidence,
            "rationale": (
                "Fake NACE mapper selected the best candidate from supplied evidence/context."
                if selected_code
                else "Fake NACE mapper found no supportable candidate."
            ),
            "ranked_candidates": ranked,
        },
        sort_keys=True,
    )


def _extract_nace_candidates_from_prompt(prompt: str) -> list[dict[str, Any]]:
    marker = "Candidate list JSON:"
    start = prompt.find(marker)
    if start < 0:
        return []
    try:
        candidates = json.loads(prompt[start + len(marker) :].strip())
    except json.JSONDecodeError:
        return []
    return [candidate for candidate in candidates if isinstance(candidate, dict)]


def _fake_nace_selected_code(
    segment_name: str,
    combined_text: str,
    candidates: list[dict[str, Any]],
) -> str | None:
    candidate_codes = {str(candidate.get("code", "")) for candidate in candidates}
    preference_sets = []
    if any(term in segment_name for term in ("offshore", "onshore", "bioenergy")) and any(
        term in combined_text for term in ("wind", "generation of power", "power")
    ):
        preference_sets.append(("35.11", "35.1", "35"))
    if any(term in segment_name for term in ("network operator", "liander")) and any(
        term in combined_text for term in ("energy transport", "metering services", "connection")
    ):
        preference_sets.append(("35.13", "35.1", "35"))
    if "insurance" in segment_name:
        preference_sets.append(("65.12", "65.1", "65"))
    if any(term in segment_name for term in ("banking", "markets", "investment", "brokerage")):
        preference_sets.append(("64.19", "64"))
    if "cloud" in segment_name:
        preference_sets.append(("63.11", "62", "63"))

    for codes in preference_sets:
        for code in codes:
            if code in candidate_codes:
                return code
    return None


def _fake_ranked_nace_candidates(
    selected_code: str | None,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    if selected_code:
        selected = next(
            (candidate for candidate in candidates if str(candidate.get("code", "")) == selected_code),
            None,
        )
        if selected is not None:
            ordered.append(selected)
    for candidate in candidates:
        if len(ordered) == 5:
            break
        if candidate not in ordered:
            ordered.append(candidate)

    return [
        {
            "code": str(candidate.get("code", "")),
            "rank": index,
            "confidence": 0.86 if str(candidate.get("code", "")) == selected_code else 0.55,
            "rationale": "Fake NACE mapper ranked this candidate from supplied evidence/context.",
        }
        for index, candidate in enumerate(ordered[:5], start=1)
        if candidate.get("code")
    ]


def _build_fake_esg_response(prompt: str) -> str:
    segments = _extract_segments_from_prompt(prompt)
    factors: list[dict[str, Any]] = []
    for page_number, page_text in _extract_page_sections(prompt):
        normalized_text = page_text.casefold()
        if any(term in normalized_text for term in ("esg index", "gri index", "tcfd index")):
            continue

        factor_type = _fake_esg_factor_type(normalized_text)
        if factor_type is None:
            continue

        matched_segment = _fake_esg_segment_match(normalized_text, segments)
        evidence = _first_sentence(page_text)
        factors.append(
            {
                "factor_type": factor_type,
                "polarity": _fake_esg_polarity(factor_type),
                "description": evidence,
                "page_ref": f"p. {page_number}",
                "evidence_text": evidence,
                "confidence": 0.82,
                "is_company_wide": matched_segment is None,
                "segment_name": matched_segment,
                "linked_business_activity": matched_segment,
                "linkage_rationale": (
                    f"Evidence explicitly mentions {matched_segment}."
                    if matched_segment
                    else "Evidence is phrased as company-wide ESG context."
                ),
                "segment_link_type": (
                    "direct_segment_name" if matched_segment else "company_wide"
                ),
                "esg_category": _fake_esg_category(factor_type),
                "score_relevant": matched_segment is not None,
                "impact_mechanism": factor_type,
            }
        )
    return json.dumps({"factors": factors, "extraction_warnings": []}, sort_keys=True)


def _extract_context(prompt: str) -> dict[str, str | None]:
    return {
        "company_name": _match_context_value(prompt, "Company"),
        "document_name": _match_context_value(prompt, "Document"),
        "fiscal_period": _match_context_value(prompt, "Fiscal period"),
        "currency": _infer_currency(prompt),
        "scale": _infer_scale(prompt),
    }


def _extract_segments_from_prompt(prompt: str) -> list[str]:
    marker = "Segment context JSON:"
    schema_marker = "Required JSON schema:"
    start = prompt.find(marker)
    end = prompt.find(schema_marker, start)
    if start < 0 or end < 0:
        return []
    try:
        rows = json.loads(prompt[start + len(marker) : end].strip())
    except json.JSONDecodeError:
        return []
    return [
        str(row.get("segment_name", "")).strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("segment_name", "")).strip()
    ]


def _extract_page_sections(prompt: str) -> list[tuple[int, str]]:
    sections: list[tuple[int, str]] = []
    matches = list(re.finditer(r"^Page\s+(\d+)$", prompt, flags=re.MULTILINE))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        sections.append((int(match.group(1)), prompt[start:end].strip()))
    return sections


def _fake_esg_factor_type(normalized_text: str) -> str | None:
    if "coal" in normalized_text:
        return "coal_phaseout" if "phase" in normalized_text else "fossil_fuel_exposure"
    if any(term in normalized_text for term in ("renewable", "solar", "wind")):
        return "renewable_investment"
    if any(term in normalized_text for term in ("emissions target", "net zero", "ghg")):
        return "emissions_target"
    if any(term in normalized_text for term in ("safety incident", "fatality", "injury")):
        return "safety_incident"
    if any(term in normalized_text for term in ("water risk", "water stress")):
        return "water_risk"
    if any(term in normalized_text for term in ("violation", "fine", "penalty")):
        return "regulatory_violation"
    if "governance" in normalized_text:
        return "governance_policy"
    return None


def _fake_esg_polarity(factor_type: str) -> str:
    if factor_type in {
        "fossil_fuel_exposure",
        "controversy",
        "regulatory_violation",
        "safety_incident",
        "labor_issue",
        "biodiversity_impact",
        "water_risk",
    }:
        return "negative"
    if factor_type in {
        "emissions_target",
        "decarbonization_plan",
        "renewable_investment",
        "coal_phaseout",
        "social_program",
        "circular_economy",
    }:
        return "positive"
    return "neutral"


def _fake_esg_category(factor_type: str) -> str:
    if factor_type in {
        "emissions_target",
        "decarbonization_plan",
        "renewable_investment",
        "coal_phaseout",
        "fossil_fuel_exposure",
        "circular_economy",
        "biodiversity_impact",
        "water_risk",
    }:
        return "E"
    if factor_type in {"social_program", "labor_issue", "safety_incident"}:
        return "S"
    if factor_type in {"governance_policy", "company_wide_policy", "controversy", "regulatory_violation"}:
        return "G"
    return "unknown"


def _fake_esg_segment_match(normalized_text: str, segments: list[str]) -> str | None:
    for segment in segments:
        if segment.casefold() in normalized_text:
            return segment
    return None


def _first_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"^Section:\s*[^T]+Text:\s*", "", cleaned)
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return sentences[0][:500] if sentences and sentences[0] else cleaned[:500]


def _match_context_value(prompt: str, label: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", prompt, flags=re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    return None if value.lower() in {"unknown", "none", "null"} else value


def _extract_rows_from_prompt(prompt: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_page: int | None = None
    current_section: str | None = None
    table_header: list[str] | None = None
    revenue_index: int | None = None

    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        page_match = re.match(r"^Page\s+(\d+)$", line)
        if page_match:
            current_page = int(page_match.group(1))
            current_section = None
            table_header = None
            revenue_index = None
            continue

        if line.startswith("Section:"):
            current_section = line.removeprefix("Section:").strip() or None
            continue

        if not line.startswith("|") or not line.endswith("|"):
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue

        lowered_cells = [cell.lower() for cell in cells]
        if table_header is None and "segment" in lowered_cells[0]:
            table_header = cells
            revenue_index = _find_revenue_column(lowered_cells)
            continue

        if table_header is None or revenue_index is None or len(cells) <= revenue_index:
            continue

        segment_name = cells[0].strip()
        if not segment_name or segment_name.lower() in {"total", "subtotal"}:
            continue

        revenue_raw = cells[revenue_index].strip()
        revenue_value = _parse_decimal(revenue_raw)
        if not revenue_raw or revenue_value is None:
            continue

        rows.append(
            {
                "segment_name": segment_name,
                "revenue_raw": revenue_raw,
                "revenue_value": str(revenue_value),
                "currency": _infer_currency(prompt),
                "scale": _infer_scale(prompt),
                "period_label": _match_context_value(prompt, "Fiscal period"),
                "page_ref": f"p. {current_page}" if current_page is not None else None,
                "section_ref": current_section,
                "metric_basis": table_header[revenue_index],
                "evidence_text": " | ".join([segment_name, revenue_raw]),
                "confidence": 0.9,
                "extraction_notes": "Deterministic fake provider extracted a table row.",
            }
        )

    return rows


def _extract_json_array_after_marker(prompt: str, marker: str) -> list[Any]:
    start = prompt.find(marker)
    if start < 0:
        return []
    array_start = prompt.find("[", start)
    if array_start < 0:
        return []

    depth = 0
    in_string = False
    escaped = False
    for index in range(array_start, len(prompt)):
        char = prompt[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(prompt[array_start : index + 1])
                except json.JSONDecodeError:
                    return []
                return parsed if isinstance(parsed, list) else []
    return []


def _find_revenue_column(lowered_cells: list[str]) -> int | None:
    preferred_terms = ("external revenue", "segment revenue", "net sales", "turnover", "revenue")
    for term in preferred_terms:
        for index, cell in enumerate(lowered_cells):
            if term in cell:
                return index
    return None


def _parse_decimal(raw_value: str) -> Decimal | None:
    cleaned = raw_value.strip().replace(",", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    cleaned = re.sub(r"^[^\d.-]+", "", cleaned)
    cleaned = re.sub(r"[^\d.-]+$", "", cleaned)
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def _infer_currency(prompt: str) -> str | None:
    upper_prompt = prompt.upper()
    if "USD" in upper_prompt or "$" in prompt:
        return "USD"
    if "EUR" in upper_prompt or "€" in prompt:
        return "EUR"
    if "GBP" in upper_prompt or "£" in prompt:
        return "GBP"
    return None


def _infer_scale(prompt: str) -> str | None:
    lowered_prompt = prompt.lower()
    if "millions" in lowered_prompt or "million" in lowered_prompt:
        return "millions"
    if "billions" in lowered_prompt or "billion" in lowered_prompt:
        return "billions"
    if "thousands" in lowered_prompt or "thousand" in lowered_prompt:
        return "thousands"
    return None
