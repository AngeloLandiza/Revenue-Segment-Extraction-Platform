from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from fitch_extractor.enrichment import (
    ROW_TYPE_BUSINESS_SEGMENT,
    SEGMENT_LINK_COMPANY_WIDE,
    SEGMENT_LINK_UNCLEAR,
    classify_segment_row,
    default_esg_link_type,
    esg_category_for_factor,
    score_relevant_esg_link,
)
from fitch_extractor.extraction.config import ExtractionSettings
from fitch_extractor.extraction.json_response import JsonExtractionError, extract_json_object
from fitch_extractor.extraction.prompts import (
    ESG_EXTRACTION_PROMPT_VERSION,
    build_esg_extraction_prompt,
)
from fitch_extractor.extraction.providers import (
    LLMExtractionRequest,
    LLMProvider,
    LLMProviderError,
)
from fitch_extractor.extraction.schemas import ExtractedEsgFactor, EsgExtractionOutput
from fitch_extractor.models import (
    Document,
    EsgFactor,
    ParsedPage,
    SegmentRow,
    SerializableModel,
    ValidationIssue,
)
from fitch_extractor.persistence.repository import SQLiteRepository


ESG_KEYWORDS_BY_TYPE = {
    "emissions_target": (
        "emissions target",
        "greenhouse gas",
        "ghg",
        "net zero",
        "scope 1",
        "scope 2",
        "scope 3",
    ),
    "decarbonization_plan": (
        "decarbonization",
        "decarbonisation",
        "energy transition",
        "transition plan",
        "carbon reduction",
    ),
    "renewable_investment": (
        "renewable",
        "solar",
        "wind",
        "hydrogen",
        "battery storage",
        "clean energy",
    ),
    "fossil_fuel_exposure": ("fossil fuel", "oil and gas", "coal", "lignite", "thermal power"),
    "controversy": ("controversy", "litigation", "investigation", "protest"),
    "regulatory_violation": ("violation", "fine", "penalty", "non-compliance", "enforcement"),
    "safety_incident": ("fatality", "injury", "safety incident", "lost time incident"),
    "social_program": ("community", "social program", "affordable", "accessibility"),
    "labor_issue": ("labor", "labour", "strike", "collective bargaining", "workforce"),
    "circular_economy": ("recycling", "circular economy", "reuse", "waste reduction"),
    "biodiversity_impact": ("biodiversity", "habitat", "deforestation", "protected area"),
    "water_risk": ("water stress", "water risk", "wastewater", "water withdrawal"),
    "governance_policy": ("governance", "board oversight", "ethics", "anti-corruption"),
    "company_wide_policy": ("sustainability policy", "esg strategy", "code of conduct"),
}

MDNA_TERMS = ("management discussion", "md&a", "business review", "risk factors")
GENERIC_BOILERPLATE_TERMS = (
    "content index",
    "esg index",
    "gri index",
    "sasb index",
    "tcfd index",
    "table of contents",
    "cross-reference",
)


@dataclass(frozen=True)
class EsgCandidatePage(SerializableModel):
    page: ParsedPage
    score: float
    matched_terms: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class EsgExtractionSummary(SerializableModel):
    document: Document
    prompt_version: str
    provider_name: str
    model: str
    candidate_page_count: int
    extracted_factor_count: int
    persisted_factor_count: int
    validation_issue_count: int
    esg_factors: tuple[EsgFactor, ...]
    validation_issues: tuple[ValidationIssue, ...]


def select_esg_candidate_pages(
    parsed_pages: list[ParsedPage],
    segment_rows: list[SegmentRow],
    *,
    max_pages: int = 8,
    neighborhood: int = 1,
) -> list[EsgCandidatePage]:
    segment_pages = _segment_page_numbers(segment_rows)
    candidates: list[EsgCandidatePage] = []
    for page in parsed_pages:
        normalized_text = _normalize(page.text)
        if _is_generic_boilerplate(normalized_text):
            continue

        matched_terms = _matched_esg_terms(normalized_text)
        if not matched_terms:
            continue

        score = float(len(matched_terms) * 10)
        reasons = [f"matched ESG terms: {', '.join(matched_terms[:5])}"]
        if any(term in normalized_text for term in MDNA_TERMS):
            score += 5
            reasons.append("near MD&A/risk discussion")
        if _near_segment_page(page.page_number, segment_pages, neighborhood):
            score += 7
            reasons.append("near segment disclosure page")

        candidates.append(
            EsgCandidatePage(
                page=page,
                score=score,
                matched_terms=tuple(matched_terms),
                reason="; ".join(reasons),
            )
        )

    return sorted(candidates, key=lambda item: (-item.score, item.page.page_number))[:max_pages]


class EsgExtractionService:
    def __init__(
        self,
        repository: SQLiteRepository,
        provider: LLMProvider,
        settings: ExtractionSettings | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.settings = settings or ExtractionSettings()

    def extract_document(
        self,
        document_id: str,
        *,
        candidate_limit: int | None = None,
    ) -> EsgExtractionSummary:
        document = self.repository.get_document(document_id)
        if document is None:
            raise KeyError(f"Document not found: {document_id}")

        pages = self.repository.list_parsed_pages(document_id)
        segments = self.repository.list_segment_rows(document_id)
        candidates = select_esg_candidate_pages(pages, segments)
        if candidate_limit is not None:
            candidates = candidates[:candidate_limit]

        issues: list[ValidationIssue] = []
        if not pages:
            issue = self.repository.create_validation_issue(
                document_id=document.id,
                severity="warning",
                issue_type="missing_esg_candidate_pages",
                message="No parsed pages are available for ESG extraction.",
            )
            return self._summary(document, [], 0, [], [issue])
        if not candidates:
            issue = self.repository.create_validation_issue(
                document_id=document.id,
                severity="warning",
                issue_type="no_esg_candidate_pages",
                message="No parsed pages matched deterministic ESG retrieval signals.",
            )
            return self._summary(document, candidates, 0, [], [issue])

        output = self._complete_and_validate(
            document=document,
            pages=[candidate.page for candidate in candidates],
            segments=segments,
            issues=issues,
        )
        if output is None:
            return self._summary(document, candidates, 0, [], issues)

        issues.extend(self._persist_warnings(document, output.extraction_warnings))
        persisted = self._persist_linked_factors(document, output.factors, segments, issues)
        return self._summary(document, candidates, len(output.factors), persisted, issues)

    def _complete_and_validate(
        self,
        *,
        document: Document,
        pages: list[ParsedPage],
        segments: list[SegmentRow],
        issues: list[ValidationIssue],
    ) -> EsgExtractionOutput | None:
        request = LLMExtractionRequest(
            prompt=build_esg_extraction_prompt(
                document=document,
                pages=pages,
                segments=segments,
            ),
            model=self.settings.model,
            prompt_version=ESG_EXTRACTION_PROMPT_VERSION,
            max_tokens=self.settings.max_tokens,
            temperature=self.settings.temperature,
            metadata={"document_id": document.id},
        )
        try:
            response = self.provider.complete_json(request)
        except LLMProviderError as exc:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="error",
                    issue_type="llm_provider_error",
                    message=str(exc),
                )
            )
            return None

        try:
            return EsgExtractionOutput.model_validate_json(extract_json_object(response.content))
        except JsonExtractionError as exc:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="error",
                    issue_type="llm_esg_output_validation",
                    message=str(exc),
                )
            )
            return None
        except ValidationError as exc:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="error",
                    issue_type="llm_esg_output_validation",
                    message=_format_validation_error(exc),
                )
            )
            return None

    def _persist_warnings(self, document: Document, warnings: list[str]) -> list[ValidationIssue]:
        return [
            self.repository.create_validation_issue(
                document_id=document.id,
                severity="warning",
                issue_type="llm_esg_extraction_warning",
                message=warning,
            )
            for warning in warnings
            if warning.strip()
        ]

    def _persist_linked_factors(
        self,
        document: Document,
        extracted_factors: list[ExtractedEsgFactor],
        segments: list[SegmentRow],
        issues: list[ValidationIssue],
    ) -> list[EsgFactor]:
        persisted: list[EsgFactor] = []
        for extracted in extracted_factors:
            linked_segment = link_esg_factor_to_segment(extracted, segments)
            segment_link_type = _segment_link_type(extracted)
            if linked_segment is None:
                segment_link_type = SEGMENT_LINK_COMPANY_WIDE if extracted.is_company_wide else SEGMENT_LINK_UNCLEAR
            if linked_segment is None and should_discard_esg_factor(extracted):
                issues.append(
                    self.repository.create_validation_issue(
                        document_id=document.id,
                        severity="warning",
                        issue_type="generic_esg_boilerplate_skipped",
                        message=f"Skipped generic ESG boilerplate: {extracted.description}",
                    )
                )
                continue

            persisted.append(
                self.repository.create_esg_factor(
                    document_id=document.id,
                    segment_id=linked_segment.id if linked_segment else None,
                    factor_type=extracted.factor_type,
                    polarity=extracted.polarity,
                    description=extracted.description,
                    page_ref=extracted.page_ref,
                    evidence_text=extracted.evidence_text,
                    confidence=extracted.confidence,
                    is_company_wide=linked_segment is None,
                    segment_link_type=segment_link_type,
                    esg_category=extracted.esg_category
                    or esg_category_for_factor(extracted.factor_type),
                    score_relevant=score_relevant_esg_link(
                        segment_link_type,
                        linked_segment is None,
                    )
                    if extracted.score_relevant is None
                    else extracted.score_relevant
                    and score_relevant_esg_link(segment_link_type, linked_segment is None),
                    impact_mechanism=extracted.impact_mechanism or extracted.factor_type,
                    evidence_source=extracted.page_ref,
                )
            )
        return persisted

    def _summary(
        self,
        document: Document,
        candidates: list[EsgCandidatePage],
        extracted_count: int,
        factors: list[EsgFactor],
        issues: list[ValidationIssue],
    ) -> EsgExtractionSummary:
        return EsgExtractionSummary(
            document=document,
            prompt_version=ESG_EXTRACTION_PROMPT_VERSION,
            provider_name=self.provider.name,
            model=self.settings.model,
            candidate_page_count=len(candidates),
            extracted_factor_count=extracted_count,
            persisted_factor_count=len(factors),
            validation_issue_count=len(issues),
            esg_factors=tuple(factors),
            validation_issues=tuple(issues),
        )


def link_esg_factor_to_segment(
    factor: ExtractedEsgFactor,
    segments: list[SegmentRow],
) -> SegmentRow | None:
    if factor.is_company_wide:
        return None

    combined_text = _normalize(
        " ".join(
            value
            for value in (
                factor.segment_name,
                factor.linked_business_activity,
                factor.description,
                factor.evidence_text,
                factor.linkage_rationale,
            )
            if value
        )
    )
    if _is_generic_boilerplate(combined_text):
        return None

    business_segments = [
        segment
        for segment in segments
        if (segment.row_type or classify_segment_row(segment.segment_name).row_type)
        == ROW_TYPE_BUSINESS_SEGMENT
    ]

    for segment in business_segments:
        segment_name = _normalize(segment.segment_name)
        if segment_name and segment_name in combined_text:
            return segment

    for segment in business_segments:
        if _segment_activity_matches(segment.segment_name, combined_text):
            return segment

    return None


def _segment_link_type(factor: ExtractedEsgFactor) -> str:
    if factor.segment_link_type:
        return factor.segment_link_type
    return default_esg_link_type(
        is_company_wide=factor.is_company_wide,
        segment_name=factor.segment_name,
        linked_business_activity=factor.linked_business_activity,
    )


def should_discard_esg_factor(factor: ExtractedEsgFactor) -> bool:
    combined_text = _normalize(f"{factor.description} {factor.evidence_text}")
    if _is_generic_boilerplate(combined_text):
        return True
    if factor.factor_type in {"governance_policy", "company_wide_policy"}:
        material_terms = (
            "fine",
            "penalty",
            "violation",
            "controversy",
            "target",
            "emissions",
            "safety",
            "labor",
            "water",
            "biodiversity",
        )
        return not any(term in combined_text for term in material_terms)
    return False


def _segment_page_numbers(segment_rows: list[SegmentRow]) -> set[int]:
    page_numbers: set[int] = set()
    for row in segment_rows:
        page_number = _page_number_from_ref(row.page_ref)
        if page_number is not None:
            page_numbers.add(page_number)
    return page_numbers


def _page_number_from_ref(page_ref: str | None) -> int | None:
    if not page_ref:
        return None
    match = re.search(r"\d+", page_ref)
    return int(match.group(0)) if match else None


def _near_segment_page(page_number: int, segment_pages: set[int], neighborhood: int) -> bool:
    return any(abs(page_number - segment_page) <= neighborhood for segment_page in segment_pages)


def _matched_esg_terms(normalized_text: str) -> list[str]:
    terms: list[str] = []
    for term_group in ESG_KEYWORDS_BY_TYPE.values():
        for term in term_group:
            if term in normalized_text:
                terms.append(term)
    return sorted(set(terms))


def _segment_activity_matches(segment_name: str, normalized_text: str) -> bool:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", _normalize(segment_name))
        if len(token) >= 4 and token not in {"segment", "business", "division", "group"}
    }
    return bool(tokens) and any(token in normalized_text for token in tokens)


def _is_generic_boilerplate(normalized_text: str) -> bool:
    return any(term in normalized_text for term in GENERIC_BOILERPLATE_TERMS)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _format_validation_error(error: ValidationError) -> str:
    errors: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = item.get("msg", "invalid value")
        errors.append(f"{location}: {message}" if location else message)
    return "; ".join(errors) or "Invalid ESG extraction output"
