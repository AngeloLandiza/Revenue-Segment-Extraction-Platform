from __future__ import annotations

from dataclasses import dataclass

from fitch_extractor.extraction.providers import LLMProvider
from fitch_extractor.extraction.config import DEFAULT_EXTRACTION_MODEL
from fitch_extractor.enrichment import (
    ROW_TYPE_BUSINESS_SEGMENT,
    SEGMENT_TYPE_SINGLE_ACTIVITY,
    build_evidence_bundle,
    classify_segment_row,
)
from fitch_extractor.models import (
    NaceCandidate,
    REVIEWABLE_SEGMENT_STATUSES,
    NaceSelection,
    SegmentRow,
)
from fitch_extractor.nace.reference import NaceNode, load_nace_nodes
from fitch_extractor.nace.rerank import NaceMappingDecision, classify_nace_mapping
from fitch_extractor.nace.retrieval import NaceMatch, retrieve_nace_candidates
from fitch_extractor.persistence.repository import SQLiteRepository


DETERMINISTIC_CANDIDATE_LIMIT = 30
STORED_CANDIDATE_LIMIT = 5
AUTO_SELECTION_CONFIDENCE_THRESHOLD = 0.65
CONTEXT_NEIGHBORHOOD = 2
CONTEXT_CHARACTER_LIMIT = 8000
REPLACEABLE_SELECTION_SOURCES = {
    "candidate",
    "llm_candidate",
    "deterministic_fallback",
}


@dataclass(frozen=True)
class NaceMappingResult:
    segment_id: str
    candidates: tuple[NaceCandidate, ...]
    decision: str
    selected_code: str | None
    confidence: float
    rationale: str


class NaceMappingService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        reference_nodes: tuple[NaceNode, ...] | None = None,
        provider: LLMProvider | None = None,
        model: str = DEFAULT_EXTRACTION_MODEL,
    ) -> None:
        self.repository = repository
        self.reference_nodes = reference_nodes or load_nace_nodes()
        self.provider = provider
        self.model = model

    def map_document(self, document_id: str) -> tuple[NaceMappingResult, ...]:
        results: list[NaceMappingResult] = []
        for row in self.repository.list_segment_rows(document_id):
            if row.status in REVIEWABLE_SEGMENT_STATUSES:
                results.append(self.map_segment(row))
        return tuple(results)

    def map_segment(self, row: SegmentRow, *, context: str = "") -> NaceMappingResult:
        document = self.repository.get_document(row.document_id)
        evidence_items = _segment_evidence_items(self.repository, row.id)
        mapping_context = _segment_mapping_context(
            self.repository,
            row,
            additional_context=context,
        )
        evidence_bundle = build_evidence_bundle(evidence_items=evidence_items)
        evidence_text = evidence_bundle.classification_text
        classification = classify_segment_row(
            row.segment_name,
            evidence_text=" ".join((evidence_text, mapping_context)),
            language=row.language or evidence_bundle.language,
            normalized_value=row.normalized_value,
        )
        company_name = document.company_name if document else ""
        document_name = document.document_name if document else ""

        if classification.row_type != ROW_TYPE_BUSINESS_SEGMENT:
            self._clear_replaceable_selection(row.id)
            self.repository.replace_nace_candidates(row.id, [])
            return NaceMappingResult(
                segment_id=row.id,
                candidates=(),
                decision="not_applicable",
                selected_code=None,
                confidence=1.0,
                rationale=(
                    "Segment is classified as "
                    f"{classification.row_type}; NACE applies only to business segments."
                ),
            )

        deterministic = retrieve_nace_candidates(
            self.reference_nodes,
            segment_name=row.segment_name,
            evidence_text=evidence_text,
            context=" ".join((company_name, document_name, mapping_context)),
            limit=DETERMINISTIC_CANDIDATE_LIMIT,
        )
        candidates = _merge_matches(
            deterministic,
            _domain_hint_matches(self.reference_nodes, row, evidence_text, mapping_context),
        )[:DETERMINISTIC_CANDIDATE_LIMIT]
        try:
            decision = classify_nace_mapping(
                company_name=company_name,
                document_name=document_name,
                segment_name=row.segment_name,
                evidence_text=evidence_text,
                context=_nace_context_with_metadata(
                    classification=classification,
                    evidence_bundle=evidence_bundle,
                    additional_context=mapping_context,
                ),
                candidates=candidates,
                provider=self.provider,
                model=self.model,
                top_n=STORED_CANDIDATE_LIMIT,
            )
        except ValueError as exc:
            stored = self._store_candidates(row.id, candidates[:STORED_CANDIDATE_LIMIT])
            self._clear_replaceable_selection(row.id)
            _create_unique_nace_warning(self.repository, row, str(exc))
            return NaceMappingResult(
                segment_id=row.id,
                candidates=tuple(stored),
                decision="needs_review",
                selected_code=None,
                confidence=0.0,
                rationale=f"NACE mapping requires review because provider output was rejected: {exc}",
            )
        stored = self._store_candidates(row.id, decision.ranked_candidates[:STORED_CANDIDATE_LIMIT])
        if classification.segment_type == SEGMENT_TYPE_SINGLE_ACTIVITY:
            self._apply_selection(row.id, stored, decision)
        else:
            self._clear_replaceable_selection(row.id)
            decision = NaceMappingDecision(
                decision="needs_review",
                selected_code=decision.selected_code,
                confidence=min(decision.confidence, 0.6),
                rationale=(
                    f"{decision.rationale} Segment type is {classification.segment_type}; "
                    "review is required before selecting a primary NACE code."
                ),
                ranked_candidates=decision.ranked_candidates,
            )
        return NaceMappingResult(
            segment_id=row.id,
            candidates=tuple(stored),
            decision=decision.decision,
            selected_code=decision.selected_code,
            confidence=decision.confidence,
            rationale=decision.rationale,
        )

    def _store_candidates(
        self,
        segment_id: str,
        matches: tuple[NaceMatch, ...],
    ) -> list[NaceCandidate]:
        candidates = [
            NaceCandidate(
                id="",
                segment_id=segment_id,
                nace_code=match.node.code,
                nace_label=match.node.label,
                nace_level=match.node.level_depth,
                rank=index,
                match_score=match.score,
                rationale=match.rationale,
            )
            for index, match in enumerate(matches, start=1)
        ]
        return self.repository.replace_nace_candidates(segment_id, candidates)

    def _apply_selection(
        self,
        segment_id: str,
        candidates: list[NaceCandidate],
        decision: NaceMappingDecision,
    ) -> None:
        existing = self.repository.get_nace_selection(segment_id)
        if existing is not None and not _is_replaceable_selection(existing):
            return

        if (
            decision.decision != "mapped"
            or not decision.selected_code
            or decision.confidence < AUTO_SELECTION_CONFIDENCE_THRESHOLD
        ):
            self._clear_replaceable_selection(segment_id)
            return

        selected_candidates = [
            candidate for candidate in candidates if candidate.nace_code == decision.selected_code
        ]
        if not selected_candidates:
            self._clear_replaceable_selection(segment_id)
            return

        top_candidate = selected_candidates[0]
        self.repository.upsert_nace_selection(
            segment_id=segment_id,
            nace_code=top_candidate.nace_code,
            nace_label=top_candidate.nace_label,
            nace_level=top_candidate.nace_level,
            match_score=decision.confidence,
            rationale=decision.rationale,
            source="llm_candidate" if self.provider is not None else "deterministic_fallback",
        )

    def _clear_replaceable_selection(self, segment_id: str) -> None:
        existing = self.repository.get_nace_selection(segment_id)
        if existing is None or _is_replaceable_selection(existing):
            self.repository.delete_nace_selection(segment_id)


def _segment_evidence_items(
    repository: SQLiteRepository,
    segment_id: str,
) -> list[tuple[int, str, str | None]]:
    return [
        (
            evidence.page_number,
            evidence.evidence_original or evidence.snippet_text,
            evidence.language,
        )
        for evidence in repository.list_segment_evidence(segment_id)
        if (evidence.evidence_original or evidence.snippet_text).strip()
    ]


def _segment_mapping_context(
    repository: SQLiteRepository,
    row: SegmentRow,
    *,
    additional_context: str = "",
) -> str:
    evidence_pages = [
        evidence.page_number for evidence in repository.list_segment_evidence(row.id)
    ]
    page_numbers = {
        page_number + offset
        for page_number in evidence_pages
        for offset in range(-CONTEXT_NEIGHBORHOOD, CONTEXT_NEIGHBORHOOD + 1)
        if page_number + offset > 0
    }
    pages = [
        page
        for page in repository.list_parsed_pages(row.document_id)
        if page.page_number in page_numbers
    ]
    context_parts = [additional_context.strip()] if additional_context.strip() else []
    for page in pages:
        text = " ".join(page.text.split())
        if text:
            context_parts.append(f"Page {page.page_number}: {text}")
    return "\n".join(context_parts)[:CONTEXT_CHARACTER_LIMIT]

def _nace_context_with_metadata(*, classification, evidence_bundle, additional_context: str) -> str:
    parts = [
        f"Row type: {classification.row_type}",
        f"Segment type: {classification.segment_type}",
        f"Segment name original: {classification.segment_name_original}",
        f"Segment name normalized: {classification.segment_name_normalized}",
        f"Language: {classification.language or evidence_bundle.language or 'unknown'}",
        f"Needs review: {classification.needs_review}",
        f"NACE reasoning guardrail: {classification.rationale}",
        f"Evidence bundle reasoning: {evidence_bundle.reasoning}",
    ]
    if evidence_bundle.evidence_translation:
        parts.append(f"Normalized English evidence: {evidence_bundle.evidence_translation}")
    if evidence_bundle.evidence_original:
        parts.append(evidence_bundle.evidence_original)
    if additional_context.strip():
        parts.append(additional_context.strip())
    return "\n".join(parts)


def _domain_hint_matches(
    nodes: tuple[NaceNode, ...],
    row: SegmentRow,
    evidence_text: str,
    context: str,
) -> tuple[NaceMatch, ...]:
    text = " ".join((row.segment_name, evidence_text, context)).casefold()
    row_specific_text = " ".join((row.segment_name, evidence_text)).casefold()
    hinted_codes: list[tuple[str, float, str]] = []
    if any(term in text for term in ("offshore", "onshore", "wind", "generation of power")):
        hinted_codes.append(("35.11", 0.92, "domain hint: power generation / wind activity"))
    if any(term in text for term in ("energy transport", "network operator", "metering services")):
        hinted_codes.append(("35.13", 0.92, "domain hint: electricity distribution network activity"))
    if "transmission" in text and "electric" in text:
        hinted_codes.append(("35.12", 0.88, "domain hint: electricity transmission activity"))
    if "sale of gas" in text or "distribution of gaseous fuels" in text:
        hinted_codes.append(("35.22", 0.86, "domain hint: gaseous fuel distribution activity"))
    if "steam" in row_specific_text or "heat" in row_specific_text:
        hinted_codes.append(("35.30", 0.82, "domain hint: heat or steam supply activity"))
    if "cloud" in text and any(term in text for term in ("hosting", "infrastructure", "platform")):
        hinted_codes.append(("63.11", 0.84, "domain hint: data processing / hosting activity"))
    has_utility_project_context = any(
        term in text
        for term in (
            "electric vehicle charging infrastructure",
            "ev charging infrastructure",
            "electrical service upgrades",
            "utility projects",
            "substation",
            "transformer station",
            "telecommunications infrastructure",
        )
    )
    has_related_segment_label = any(
        term in row_specific_text
        for term in (
            "commercial services",
            "utility services",
            "construction",
            "infrastructure",
            "telecommunications",
            "electrical service",
        )
    )
    if has_utility_project_context and has_related_segment_label:
        hinted_codes.append(
            (
                "42.22",
                0.78,
                "domain hint: utility electricity or telecommunications infrastructure construction",
            )
        )

    by_code = {node.code: node for node in nodes}
    return tuple(
        NaceMatch(node=by_code[code], score=score, rationale=rationale)
        for code, score, rationale in hinted_codes
        if code in by_code
    )


def _merge_matches(
    primary: tuple[NaceMatch, ...],
    hinted: tuple[NaceMatch, ...],
) -> tuple[NaceMatch, ...]:
    by_code: dict[str, NaceMatch] = {}
    for match in (*hinted, *primary):
        existing = by_code.get(match.node.code)
        if existing is None or match.score > existing.score:
            by_code[match.node.code] = match
    return tuple(
        sorted(
            by_code.values(),
            key=lambda match: (match.score, match.node.level_depth, -match.node.source_row_number),
            reverse=True,
        )
    )


def _create_unique_nace_warning(
    repository: SQLiteRepository,
    row: SegmentRow,
    message: str,
) -> None:
    issue_message = f"NACE mapping requires review for {row.segment_name}: {message}"
    existing = repository.list_validation_issues(row.document_id)
    if any(
        issue.segment_id == row.id
        and issue.issue_type == "nace_mapping_candidate_validation"
        and issue.message == issue_message
        for issue in existing
    ):
        return
    repository.create_validation_issue(
        document_id=row.document_id,
        segment_id=row.id,
        severity="warning",
        issue_type="nace_mapping_candidate_validation",
        message=issue_message,
    )


def _is_replaceable_selection(selection: NaceSelection) -> bool:
    return selection.source in REPLACEABLE_SELECTION_SOURCES
