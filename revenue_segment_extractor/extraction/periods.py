from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Sequence

from revenue_segment_extractor.extraction.schemas import ExtractedRevenueRow


@dataclass(frozen=True)
class LatestPeriodRows:
    rows: list[ExtractedRevenueRow]
    latest_year: int | None
    skipped_count: int


def keep_latest_year_rows(rows: Sequence[ExtractedRevenueRow]) -> LatestPeriodRows:
    years_by_index = {
        index: year
        for index, row in enumerate(rows)
        for year in [_year_from_period_label(row.period_label)]
        if year is not None
    }
    distinct_years = set(years_by_index.values())
    if len(distinct_years) < 2:
        return LatestPeriodRows(rows=list(rows), latest_year=None, skipped_count=0)

    latest_year = max(distinct_years)
    latest_rows = [
        row
        for index, row in enumerate(rows)
        if years_by_index.get(index) in {None, latest_year}
    ]
    return LatestPeriodRows(
        rows=latest_rows,
        latest_year=latest_year,
        skipped_count=len(rows) - len(latest_rows),
    )


def _year_from_period_label(period_label: str | None) -> int | None:
    if not period_label:
        return None
    years = [
        int(match)
        for match in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", period_label)
    ]
    if not years:
        return None
    return max(years)
