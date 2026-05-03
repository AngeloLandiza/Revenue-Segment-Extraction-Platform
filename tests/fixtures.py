from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from revenue_segment_extractor.models import (
    DOCUMENT_STATUS_NEW,
    SEGMENT_STATUS_PENDING,
    Document,
    SegmentRow,
)


FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def build_document(
    *,
    id: str = "doc_fixture",
    company_name: str = "Example Demo Co.",
    document_name: str = "example-annual-report.pdf",
    source_path: str = "fixtures/example-annual-report.pdf",
    fiscal_period: str = "FY2025",
    status: str = DOCUMENT_STATUS_NEW,
    reported_total: Decimal | None = Decimal("125000000"),
    currency: str = "USD",
    scale: str = "ones",
    created_at: datetime = FIXED_TIME,
    updated_at: datetime = FIXED_TIME,
    analysis_notes: str | None = None,
) -> Document:
    return Document(
        id=id,
        company_name=company_name,
        document_name=document_name,
        source_path=source_path,
        fiscal_period=fiscal_period,
        status=status,
        reported_total=reported_total,
        currency=currency,
        scale=scale,
        created_at=created_at,
        updated_at=updated_at,
        analysis_notes=analysis_notes,
    )


def build_segment_row(
    *,
    id: str = "seg_fixture",
    document_id: str = "doc_fixture",
    segment_name: str = "Insurance",
    revenue_raw: str | None = "$42 million",
    revenue_value: Decimal | None = Decimal("42"),
    currency: str = "USD",
    scale: str = "millions",
    period_label: str = "FY2025",
    normalized_value: Decimal | None = Decimal("42000000"),
    page_ref: str = "p. 12",
    section_ref: str = "Revenue by segment",
    metric_basis: str = "revenue",
    confidence: float | None = 0.91,
    status: str = SEGMENT_STATUS_PENDING,
    extraction_method: str = "fixture",
    created_at: datetime = FIXED_TIME,
    updated_at: datetime = FIXED_TIME,
) -> SegmentRow:
    return SegmentRow(
        id=id,
        document_id=document_id,
        segment_name=segment_name,
        revenue_raw=revenue_raw,
        revenue_value=revenue_value,
        currency=currency,
        scale=scale,
        period_label=period_label,
        normalized_value=normalized_value,
        page_ref=page_ref,
        section_ref=section_ref,
        metric_basis=metric_basis,
        confidence=confidence,
        status=status,
        extraction_method=extraction_method,
        created_at=created_at,
        updated_at=updated_at,
    )
