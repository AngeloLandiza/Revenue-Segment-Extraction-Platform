from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from revenue_segment_extractor.extraction.deduplication import PreparedRevenueRow


@dataclass(frozen=True)
class PrimarySelectionResult:
    rows: list[PreparedRevenueRow]
    skipped_count: int


def keep_primary_table_rows(rows: list[PreparedRevenueRow]) -> PrimarySelectionResult:
    groups = _page_groups(rows)
    best_group = _best_primary_group(groups)
    if best_group is None:
        return PrimarySelectionResult(rows=rows, skipped_count=0)

    kept: list[PreparedRevenueRow] = []
    skipped_count = 0
    best_page = best_group.page_number
    best_period = best_group.fiscal_period
    for row in rows:
        if row.page_number == best_page or row.fiscal_period != best_period:
            kept.append(row)
            continue
        skipped_count += 1
    return PrimarySelectionResult(rows=kept, skipped_count=skipped_count)


@dataclass(frozen=True)
class _PageGroup:
    page_number: int
    fiscal_period: str | None
    rows: list[PreparedRevenueRow]

    @property
    def has_total(self) -> bool:
        return any("total" in _normalize(row.source_row.segment_name) for row in self.rows)

    @property
    def score(self) -> tuple[Decimal, int]:
        row_score = Decimal(len(self.rows)) / Decimal("2")
        total_score = Decimal("5") if self.has_total else Decimal("0")
        metric_score = max(_metric_priority(row.source_row.metric_basis) for row in self.rows)
        return (row_score + total_score + metric_score, -self.page_number)


def _page_groups(rows: list[PreparedRevenueRow]) -> list[_PageGroup]:
    grouped: dict[tuple[int, str | None], list[PreparedRevenueRow]] = {}
    for row in rows:
        if row.page_number is None:
            continue
        grouped.setdefault((row.page_number, row.fiscal_period), []).append(row)
    return [
        _PageGroup(page_number=page_number, fiscal_period=period, rows=group_rows)
        for (page_number, period), group_rows in grouped.items()
    ]


def _best_primary_group(groups: list[_PageGroup]) -> _PageGroup | None:
    primary_groups = [
        group for group in groups if len(group.rows) >= 5 and group.has_total
    ]
    if len(primary_groups) < 2:
        return None
    return max(primary_groups, key=lambda group: group.score)


def _metric_priority(metric_basis: str | None) -> Decimal:
    metric = _normalize(metric_basis or "")
    if metric.startswith("external customers"):
        return Decimal("1")
    if metric in {"total income", "total operating income", "total revenues"}:
        return Decimal("10")
    if metric in {"revenue", "revenues", "net revenues"}:
        return Decimal("9")
    if "revenue" in metric or "income" in metric:
        return Decimal("5")
    return Decimal("0")


def _normalize(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text.casefold()).strip()
