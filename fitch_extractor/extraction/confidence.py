from __future__ import annotations

from dataclasses import dataclass

from fitch_extractor.extraction.normalization import NormalizedRevenueRow
from fitch_extractor.extraction.validation import DeterministicValidationIssue, ReconciliationResult


@dataclass(frozen=True)
class ConfidenceInputs:
    row: NormalizedRevenueRow
    row_issues: tuple[DeterministicValidationIssue, ...]
    reconciliation: ReconciliationResult


def compute_row_confidence(inputs: ConfidenceInputs) -> float:
    extraction_score = inputs.row.extraction_confidence
    if extraction_score is None:
        extraction_score = 0.5

    evidence_score = _evidence_score(inputs.row)
    normalization_score = _normalization_score(inputs.row)
    validation_score = _validation_score(inputs.row_issues)
    page_score = _page_relevance_score(inputs.row.page_relevance_score)
    reconciliation_score = _reconciliation_score(inputs.reconciliation)

    confidence = (
        extraction_score * 0.40
        + evidence_score * 0.18
        + normalization_score * 0.17
        + validation_score * 0.15
        + page_score * 0.05
        + reconciliation_score * 0.05
    )
    return round(max(0.0, min(1.0, confidence)), 4)


def _evidence_score(row: NormalizedRevenueRow) -> float:
    if row.page_number is not None and row.evidence_text.strip():
        return 1.0
    if row.page_number is not None or row.evidence_text.strip():
        return 0.5
    return 0.0


def _normalization_score(row: NormalizedRevenueRow) -> float:
    score = 1.0
    if row.revenue_value is None:
        score -= 0.35
    if row.currency is None:
        score -= 0.2
    if row.scale is None:
        score -= 0.2
    if row.period_label is None:
        score -= 0.15
    if row.warnings:
        score -= min(0.2, 0.05 * len(row.warnings))
    return max(0.0, score)


def _validation_score(issues: tuple[DeterministicValidationIssue, ...]) -> float:
    score = 1.0
    for issue in issues:
        if issue.blocking or issue.severity == "error":
            score -= 0.45
        elif issue.severity == "warning":
            score -= 0.15
        else:
            score -= 0.05
    return max(0.0, score)


def _page_relevance_score(score: float | None) -> float:
    if score is None:
        return 0.5
    return max(0.0, min(1.0, score / 20.0))


def _reconciliation_score(reconciliation: ReconciliationResult) -> float:
    if reconciliation.status == "matched":
        return 1.0
    if reconciliation.status == "not_available":
        return 0.7
    if reconciliation.status == "failed":
        return 0.2
    return 0.5
