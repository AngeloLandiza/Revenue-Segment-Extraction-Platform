from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


JsonDict = dict[str, Any]

DOCUMENT_STATUS_NEW = "new"
DOCUMENT_STATUS_PARSED = "parsed"
DOCUMENT_STATUS_EXTRACTED = "extracted"
DOCUMENT_STATUS_VALIDATED = "validated"
DOCUMENT_STATUS_NEEDS_REVIEW = "needs_review"
DOCUMENT_STATUS_READY_FOR_REVIEW = "ready_for_review"
DOCUMENT_STATUS_APPROVED = "approved"
DOCUMENT_STATUS_REJECTED = "rejected"
DOCUMENT_STATUS_EXPORTED = "exported"
DOCUMENT_STATUS_FAILED = "failed"

QUEUE_STATUS_PENDING = "pending"
QUEUE_STATUS_RUNNING = "running"
QUEUE_STATUS_COMPLETED = "completed"
QUEUE_STATUS_FAILED = "failed"

SEGMENT_STATUS_PENDING = "pending"
SEGMENT_STATUS_PARSED = "parsed"
SEGMENT_STATUS_EXTRACTED = "extracted"
SEGMENT_STATUS_VALIDATED = "validated"
SEGMENT_STATUS_NEEDS_REVIEW = "needs_review"
SEGMENT_STATUS_READY_FOR_REVIEW = "ready_for_review"
SEGMENT_STATUS_APPROVED = "approved"
SEGMENT_STATUS_EDITED = "edited"
SEGMENT_STATUS_REJECTED = "rejected"
SEGMENT_STATUS_EXPORTED = "exported"
SEGMENT_STATUS_FAILED = "failed"

REVIEWABLE_SEGMENT_STATUSES = {
    SEGMENT_STATUS_PENDING,
    SEGMENT_STATUS_PARSED,
    SEGMENT_STATUS_EXTRACTED,
    SEGMENT_STATUS_VALIDATED,
    SEGMENT_STATUS_NEEDS_REVIEW,
    SEGMENT_STATUS_READY_FOR_REVIEW,
    SEGMENT_STATUS_APPROVED,
    SEGMENT_STATUS_EDITED,
    SEGMENT_STATUS_REJECTED,
    SEGMENT_STATUS_EXPORTED,
    SEGMENT_STATUS_FAILED,
}

EXPORT_READY_SEGMENT_STATUSES = {
    SEGMENT_STATUS_APPROVED,
    SEGMENT_STATUS_EDITED,
    SEGMENT_STATUS_REJECTED,
}

VALIDATION_ISSUE_STATUS_OPEN = "open"
VALIDATION_ISSUE_STATUS_ACKNOWLEDGED = "acknowledged"
VALIDATION_ISSUE_STATUS_RESOLVED = "resolved"

VALIDATION_ISSUE_REVIEW_STATUSES = {
    VALIDATION_ISSUE_STATUS_OPEN,
    VALIDATION_ISSUE_STATUS_ACKNOWLEDGED,
    VALIDATION_ISSUE_STATUS_RESOLVED,
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def serialize_model(model: Any) -> dict[str, Any]:
    if not is_dataclass(model):
        raise TypeError("serialize_model expects a dataclass instance")

    return {field.name: _serialize_value(getattr(model, field.name)) for field in fields(model)}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return serialize_model(value)
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    return value


@dataclass(frozen=True)
class SerializableModel:
    def to_dict(self) -> dict[str, Any]:
        return serialize_model(self)


@dataclass(frozen=True)
class Document(SerializableModel):
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


@dataclass(frozen=True)
class DocumentQueueJob(SerializableModel):
    id: str
    document_id: str
    status: str
    requested_by: str
    provider_name: str
    model: str | None
    worker_id: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class ParsedPage(SerializableModel):
    id: str
    document_id: str
    page_number: int
    text: str
    blocks_json: JsonDict
    tables_json: JsonDict
    language: str | None
    parser_sources: tuple[str, ...]
    has_text: bool
    created_at: datetime


@dataclass(frozen=True)
class PageCandidate(SerializableModel):
    id: str
    document_id: str
    page_number: int
    relevance_score: float
    matched_signals_json: JsonDict
    reason: str


@dataclass(frozen=True)
class SegmentRow(SerializableModel):
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


@dataclass(frozen=True)
class SegmentEvidence(SerializableModel):
    id: str
    segment_id: str
    document_id: str
    page_number: int
    snippet_text: str
    bbox_json: JsonDict | None
    parser_source: str
    evidence_kind: str
    evidence_original: str | None = None
    evidence_translation: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class ValidationIssue(SerializableModel):
    id: str
    document_id: str
    segment_id: str | None
    severity: str
    issue_type: str
    message: str
    created_at: datetime


@dataclass(frozen=True)
class ValidationIssueReview(SerializableModel):
    issue_id: str
    document_id: str
    status: str
    reviewer: str
    note: str | None
    updated_at: datetime


@dataclass(frozen=True)
class NaceCandidate(SerializableModel):
    id: str
    segment_id: str
    nace_code: str
    nace_label: str
    nace_level: int
    rank: int
    match_score: float
    rationale: str | None


@dataclass(frozen=True)
class NaceSelection(SerializableModel):
    segment_id: str
    nace_code: str
    nace_label: str
    nace_level: int
    match_score: float | None
    rationale: str | None
    source: str
    reviewer: str | None
    updated_at: datetime


@dataclass(frozen=True)
class EsgFactor(SerializableModel):
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


@dataclass(frozen=True)
class SegmentScore(SerializableModel):
    id: str
    segment_id: str
    base_score: float
    adjustment_score: float
    final_score: float
    weight_share: float | None
    rationale: str | None


@dataclass(frozen=True)
class CompanyScore(SerializableModel):
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


@dataclass(frozen=True)
class ReviewEvent(SerializableModel):
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


@dataclass(frozen=True)
class ExportRecord(SerializableModel):
    id: str
    document_id: str
    format: str
    path: str
    created_at: datetime
