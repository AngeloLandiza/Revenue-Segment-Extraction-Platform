from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from revenue_segment_extractor.enrichment import ROW_TYPE_BUSINESS_SEGMENT, classify_row_type
from revenue_segment_extractor.extraction.normalization import NormalizedRevenueRow
from revenue_segment_extractor.models import ParsedPage


VALID_METRIC_TERMS = (
    "revenue",
    "revenues",
    "segment revenue",
    "segment revenues",
    "external revenue",
    "net revenue",
    "net revenues",
    "net sales",
    "sales",
    "turnover",
    "external income",
    "total income",
    "total operating income",
    "net interest income",
    "net commission income",
    "revenue-equivalent",
)

INVALID_METRIC_TERMS = (
    "asset",
    "assets",
    "liabilities",
    "expense",
    "expenses",
    "cost of sales",
    "costs",
    "loss",
    "losses",
    "gross profit",
    "profit",
    "ebit",
    "ebitda",
    "tax",
    "income tax",
    "finance income",
    "finance costs",
    "cash flow",
)

CONSOLIDATED_LINE_ITEM_LABELS = {
    "cost of sales",
    "costs of sales",
    "gross profit",
    "finance income",
    "finance cost",
    "finance costs",
    "income tax",
    "tax",
    "tax expense",
    "expenses",
    "operating expenses",
    "profit",
    "profit before tax",
    "net profit",
    "loss",
    "losses",
    "ebit",
    "ebitda",
    "assets",
    "total assets",
}

TOTAL_ROW_TERMS = {
    "total",
    "total revenue",
    "total revenues",
    "total net sales",
    "reported",
    "consolidated",
    "total consolidated",
    "group total",
}

RECONCILIATION_ONLY_TERMS = {
    "reclassification",
    "reported",
}

REVENUE_RECONCILIATION_ROW_TERMS = {
    "hedging gain",
    "hedging gains",
    "hedging loss",
    "hedging losses",
    "hedging gains losses",
    "hedging gain loss",
    "reconciling item",
    "reconciling items",
}


class ValidationDocument(Protocol):
    reported_total: Decimal | None
    currency: str | None
    scale: str | None
    fiscal_period: str | None


@dataclass(frozen=True)
class ValidationConfig:
    absolute_tolerance: Decimal = Decimal("1")
    relative_tolerance: Decimal = Decimal("0.005")
    allow_non_revenue_metrics: bool = False


@dataclass(frozen=True)
class DeterministicValidationIssue:
    row_index: int | None
    severity: str
    issue_type: str
    message: str
    blocking: bool = False


@dataclass(frozen=True)
class ReconciliationResult:
    status: str
    target_total: Decimal | None
    segment_sum: Decimal | None
    difference: Decimal | None
    message: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    issues: tuple[DeterministicValidationIssue, ...]
    reconciliation: ReconciliationResult

    @property
    def blocking_row_indexes(self) -> frozenset[int]:
        return frozenset(
            issue.row_index
            for issue in self.issues
            if issue.row_index is not None and issue.blocking
        )

    @property
    def has_uncertain_issues(self) -> bool:
        return any(issue.severity in {"warning", "error"} for issue in self.issues)

    def issues_for_row(self, row_index: int) -> tuple[DeterministicValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.row_index == row_index)


def validate_normalized_rows(
    *,
    document: ValidationDocument,
    rows: list[NormalizedRevenueRow],
    parsed_pages: list[ParsedPage],
    config: ValidationConfig | None = None,
) -> ValidationResult:
    active_config = config or ValidationConfig()
    issues: list[DeterministicValidationIssue] = []

    for row_index, row in enumerate(rows):
        issues.extend(_normalization_issues(row_index, row))
        issues.extend(_evidence_issues(row_index, row))
        issues.extend(_currency_issues(row_index, row, document.currency))
        issues.extend(_scale_issues(row_index, row, document.scale))
        issues.extend(_period_issues(row_index, row, document.fiscal_period))
        issues.extend(_metric_issues(row_index, row, active_config))
        issues.extend(_consolidated_line_item_issues(row_index, row))
        issues.extend(_row_type_issues(row_index, row))

    issues.extend(_document_currency_issues(rows))
    issues.extend(_document_scale_issues(rows))
    issues.extend(_document_period_issues(rows))
    issues.extend(_duplicate_segment_issues(rows))

    reconciliation = reconcile_totals(document=document, rows=rows, config=active_config)
    if reconciliation.status == "failed":
        issues.append(
            DeterministicValidationIssue(
                row_index=None,
                severity="warning",
                issue_type="total_reconciliation_mismatch",
                message=reconciliation.message or "Segment sum does not match reported total.",
            )
        )

    issues.extend(_declared_segment_coverage_issues(rows, parsed_pages))
    return ValidationResult(issues=tuple(issues), reconciliation=reconciliation)


def reconcile_totals(
    *,
    document: ValidationDocument,
    rows: list[NormalizedRevenueRow],
    config: ValidationConfig | None = None,
) -> ReconciliationResult:
    active_config = config or ValidationConfig()
    explicit_total = _explicit_total_value(rows)
    target_total = explicit_total or document.reported_total
    if target_total is None:
        return ReconciliationResult(
            status="not_available",
            target_total=None,
            segment_sum=None,
            difference=None,
        )

    values = [
        row.normalized_value
        for row in rows
        if row.normalized_value is not None
        and classify_row_type(row.segment_name, normalized_value=row.normalized_value)
        == ROW_TYPE_BUSINESS_SEGMENT
        and not (explicit_total is not None and is_reconciliation_only_row(row.segment_name))
    ]
    if not values:
        return ReconciliationResult(
            status="not_available",
            target_total=target_total,
            segment_sum=None,
            difference=None,
        )

    segment_sum = sum(values, Decimal("0"))
    difference = segment_sum - target_total
    tolerance = max(
        active_config.absolute_tolerance,
        abs(target_total) * active_config.relative_tolerance,
    )
    if abs(difference) <= tolerance:
        return ReconciliationResult(
            status="matched",
            target_total=target_total,
            segment_sum=segment_sum,
            difference=difference,
        )
    return ReconciliationResult(
        status="failed",
        target_total=target_total,
        segment_sum=segment_sum,
        difference=difference,
        message=(
            f"Segment sum {segment_sum} differs from reported total {target_total} "
            f"by {difference}; tolerance is {tolerance}."
        ),
    )


def is_total_row(segment_name: str) -> bool:
    normalized = _normalize_text(segment_name)
    if normalized in TOTAL_ROW_TERMS:
        return True
    return normalized.startswith("total ") or normalized.endswith(" total")


def is_reconciliation_only_row(segment_name: str) -> bool:
    normalized = _normalize_text(segment_name)
    return any(term in normalized for term in RECONCILIATION_ONLY_TERMS)


def _is_revenue_reconciliation_row(segment_name: str) -> bool:
    normalized = _normalize_text(segment_name)
    return any(term in normalized for term in REVENUE_RECONCILIATION_ROW_TERMS)


def _row_type_issues(
    row_index: int,
    row: NormalizedRevenueRow,
) -> list[DeterministicValidationIssue]:
    row_type = classify_row_type(row.segment_name, normalized_value=row.normalized_value)
    if (
        row.normalized_value is not None
        and row.normalized_value < 0
        and row_type == ROW_TYPE_BUSINESS_SEGMENT
    ):
        return [
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="negative_business_segment_value",
                message=(
                    f"Negative revenue value for {row.segment_name} is only expected "
                    "for eliminations or reconciliation adjustments."
                ),
            )
        ]
    return []


def _normalization_issues(
    row_index: int,
    row: NormalizedRevenueRow,
) -> list[DeterministicValidationIssue]:
    return [
        DeterministicValidationIssue(
            row_index=row_index,
            severity="warning",
            issue_type="normalization_warning",
            message=f"{warning.field}: {warning.message}",
        )
        for warning in row.warnings
    ]


def _evidence_issues(
    row_index: int,
    row: NormalizedRevenueRow,
) -> list[DeterministicValidationIssue]:
    issues: list[DeterministicValidationIssue] = []
    if row.page_number is None:
        issues.append(
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="missing_page_reference",
                message=f"Row for {row.segment_name} has no usable page reference.",
            )
        )
    if not row.evidence_text.strip():
        issues.append(
            DeterministicValidationIssue(
                row_index=row_index,
                severity="error",
                issue_type="missing_evidence_text",
                message=f"Row for {row.segment_name} has no evidence text.",
                blocking=True,
            )
        )
    return issues


def _currency_issues(
    row_index: int,
    row: NormalizedRevenueRow,
    expected_currency: str | None,
) -> list[DeterministicValidationIssue]:
    if row.currency is None:
        return [
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="missing_currency",
                message=f"Row for {row.segment_name} has no normalized currency.",
            )
        ]
    if expected_currency and row.currency != expected_currency:
        return [
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="currency_mismatch",
                message=(
                    f"Row for {row.segment_name} uses {row.currency}; "
                    f"document context uses {expected_currency}."
                ),
            )
        ]
    return []


def _scale_issues(
    row_index: int,
    row: NormalizedRevenueRow,
    expected_scale: str | None,
) -> list[DeterministicValidationIssue]:
    if row.scale is None:
        return [
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="missing_scale",
                message=f"Row for {row.segment_name} has no normalized scale.",
            )
        ]
    if expected_scale and _normalize_scale(expected_scale) != _normalize_scale(row.scale):
        return [
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="scale_mismatch",
                message=(
                    f"Row for {row.segment_name} uses {row.scale}; "
                    f"document context uses {expected_scale}."
                ),
            )
        ]
    return []


def _period_issues(
    row_index: int,
    row: NormalizedRevenueRow,
    expected_period: str | None,
) -> list[DeterministicValidationIssue]:
    if row.period_label is None:
        return [
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="missing_time_period",
                message=f"Row for {row.segment_name} has no normalized fiscal period.",
            )
        ]
    expected_year = _latest_year(expected_period or "")
    row_year = _latest_year(row.period_label)
    if expected_year and row_year and expected_year != row_year:
        return [
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="time_period_mismatch",
                message=(
                    f"Row for {row.segment_name} uses {row.period_label}; "
                    f"document context uses {expected_period}."
                ),
            )
        ]
    return []


def _metric_issues(
    row_index: int,
    row: NormalizedRevenueRow,
    config: ValidationConfig,
) -> list[DeterministicValidationIssue]:
    if config.allow_non_revenue_metrics:
        return []

    metric_text = _normalize_text(
        " ".join(
            value
            for value in [row.metric_basis or "", row.evidence_text, row.section_ref or ""]
            if value
        )
    )
    metric_basis_text = _normalize_text(row.metric_basis or "")
    has_valid_metric = _contains_valid_metric(metric_text)
    if metric_basis_text and _contains_valid_metric(metric_basis_text):
        return []
    if has_valid_metric and _is_revenue_reconciliation_row(row.segment_name):
        return []
    if _contains_invalid_metric(metric_text):
        return [
            DeterministicValidationIssue(
                row_index=row_index,
                severity="error",
                issue_type="invalid_metric_basis",
                message=(
                    f"Row for {row.segment_name} appears to use an expense, loss, asset, "
                    "profit, EBIT, EBITDA, tax, or other non-revenue metric."
                ),
                blocking=True,
            )
        ]
    if not has_valid_metric:
        return [
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="unconfirmed_metric_basis",
                message=f"Row for {row.segment_name} does not show a clear revenue metric basis.",
            )
        ]
    return []


def _consolidated_line_item_issues(
    row_index: int,
    row: NormalizedRevenueRow,
) -> list[DeterministicValidationIssue]:
    if _is_revenue_reconciliation_row(row.segment_name):
        return []
    normalized_segment = _normalize_text(row.segment_name)
    if normalized_segment not in CONSOLIDATED_LINE_ITEM_LABELS:
        return []
    return [
        DeterministicValidationIssue(
            row_index=row_index,
            severity="error",
            issue_type="consolidated_income_statement_line_item",
            message=(
                f"Row label {row.segment_name!r} is a consolidated income statement "
                "line item, not a revenue segment."
            ),
            blocking=True,
        )
    ]


def _document_currency_issues(rows: list[NormalizedRevenueRow]) -> list[DeterministicValidationIssue]:
    currencies = sorted({row.currency for row in rows if row.currency})
    if len(currencies) <= 1:
        return []
    return [
        DeterministicValidationIssue(
            row_index=None,
            severity="warning",
            issue_type="document_currency_inconsistent",
            message=f"Rows contain multiple currencies: {', '.join(currencies)}.",
        )
    ]


def _document_scale_issues(rows: list[NormalizedRevenueRow]) -> list[DeterministicValidationIssue]:
    scales = sorted({_normalize_scale(row.scale) for row in rows if row.scale})
    if len(scales) <= 1:
        return []
    return [
        DeterministicValidationIssue(
            row_index=None,
            severity="warning",
            issue_type="document_scale_inconsistent",
            message=f"Rows contain multiple scales: {', '.join(scales)}.",
        )
    ]


def _document_period_issues(rows: list[NormalizedRevenueRow]) -> list[DeterministicValidationIssue]:
    years = sorted(
        year
        for year in {_latest_year(row.period_label or "") for row in rows if row.period_label}
        if year is not None
    )
    if len(years) <= 1:
        return []
    return [
        DeterministicValidationIssue(
            row_index=None,
            severity="warning",
            issue_type="document_time_period_inconsistent",
            message=f"Rows contain multiple fiscal years: {', '.join(str(year) for year in years)}.",
        )
    ]


def _duplicate_segment_issues(rows: list[NormalizedRevenueRow]) -> list[DeterministicValidationIssue]:
    issues: list[DeterministicValidationIssue] = []
    seen: dict[tuple[str, str | None], int] = {}
    for row_index, row in enumerate(rows):
        if is_total_row(row.segment_name):
            continue
        key = (_normalize_text(row.segment_name), row.period_label)
        previous_index = seen.get(key)
        if previous_index is None:
            seen[key] = row_index
            continue
        issues.append(
            DeterministicValidationIssue(
                row_index=row_index,
                severity="warning",
                issue_type="duplicate_segment_candidate",
                message=(
                    f"Segment {row.segment_name!r} also appears in row {previous_index + 1} "
                    "for the same period."
                ),
            )
        )
    return issues


def _declared_segment_coverage_issues(
    rows: list[NormalizedRevenueRow],
    parsed_pages: list[ParsedPage],
) -> list[DeterministicValidationIssue]:
    if not rows:
        return []
    page_numbers = {row.page_number for row in rows if row.page_number is not None}
    extracted = {_normalize_text(row.segment_name) for row in rows}
    declared_names: set[str] = set()
    for page in parsed_pages:
        if page.page_number not in page_numbers:
            continue
        declared_names.update(_declared_names_from_tables(page))

    missing = sorted(
        name
        for name in declared_names
        if _normalize_text(name) not in extracted
        and not is_total_row(name)
        and not _is_noise_label(name)
    )
    return [
        DeterministicValidationIssue(
            row_index=None,
            severity="warning",
            issue_type="potential_missing_segment",
            message=f"Potential declared segment was not extracted: {name}.",
        )
        for name in missing
    ]


def _declared_names_from_tables(page: ParsedPage) -> set[str]:
    tables = page.tables_json.get("tables", []) if isinstance(page.tables_json, dict) else []
    names: set[str] = set()
    for table in tables:
        rows = table.get("rows", []) if isinstance(table, dict) else []
        if not rows:
            continue
        header = [_normalize_text(str(cell)) for cell in rows[0]]
        first_header = header[0] if header else ""
        first_column_has_segment_header = any(
            term in first_header
            for term in ("segment", "division", "business", "geography", "region", "product")
        )
        if first_column_has_segment_header:
            for table_row in rows[1:]:
                if table_row:
                    names.add(str(table_row[0]).strip())
    return {name for name in names if name}


def _explicit_total_value(rows: list[NormalizedRevenueRow]) -> Decimal | None:
    for row in rows:
        if is_total_row(row.segment_name) and not is_reconciliation_only_row(row.segment_name):
            if row.normalized_value is not None:
                return row.normalized_value
    return None


def _contains_valid_metric(text: str) -> bool:
    return any(term in text for term in VALID_METRIC_TERMS)


def _contains_invalid_metric(text: str) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in INVALID_METRIC_TERMS)


def _is_noise_label(label: str) -> bool:
    normalized = _normalize_text(label)
    if not normalized or normalized in {"segment", "segments", "business", "division"}:
        return True
    if _contains_invalid_metric(normalized):
        return True
    if re.fullmatch(r"[\d,.\-()]+", normalized):
        return True
    if _latest_year(normalized):
        return True
    return False


def _normalize_scale(scale: str | None) -> str:
    normalized = _normalize_text(scale or "")
    if normalized in {"ones", "actual", "actuals", "unit", "units"}:
        return "actuals"
    return normalized


def _latest_year(text: str) -> int | None:
    years = [int(match) for match in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", text)]
    return max(years) if years else None


def _normalize_text(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text.casefold()).strip()
