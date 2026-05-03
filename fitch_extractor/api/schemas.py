from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DocumentResponse(ApiModel):
    id: str
    company_name: str
    document_name: str
    source_path: str
    fiscal_period: str | None
    status: str
    reported_total: Decimal | None
    currency: str | None
    scale: str | None
    created_at: datetime
    updated_at: datetime
    analysis_notes: str | None = None


class ParsedPageResponse(ApiModel):
    id: str
    document_id: str
    page_number: int
    text: str
    blocks_json: dict
    tables_json: dict
    language: str | None
    parser_sources: tuple[str, ...]
    has_text: bool
    created_at: datetime


class PageCandidateResponse(ApiModel):
    id: str
    document_id: str
    page_number: int
    relevance_score: float
    matched_signals_json: dict
    reason: str


class IngestionSummaryResponse(ApiModel):
    document: DocumentResponse
    page_count: int
    parsed_page_count: int
    candidate_count: int
    no_text_pages: tuple[int, ...]
    candidate_pages: tuple[PageCandidateResponse, ...]


class SegmentRowResponse(ApiModel):
    id: str
    document_id: str
    segment_name: str
    revenue_raw: str | None
    revenue_value: Decimal | None
    currency: str | None
    scale: str | None
    period_label: str | None
    normalized_value: Decimal | None
    page_ref: str | None
    section_ref: str | None
    metric_basis: str | None
    confidence: float | None
    status: str
    extraction_method: str | None
    created_at: datetime
    updated_at: datetime
    row_type: str | None = None
    segment_type: str | None = None
    segment_name_original: str | None = None
    segment_name_normalized: str | None = None
    language: str | None = None
    needs_review: bool | None = None
    classification_rationale: str | None = None


class SegmentEvidenceResponse(ApiModel):
    id: str
    segment_id: str
    document_id: str
    page_number: int
    snippet_text: str
    bbox_json: dict | None
    parser_source: str
    evidence_kind: str
    evidence_original: str | None = None
    evidence_translation: str | None = None
    language: str | None = None


class ValidationIssueResponse(ApiModel):
    id: str
    document_id: str
    segment_id: str | None
    severity: str
    issue_type: str
    message: str
    created_at: datetime


class ValidationIssueReviewResponse(ApiModel):
    issue_id: str
    document_id: str
    status: str
    reviewer: str
    note: str | None
    updated_at: datetime


class ReviewedValidationIssueResponse(ApiModel):
    issue: ValidationIssueResponse
    review: ValidationIssueReviewResponse | None
    blocks_approval: bool
    why_it_matters: str


class DocumentApprovalCheckResponse(ApiModel):
    can_approve: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


class DocumentReviewStateResponse(ApiModel):
    document: DocumentResponse
    page_count: int
    segment_rows: tuple[SegmentRowResponse, ...]
    evidence_by_segment: dict[str, tuple[SegmentEvidenceResponse, ...]]
    nace_candidates_by_segment: dict[str, tuple["NaceCandidateResponse", ...]]
    nace_selection_by_segment: dict[str, "NaceSelectionResponse"]
    esg_factors: tuple["EsgFactorResponse", ...]
    esg_status_by_factor: dict[str, str]
    validation_issues: tuple[ReviewedValidationIssueResponse, ...]
    review_events: tuple["ReviewEventResponse", ...]
    approval_check: DocumentApprovalCheckResponse


class ExtractionSummaryResponse(ApiModel):
    document: DocumentResponse
    prompt_version: str
    provider_name: str
    model: str
    candidate_page_count: int
    bundle_count: int
    extracted_row_count: int
    persisted_row_count: int
    validation_issue_count: int
    segment_rows: tuple[SegmentRowResponse, ...]
    validation_issues: tuple[ValidationIssueResponse, ...]


class NaceCandidateResponse(ApiModel):
    id: str
    segment_id: str
    nace_code: str
    nace_label: str
    nace_level: int
    rank: int
    match_score: float
    rationale: str | None


class NaceSelectionResponse(ApiModel):
    segment_id: str
    nace_code: str
    nace_label: str
    nace_level: int
    match_score: float | None
    rationale: str | None
    source: str
    reviewer: str | None
    updated_at: datetime


class EsgFactorResponse(ApiModel):
    id: str
    segment_id: str | None
    document_id: str
    factor_type: str
    polarity: str
    description: str
    page_ref: str | None
    evidence_text: str
    confidence: float | None
    is_company_wide: bool
    segment_link_type: str | None = None
    esg_category: str | None = None
    score_relevant: bool | None = None
    impact_mechanism: str | None = None
    evidence_source: str | None = None
    cluster_key: str | None = None


class SegmentScoreResponse(ApiModel):
    id: str
    segment_id: str
    base_score: float
    adjustment_score: float
    final_score: float
    weight_share: float | None
    rationale: str | None


class CompanyScoreResponse(ApiModel):
    id: str
    document_id: str
    weighted_average_score: float
    included_weight_share: float
    included_segment_count: int
    denominator_value: Decimal | None
    scale_min: float
    scale_max: float
    score_direction: str
    rationale: str | None
    created_at: datetime


class ReviewEventResponse(ApiModel):
    id: str
    document_id: str
    segment_id: str | None
    reviewer: str
    action: str
    field_changed: str | None
    old_value: str | None
    new_value: str | None
    note: str | None
    timestamp: datetime


class ExportRecordResponse(ApiModel):
    id: str
    document_id: str
    format: str
    path: str
    created_at: datetime


class DocumentExportResponse(ApiModel):
    document_id: str
    output_dir: str
    csv_path: str
    json_path: str
    xlsx_path: str
    exported_at: datetime
    records: tuple[ExportRecordResponse, ...]
