from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from fitch_extractor.extraction.schemas import ExtractedRevenueRow
from fitch_extractor.models import ParsedPage


def align_rows_to_preferred_metric(
    rows: list[ExtractedRevenueRow],
    page: ParsedPage,
) -> list[ExtractedRevenueRow]:
    if not rows:
        return []

    preferred_metric = _preferred_metric(rows[0].metric_basis)
    if preferred_metric is None:
        return rows

    lines = _clean_lines(page.text)
    metric_index = _find_metric_index(lines, preferred_metric)
    if metric_index is None:
        return rows

    value_tokens = _metric_values(lines, metric_index, row_count=len(rows))
    if len(value_tokens) < len(rows):
        return rows

    aligned_rows: list[ExtractedRevenueRow] = []
    for row, raw_value in zip(rows, value_tokens):
        aligned_rows.append(
            row.model_copy(
                update={
                    "revenue_raw": raw_value,
                    "revenue_value": _parse_decimal(raw_value),
                    "metric_basis": preferred_metric,
                    "evidence_text": f"{preferred_metric} | {row.segment_name} | {raw_value}",
                }
            )
        )
    return aligned_rows


def _preferred_metric(metric_basis: str | None) -> str | None:
    metric = (metric_basis or "").casefold()
    if metric in {"external revenue", "external income"}:
        return "Revenue" if "revenue" in metric else "Total income"
    return None


def _clean_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _find_metric_index(lines: list[str], metric: str) -> int | None:
    normalized_metric = _normalize_label(metric)
    for index, line in enumerate(lines):
        if _normalize_label(line) == normalized_metric:
            return index
    return None


def _metric_values(lines: list[str], metric_index: int, *, row_count: int) -> list[str]:
    window = lines[metric_index + 1 : metric_index + 1 + row_count * 4]
    raw_values: list[str] = []
    for line in window:
        raw_values.extend(_number_tokens(line))
        if len(raw_values) >= row_count * 2:
            break

    stride = 2 if _has_paired_year_headers(lines[:metric_index]) and len(raw_values) >= row_count * 2 else 1
    return raw_values[::stride][:row_count]


def _has_paired_year_headers(lines: list[str]) -> bool:
    years: list[str] = []
    for line in lines[-40:]:
        years.extend(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", line))
    return len(set(years)) >= 2


def _number_tokens(line: str) -> list[str]:
    return [
        token.strip()
        for token in re.findall(r"\(?-?\d[\d,]*(?:\.\d+)?\)?|-", line)
        if token.strip()
    ]


def _parse_decimal(raw_value: str) -> Decimal | None:
    cleaned = raw_value.strip().replace(",", "")
    if cleaned in {"", "-"}:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()
