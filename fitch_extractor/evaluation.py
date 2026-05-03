from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from fitch_extractor.extraction.normalization import SCALE_MULTIPLIERS


GOLD_COLUMNS = [
    "document_name",
    "company_name",
    "fiscal_period",
    "segment_name",
    "revenue_value",
    "currency",
    "scale",
    "page_ref",
    "nace_code",
    "notes",
]

RESULT_COLUMNS = [
    "document_name",
    "company_name",
    "fiscal_period",
    "gold_segment_name",
    "predicted_segment_name",
    "gold_revenue_value",
    "predicted_revenue_value",
    "gold_currency",
    "predicted_currency",
    "gold_scale",
    "predicted_scale",
    "gold_page_ref",
    "predicted_page_ref",
    "match_status",
    "failure_type",
    "segment_similarity",
    "value_exact",
    "period_match",
    "page_ref_match",
    "review_status",
    "nace_code",
    "notes",
]

FAILURE_TYPES = {
    "missing_segment",
    "false_positive_line_item",
    "wrong_value",
    "wrong_unit_scale",
    "wrong_period",
    "duplicate_segment",
    "total_handling_error",
    "table_parsing_error",
    "non_english_issue",
    "scanned_ocr_issue",
    "nace_ambiguity",
    "esg_over_linking",
}


@dataclass(frozen=True)
class MatchThresholds:
    segment_similarity: float = 0.68
    match_score: float = 0.62
    value_relative_tolerance: Decimal = Decimal("0.005")
    value_absolute_tolerance: Decimal = Decimal("1")


@dataclass(frozen=True)
class GoldRow:
    document_name: str
    company_name: str
    fiscal_period: str
    segment_name: str
    revenue_value: Decimal
    currency: str
    scale: str
    page_ref: str | None = None
    nace_code: str | None = None
    notes: str | None = None

    @property
    def normalized_value(self) -> Decimal:
        return scaled_value(self.revenue_value, self.scale)


@dataclass(frozen=True)
class PredictedRow:
    document_name: str
    company_name: str
    fiscal_period: str
    segment_name: str
    revenue_value: Decimal | None
    currency: str | None
    scale: str | None
    page_ref: str | None = None
    review_status: str | None = None
    normalized_value: Decimal | None = None
    nace_code: str | None = None
    notes: str | None = None

    @property
    def comparable_value(self) -> Decimal | None:
        if self.revenue_value is not None and self.scale:
            return scaled_value(self.revenue_value, self.scale)
        return self.normalized_value


@dataclass(frozen=True)
class DocumentEvaluationContext:
    validation_issue_count: int = 0
    reconciliation_passed: bool | None = None
    time_seconds: float | None = None


@dataclass(frozen=True)
class EvaluationInput:
    rows: tuple[PredictedRow, ...]
    document_contexts: dict[str, DocumentEvaluationContext]


@dataclass(frozen=True)
class RowMatch:
    gold: GoldRow | None
    predicted: PredictedRow | None
    score: float
    segment_similarity: float
    value_exact: bool
    period_match: bool
    page_ref_match: bool | None
    status: str
    failure_type: str | None


@dataclass(frozen=True)
class EvaluationMetrics:
    gold_rows: int
    predicted_rows: int
    matched_rows: int
    correct_rows: int
    precision: float
    recall: float
    f1: float
    exact_value_accuracy: float
    page_reference_accuracy: float
    reconciliation_pass_rate: float
    reviewer_edit_rate: float
    average_validation_issues_per_document: float
    average_time_seconds_per_document: float | None


@dataclass(frozen=True)
class EvaluationReport:
    matches: tuple[RowMatch, ...]
    metrics: EvaluationMetrics
    failure_counts: Counter[str]
    document_contexts: dict[str, DocumentEvaluationContext]


def load_gold_files(paths: list[Path]) -> list[GoldRow]:
    rows: list[GoldRow] = []
    for path in paths:
        if path.suffix.casefold() == ".json":
            rows.extend(_load_gold_json(path))
        else:
            rows.extend(_load_gold_csv(path))
    return rows


def load_prediction_files(paths: list[Path]) -> EvaluationInput:
    rows: list[PredictedRow] = []
    contexts: dict[str, DocumentEvaluationContext] = {}
    for path in _prediction_leaf_paths(paths):
        if path.name == "audit_export.json" or path.suffix.casefold() == ".json":
            loaded = _load_prediction_json(path)
            rows.extend(loaded.rows)
            contexts.update(loaded.document_contexts)
        elif path.suffix.casefold() == ".csv":
            rows.extend(_load_prediction_csv(path))
    return EvaluationInput(rows=tuple(rows), document_contexts=contexts)


def evaluate_rows(
    gold_rows: list[GoldRow],
    predicted_rows: list[PredictedRow],
    *,
    document_contexts: dict[str, DocumentEvaluationContext] | None = None,
    thresholds: MatchThresholds = MatchThresholds(),
) -> EvaluationReport:
    contexts = document_contexts or {}
    matches: list[RowMatch] = []
    by_document: dict[str, tuple[list[GoldRow], list[PredictedRow]]] = {}
    document_names = {
        normalize_document_name(row.document_name) for row in gold_rows
    } | {normalize_document_name(row.document_name) for row in predicted_rows}
    for document_name in sorted(document_names):
        doc_gold = [
            row for row in gold_rows if normalize_document_name(row.document_name) == document_name
        ]
        doc_predicted = [
            row
            for row in predicted_rows
            if normalize_document_name(row.document_name) == document_name
        ]
        by_document[document_name] = (doc_gold, doc_predicted)

    for _, (doc_gold, doc_predicted) in by_document.items():
        matches.extend(_match_document_rows(doc_gold, doc_predicted, thresholds))

    failure_counts = Counter(
        match.failure_type for match in matches if match.failure_type is not None
    )
    metrics = compute_metrics(matches, contexts)
    return EvaluationReport(
        matches=tuple(matches),
        metrics=metrics,
        failure_counts=failure_counts,
        document_contexts=contexts,
    )


def compute_metrics(
    matches: list[RowMatch] | tuple[RowMatch, ...],
    document_contexts: dict[str, DocumentEvaluationContext] | None = None,
) -> EvaluationMetrics:
    gold_rows = sum(1 for match in matches if match.gold is not None)
    predicted_rows = sum(1 for match in matches if match.predicted is not None)
    paired = [match for match in matches if match.gold is not None and match.predicted is not None]
    correct = [match for match in paired if _is_correct_match(match)]
    value_checked = [match for match in paired if match.predicted.comparable_value is not None]
    page_checked = [match for match in paired if match.page_ref_match is not None]
    edited_rows = [
        match
        for match in matches
        if match.predicted is not None and (match.predicted.review_status or "").casefold() == "edited"
    ]

    precision = _rate(len(correct), predicted_rows)
    recall = _rate(len(correct), gold_rows)
    contexts = document_contexts or {}
    reconciliation_values = [
        context.reconciliation_passed
        for context in contexts.values()
        if context.reconciliation_passed is not None
    ]
    validation_issue_counts = [context.validation_issue_count for context in contexts.values()]
    time_values = [
        context.time_seconds for context in contexts.values() if context.time_seconds is not None
    ]
    return EvaluationMetrics(
        gold_rows=gold_rows,
        predicted_rows=predicted_rows,
        matched_rows=len(paired),
        correct_rows=len(correct),
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        exact_value_accuracy=_rate(
            sum(1 for match in value_checked if match.value_exact),
            len(value_checked),
        ),
        page_reference_accuracy=_rate(
            sum(1 for match in page_checked if match.page_ref_match is True),
            len(page_checked),
        ),
        reconciliation_pass_rate=_rate(
            sum(1 for passed in reconciliation_values if passed),
            len(reconciliation_values),
        ),
        reviewer_edit_rate=_rate(len(edited_rows), predicted_rows),
        average_validation_issues_per_document=(
            sum(validation_issue_counts) / len(validation_issue_counts)
            if validation_issue_counts
            else 0.0
        ),
        average_time_seconds_per_document=(
            sum(time_values) / len(time_values) if time_values else None
        ),
    )


def classify_failure(match: RowMatch, duplicate_predicted: bool = False) -> str | None:
    gold = match.gold
    predicted = match.predicted
    notes = " ".join(
        value or "" for value in [gold.notes if gold else None, predicted.notes if predicted else None]
    ).casefold()
    text = " ".join(
        value or ""
        for value in [
            gold.segment_name if gold else None,
            predicted.segment_name if predicted else None,
            notes,
        ]
    ).casefold()

    tagged = _failure_from_notes(notes)
    if tagged is not None:
        return tagged
    if duplicate_predicted:
        return "duplicate_segment"
    if "total" in text or "elimination" in text or "reconciliation" in text:
        return "total_handling_error"
    if match.status == "missing":
        return "missing_segment"
    if match.status == "extra":
        return "false_positive_line_item"
    if not match.period_match:
        return "wrong_period"
    if gold and predicted and _currency_or_scale_mismatch(gold, predicted):
        return "wrong_unit_scale"
    if not match.value_exact:
        return "wrong_value"
    return None


def write_reports(report: EvaluationReport, reports_dir: Path) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_results_csv(report.matches, reports_dir / "evaluation_results.csv")
    (reports_dir / "evaluation_summary.md").write_text(
        render_summary_markdown(report),
        encoding="utf-8",
    )
    (reports_dir / "failure_analysis.md").write_text(
        render_failure_analysis_markdown(report),
        encoding="utf-8",
    )


def render_summary_markdown(report: EvaluationReport) -> str:
    metrics = report.metrics
    lines = [
        "# Evaluation Summary",
        "",
        "This report is generated from the supplied gold files and prediction exports. It does not claim performance beyond those inputs.",
        "",
        "## Metrics",
        "",
        f"- Gold rows: {metrics.gold_rows}",
        f"- Predicted rows: {metrics.predicted_rows}",
        f"- Matched rows: {metrics.matched_rows}",
        f"- Correct rows: {metrics.correct_rows}",
        f"- Precision: {_format_rate(metrics.precision)}",
        f"- Recall: {_format_rate(metrics.recall)}",
        f"- F1: {_format_rate(metrics.f1)}",
        f"- Exact value accuracy: {_format_rate(metrics.exact_value_accuracy)}",
        f"- Page reference accuracy: {_format_rate(metrics.page_reference_accuracy)}",
        f"- Reconciliation pass rate: {_format_rate(metrics.reconciliation_pass_rate)}",
        f"- Reviewer edit rate: {_format_rate(metrics.reviewer_edit_rate)}",
        (
            "- Average validation issues per document: "
            f"{metrics.average_validation_issues_per_document:.2f}"
        ),
        (
            "- Average time per document: "
            f"{metrics.average_time_seconds_per_document:.2f} seconds"
            if metrics.average_time_seconds_per_document is not None
            else "- Average time per document: unavailable"
        ),
        "",
        "## Inputs Needed For Rich Metrics",
        "",
        "Use `audit_export.json` predictions when available. CSV predictions support row matching and reviewer edit rate, but JSON audit exports also expose validation issues, reconciliation status, review events, and timestamps.",
        "",
    ]
    return "\n".join(lines)


def render_failure_analysis_markdown(report: EvaluationReport) -> str:
    lines = [
        "# Failure Analysis",
        "",
        "## Failure Counts",
        "",
    ]
    if report.failure_counts:
        for failure_type, count in sorted(report.failure_counts.items()):
            lines.append(f"- {failure_type}: {count}")
    else:
        lines.append("- No classified failures.")

    lines.extend(["", "## Examples", ""])
    examples_by_type: dict[str, list[RowMatch]] = defaultdict(list)
    for match in report.matches:
        if match.failure_type and len(examples_by_type[match.failure_type]) < 5:
            examples_by_type[match.failure_type].append(match)
    if not examples_by_type:
        lines.append("No failure examples were generated.")
    else:
        for failure_type, examples in sorted(examples_by_type.items()):
            lines.append(f"### {failure_type}")
            for match in examples:
                lines.append(f"- {_failure_example(match)}")
            lines.append("")
    return "\n".join(lines)


def normalize_segment_name(value: str) -> str:
    text = value.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def segment_similarity(left: str, right: str) -> float:
    normalized_left = normalize_segment_name(left)
    normalized_right = normalize_segment_name(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    base = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    left_tokens = {_singular_token(token) for token in normalized_left.split()}
    right_tokens = {_singular_token(token) for token in normalized_right.split()}
    overlap = left_tokens & right_tokens
    if overlap and (overlap == left_tokens or overlap == right_tokens):
        return max(base, 0.84)
    if overlap:
        return max(base, len(overlap) / max(len(left_tokens), len(right_tokens)))
    return base


def values_match(
    left: Decimal | None,
    right: Decimal | None,
    thresholds: MatchThresholds = MatchThresholds(),
) -> bool:
    if left is None or right is None:
        return False
    difference = abs(left - right)
    if difference <= thresholds.value_absolute_tolerance:
        return True
    denominator = max(abs(left), abs(right), Decimal("1"))
    return difference / denominator <= thresholds.value_relative_tolerance


def scaled_value(value: Decimal, scale: str | None) -> Decimal:
    scale_key = (scale or "ones").strip().casefold()
    multiplier = SCALE_MULTIPLIERS.get(scale_key, Decimal("1"))
    return value * multiplier


def normalize_document_name(value: str) -> str:
    return Path(value).name.casefold().strip()


def page_refs_match(left: str | None, right: str | None) -> bool | None:
    if not left or not right:
        return None
    left_page = _first_int(left)
    right_page = _first_int(right)
    if left_page is None or right_page is None:
        return None
    return left_page == right_page


def _match_document_rows(
    gold_rows: list[GoldRow],
    predicted_rows: list[PredictedRow],
    thresholds: MatchThresholds,
) -> list[RowMatch]:
    candidates: list[tuple[float, int, int, float, bool, bool, bool | None]] = []
    for gold_index, gold in enumerate(gold_rows):
        for predicted_index, predicted in enumerate(predicted_rows):
            similarity = segment_similarity(gold.segment_name, predicted.segment_name)
            if similarity < thresholds.segment_similarity:
                continue
            value_exact = values_match(
                gold.normalized_value,
                predicted.comparable_value,
                thresholds,
            )
            period_match = _periods_match(gold.fiscal_period, predicted.fiscal_period)
            page_match = page_refs_match(gold.page_ref, predicted.page_ref)
            score = _candidate_score(similarity, value_exact, period_match, page_match)
            if score >= thresholds.match_score:
                candidates.append(
                    (
                        score,
                        gold_index,
                        predicted_index,
                        similarity,
                        value_exact,
                        period_match,
                        page_match,
                    )
                )

    candidates.sort(reverse=True, key=lambda item: item[0])
    used_gold: set[int] = set()
    used_predicted: set[int] = set()
    matched: list[RowMatch] = []
    duplicate_keys = _duplicate_prediction_keys(predicted_rows)

    for score, gold_index, predicted_index, similarity, value_exact, period_match, page_match in candidates:
        if gold_index in used_gold or predicted_index in used_predicted:
            continue
        used_gold.add(gold_index)
        used_predicted.add(predicted_index)
        row_match = RowMatch(
            gold=gold_rows[gold_index],
            predicted=predicted_rows[predicted_index],
            score=score,
            segment_similarity=similarity,
            value_exact=value_exact,
            period_match=period_match,
            page_ref_match=page_match,
            status="matched",
            failure_type=None,
        )
        matched.append(
            _with_failure(
                row_match,
                duplicate_predicted=_prediction_key(predicted_rows[predicted_index]) in duplicate_keys,
            )
        )

    for gold_index, gold in enumerate(gold_rows):
        if gold_index in used_gold:
            continue
        row_match = RowMatch(
            gold=gold,
            predicted=None,
            score=0.0,
            segment_similarity=0.0,
            value_exact=False,
            period_match=False,
            page_ref_match=None,
            status="missing",
            failure_type=None,
        )
        matched.append(_with_failure(row_match))

    for predicted_index, predicted in enumerate(predicted_rows):
        if predicted_index in used_predicted:
            continue
        row_match = RowMatch(
            gold=None,
            predicted=predicted,
            score=0.0,
            segment_similarity=0.0,
            value_exact=False,
            period_match=False,
            page_ref_match=None,
            status="extra",
            failure_type=None,
        )
        matched.append(
            _with_failure(
                row_match,
                duplicate_predicted=_prediction_key(predicted) in duplicate_keys,
            )
        )
    return matched


def _load_gold_csv(path: Path) -> list[GoldRow]:
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        missing = set(GOLD_COLUMNS[:8]) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Gold CSV {path} is missing required columns: {sorted(missing)}")
        return [_gold_from_mapping(row, source=path) for row in reader if _has_value(row)]


def _load_gold_json(path: Path) -> list[GoldRow]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_rows = payload["rows"] if isinstance(payload, dict) and "rows" in payload else payload
    if not isinstance(raw_rows, list):
        raise ValueError(f"Gold JSON {path} must contain a list or an object with a rows list")
    return [_gold_from_mapping(row, source=path) for row in raw_rows if _has_value(row)]


def _gold_from_mapping(row: dict[str, Any], *, source: Path) -> GoldRow:
    return GoldRow(
        document_name=_required_text(row, "document_name", source),
        company_name=_required_text(row, "company_name", source),
        fiscal_period=_required_text(row, "fiscal_period", source),
        segment_name=_required_text(row, "segment_name", source),
        revenue_value=_decimal(row.get("revenue_value"), "revenue_value", source),
        currency=_required_text(row, "currency", source),
        scale=_required_text(row, "scale", source),
        page_ref=_optional_text(row.get("page_ref")),
        nace_code=_optional_text(row.get("nace_code")),
        notes=_optional_text(row.get("notes")),
    )


def _load_prediction_csv(path: Path) -> list[PredictedRow]:
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return [
            _prediction_from_mapping(row, source=path)
            for row in reader
            if _has_value(row) and _optional_text(row.get("segment_name"))
        ]


def _load_prediction_json(path: Path) -> EvaluationInput:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return EvaluationInput(
            rows=tuple(_prediction_from_mapping(row, source=path) for row in payload),
            document_contexts={},
        )
    if not isinstance(payload, dict):
        raise ValueError(f"Prediction JSON {path} must contain an object or list")
    if "segment_rows" in payload and "document" in payload:
        rows = tuple(_prediction_from_audit_row(row, payload, path) for row in payload["segment_rows"])
        document_name = normalize_document_name(str(payload["document"].get("document_name", path.name)))
        context = _audit_context(payload)
        return EvaluationInput(rows=rows, document_contexts={document_name: context})
    raw_rows = payload["rows"] if "rows" in payload else []
    return EvaluationInput(
        rows=tuple(_prediction_from_mapping(row, source=path) for row in raw_rows),
        document_contexts={},
    )


def _prediction_from_audit_row(
    row: dict[str, Any],
    payload: dict[str, Any],
    source: Path,
) -> PredictedRow:
    current_values = row.get("current_values", row)
    document = payload["document"]
    return _prediction_from_mapping(
        {
            **current_values,
            "company_name": document.get("company_name"),
            "document_name": document.get("document_name"),
            "fiscal_period": document.get("fiscal_period") or current_values.get("period_label"),
            "nace_code": _audit_nace_code(row),
            "notes": _audit_notes(row),
        },
        source=source,
    )


def _prediction_from_mapping(row: dict[str, Any], *, source: Path) -> PredictedRow:
    return PredictedRow(
        document_name=_required_text(row, "document_name", source),
        company_name=_optional_text(row.get("company_name")) or "",
        fiscal_period=_optional_text(row.get("fiscal_period") or row.get("period_label")) or "",
        segment_name=_required_text(row, "segment_name", source),
        revenue_value=_optional_decimal(row.get("revenue_value"), "revenue_value", source),
        currency=_optional_text(row.get("currency")),
        scale=_optional_text(row.get("scale")),
        page_ref=_optional_text(row.get("page_ref")),
        review_status=_optional_text(row.get("review_status") or row.get("status")),
        normalized_value=_optional_decimal(row.get("normalized_value"), "normalized_value", source),
        nace_code=_optional_text(row.get("nace_code")),
        notes=_optional_text(row.get("notes") or row.get("reviewer_note")),
    )


def _prediction_leaf_paths(paths: list[Path]) -> list[Path]:
    leaves: list[Path] = []
    for path in paths:
        if path.is_dir():
            audit_json = path / "audit_export.json"
            revenue_csv = path / "revenue_segments.csv"
            if audit_json.exists():
                leaves.append(audit_json)
            elif revenue_csv.exists():
                leaves.append(revenue_csv)
            else:
                leaves.extend(sorted(item for item in path.rglob("audit_export.json")))
                leaves.extend(sorted(item for item in path.rglob("revenue_segments.csv")))
        else:
            leaves.append(path)
    return leaves


def _audit_context(payload: dict[str, Any]) -> DocumentEvaluationContext:
    issues = payload.get("validation_issues") or []
    reconciliation_issues = [
        issue
        for issue in issues
        if "reconciliation" in str(issue.get("issue_type", "")).casefold()
    ]
    document = payload.get("document") or {}
    return DocumentEvaluationContext(
        validation_issue_count=len(issues),
        reconciliation_passed=not reconciliation_issues,
        time_seconds=_time_seconds(document.get("created_at"), payload.get("export_timestamp")),
    )


def _candidate_score(
    similarity: float,
    value_exact: bool,
    period_match: bool,
    page_match: bool | None,
) -> float:
    page_score = 0.5 if page_match is None else float(page_match)
    return (
        similarity * 0.55
        + float(value_exact) * 0.25
        + float(period_match) * 0.15
        + page_score * 0.05
    )


def _with_failure(match: RowMatch, *, duplicate_predicted: bool = False) -> RowMatch:
    failure = classify_failure(match, duplicate_predicted=duplicate_predicted)
    return RowMatch(
        gold=match.gold,
        predicted=match.predicted,
        score=match.score,
        segment_similarity=match.segment_similarity,
        value_exact=match.value_exact,
        period_match=match.period_match,
        page_ref_match=match.page_ref_match,
        status=match.status,
        failure_type=failure,
    )


def _is_correct_match(match: RowMatch) -> bool:
    return (
        match.gold is not None
        and match.predicted is not None
        and match.value_exact
        and match.period_match
        and not _currency_or_scale_mismatch(match.gold, match.predicted)
    )


def _currency_or_scale_mismatch(gold: GoldRow, predicted: PredictedRow) -> bool:
    currency_matches = (predicted.currency or "").casefold() == gold.currency.casefold()
    scale_matches = (predicted.scale or "").casefold() == gold.scale.casefold()
    return not currency_matches or not scale_matches


def _periods_match(left: str, right: str) -> bool:
    return _normalize_period(left) == _normalize_period(right)


def _normalize_period(value: str | None) -> str:
    text = (value or "").casefold().strip()
    year = re.search(r"(19|20)\d{2}", text)
    return year.group(0) if year else re.sub(r"[^a-z0-9]+", "", text)


def _duplicate_prediction_keys(rows: list[PredictedRow]) -> set[tuple[str, str]]:
    counts = Counter(_prediction_key(row) for row in rows)
    return {key for key, count in counts.items() if count > 1}


def _prediction_key(row: PredictedRow) -> tuple[str, str]:
    return (_normalize_period(row.fiscal_period), normalize_segment_name(row.segment_name))


def _failure_from_notes(notes: str) -> str | None:
    mappings = [
        ("non_english_issue", ("non-english", "non english", "translation", "language")),
        ("scanned_ocr_issue", ("ocr", "scanned", "scan")),
        ("nace_ambiguity", ("nace", "classification ambiguity")),
        ("esg_over_linking", ("esg over", "over-link", "overlink")),
        ("table_parsing_error", ("table parsing", "table_parse", "column alignment")),
    ]
    for failure_type, needles in mappings:
        if any(needle in notes for needle in needles):
            return failure_type
    return None


def _write_results_csv(matches: tuple[RowMatch, ...], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=RESULT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        for match in matches:
            writer.writerow(_result_row(match))


def _result_row(match: RowMatch) -> dict[str, str]:
    gold = match.gold
    predicted = match.predicted
    return {
        "document_name": (gold.document_name if gold else predicted.document_name if predicted else ""),
        "company_name": (gold.company_name if gold else predicted.company_name if predicted else ""),
        "fiscal_period": (gold.fiscal_period if gold else predicted.fiscal_period if predicted else ""),
        "gold_segment_name": gold.segment_name if gold else "",
        "predicted_segment_name": predicted.segment_name if predicted else "",
        "gold_revenue_value": _decimal_text(gold.revenue_value if gold else None),
        "predicted_revenue_value": _decimal_text(predicted.revenue_value if predicted else None),
        "gold_currency": gold.currency if gold else "",
        "predicted_currency": predicted.currency if predicted and predicted.currency else "",
        "gold_scale": gold.scale if gold else "",
        "predicted_scale": predicted.scale if predicted and predicted.scale else "",
        "gold_page_ref": gold.page_ref if gold and gold.page_ref else "",
        "predicted_page_ref": predicted.page_ref if predicted and predicted.page_ref else "",
        "match_status": match.status,
        "failure_type": match.failure_type or "",
        "segment_similarity": f"{match.segment_similarity:.3f}",
        "value_exact": str(match.value_exact),
        "period_match": str(match.period_match),
        "page_ref_match": "" if match.page_ref_match is None else str(match.page_ref_match),
        "review_status": predicted.review_status if predicted and predicted.review_status else "",
        "nace_code": (
            gold.nace_code
            if gold and gold.nace_code
            else predicted.nace_code if predicted and predicted.nace_code else ""
        ),
        "notes": (
            gold.notes
            if gold and gold.notes
            else predicted.notes if predicted and predicted.notes else ""
        ),
    }


def _failure_example(match: RowMatch) -> str:
    gold_name = match.gold.segment_name if match.gold else "(no gold row)"
    predicted_name = match.predicted.segment_name if match.predicted else "(no prediction)"
    document_name = (
        match.gold.document_name
        if match.gold
        else match.predicted.document_name if match.predicted else ""
    )
    return (
        f"{document_name}: gold `{gold_name}` vs predicted `{predicted_name}` "
        f"(status={match.status}, score={match.score:.2f})"
    )


def _required_text(row: dict[str, Any], key: str, source: Path) -> str:
    value = _optional_text(row.get(key))
    if value is None:
        raise ValueError(f"{source} row is missing required value {key!r}")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal(value: Any, key: str, source: Path) -> Decimal:
    parsed = _optional_decimal(value, key, source)
    if parsed is None:
        raise ValueError(f"{source} row is missing required decimal {key!r}")
    return parsed


def _optional_decimal(value: Any, key: str, source: Path) -> Decimal | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{source} has invalid decimal for {key!r}: {value!r}") from exc


def _has_value(row: dict[str, Any]) -> bool:
    return any(_optional_text(value) is not None for value in row.values())


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _format_rate(value: float) -> str:
    return f"{value:.3f}"


def _first_int(value: str) -> int | None:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def _singular_token(value: str) -> str:
    return value[:-1] if len(value) > 3 and value.endswith("s") else value


def _decimal_text(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _audit_nace_code(row: dict[str, Any]) -> str | None:
    selection = row.get("nace_selection")
    if isinstance(selection, dict):
        return _optional_text(selection.get("nace_code"))
    candidates = row.get("nace_candidates") or []
    if candidates and isinstance(candidates[0], dict):
        return _optional_text(candidates[0].get("nace_code"))
    return None


def _audit_notes(row: dict[str, Any]) -> str | None:
    events = row.get("review_events") or []
    notes = [
        event.get("note")
        for event in events
        if isinstance(event, dict) and _optional_text(event.get("note"))
    ]
    return " | ".join(str(note) for note in notes) if notes else None


def _time_seconds(start: Any, end: Any) -> float | None:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def _parse_datetime(value: Any) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
