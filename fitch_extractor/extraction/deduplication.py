from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from fitch_extractor.extraction.schemas import ExtractedRevenueRow


@dataclass(frozen=True)
class PreparedRevenueRow:
    source_row: ExtractedRevenueRow
    normalized_value: Decimal | None
    page_number: int | None
    fiscal_period: str | None


def deduplicate_rows(rows: list[PreparedRevenueRow]) -> tuple[list[PreparedRevenueRow], int]:
    kept: list[PreparedRevenueRow] = []
    duplicate_count = 0
    for row in rows:
        if any(_is_duplicate(row, existing) for existing in kept):
            duplicate_count += 1
            continue
        kept.append(row)
    return kept, duplicate_count


def is_duplicate_of_existing(
    row: PreparedRevenueRow,
    existing_segment_name: str,
    existing_normalized_value: Decimal | None,
    existing_period_label: str | None,
    existing_page_number: int | None,
    existing_evidence_text: str | None,
) -> bool:
    return _matches(
        row_segment_name=row.source_row.segment_name,
        row_normalized_value=row.normalized_value,
        row_period=row.fiscal_period,
        row_page_number=row.page_number,
        row_evidence=row.source_row.evidence_text,
        other_segment_name=existing_segment_name,
        other_normalized_value=existing_normalized_value,
        other_period=existing_period_label,
        other_page_number=existing_page_number,
        other_evidence=existing_evidence_text,
    )


def _is_duplicate(row: PreparedRevenueRow, other: PreparedRevenueRow) -> bool:
    return _matches(
        row_segment_name=row.source_row.segment_name,
        row_normalized_value=row.normalized_value,
        row_period=row.fiscal_period,
        row_page_number=row.page_number,
        row_evidence=row.source_row.evidence_text,
        other_segment_name=other.source_row.segment_name,
        other_normalized_value=other.normalized_value,
        other_period=other.fiscal_period,
        other_page_number=other.page_number,
        other_evidence=other.source_row.evidence_text,
    )


def _matches(
    *,
    row_segment_name: str,
    row_normalized_value: Decimal | None,
    row_period: str | None,
    row_page_number: int | None,
    row_evidence: str | None,
    other_segment_name: str,
    other_normalized_value: Decimal | None,
    other_period: str | None,
    other_page_number: int | None,
    other_evidence: str | None,
) -> bool:
    if _normalize_text(row_segment_name) != _normalize_text(other_segment_name):
        return False
    if row_normalized_value != other_normalized_value:
        return False
    if _normalize_text(row_period or "") != _normalize_text(other_period or ""):
        return False
    if row_page_number is None or other_page_number is None:
        return False
    if abs(row_page_number - other_page_number) > 1:
        return False
    return _normalize_text(row_evidence or "") == _normalize_text(other_evidence or "")


def _normalize_text(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()

