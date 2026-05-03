from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from revenue_segment_extractor.enrichment import ROW_TYPE_BUSINESS_SEGMENT, classify_segment_row
from revenue_segment_extractor.models import (
    DOCUMENT_STATUS_APPROVED,
    EXPORT_READY_SEGMENT_STATUSES,
    SEGMENT_STATUS_REJECTED,
)
from revenue_segment_extractor.persistence.review import (
    DocumentReviewState,
    ESG_STATUS_PENDING,
    REVIEWED_ESG_STATUSES,
)


TABLE_EDIT_FIELDS = {
    "segment_name",
    "revenue_raw",
    "revenue_value",
    "normalized_value",
    "currency",
    "scale",
    "period_label",
    "page_ref",
    "section_ref",
    "confidence",
}

ESG_EDIT_FIELDS = {
    "factor_type",
    "polarity",
    "description",
    "page_ref",
    "evidence_text",
    "confidence",
}

NACE_NOT_APPLICABLE_EXACT_NAMES = {
    "as reported",
    "consolidated",
    "consolidated revenue",
    "consolidated revenues",
    "consolidated total",
    "reportable segments",
    "reported",
    "reported revenue",
    "reported revenues",
    "segment total",
    "segments total",
    "total",
    "total revenue",
    "total revenues",
}

NACE_NOT_APPLICABLE_NAME_TERMS = {
    "elimination",
    "eliminations",
    "hedging gains",
    "hedging losses",
    "inter-segment",
    "intersegment",
    "reclassification",
    "reconciling",
    "reconciliation",
}


def build_pipeline_steps(state: DocumentReviewState) -> list[dict[str, str]]:
    has_pages = state.page_count > 0
    has_rows = bool(state.segment_rows)
    has_validation = has_rows or bool(state.validation_issues)
    is_approved = state.document.status == DOCUMENT_STATUS_APPROVED
    review_complete = state.pending_row_count == 0 and not state.approval_check.blockers

    return [
        {"stage": "Parse PDF", "status": "complete" if has_pages else "not_started"},
        {
            "stage": "Find Evidence",
            "status": "complete" if has_pages else "not_started",
        },
        {"stage": "Extract Rows", "status": "complete" if has_rows else "not_started"},
        {"stage": "Validate", "status": "complete" if has_validation else "not_started"},
        {
            "stage": "Verify",
            "status": "complete" if has_validation else "not_started",
        },
        {"stage": "Analyst Review", "status": "complete" if review_complete else "in_progress"},
        {"stage": "Export", "status": "ready" if is_approved else "locked"},
    ]


def current_pipeline_step(steps: list[dict[str, str]]) -> int:
    for index, step in enumerate(steps):
        if step["status"] in {"in_progress", "not_started", "locked"}:
            return index
    return max(len(steps) - 1, 0)


def pipeline_progress(steps: list[dict[str, str]]) -> float:
    if not steps:
        return 0.0
    completed = sum(1 for step in steps if step["status"] in {"complete", "ready"})
    return completed / len(steps)


def build_review_tasks(state: DocumentReviewState) -> list[dict[str, str | int]]:
    return [
        _review_task(
            "Row decisions",
            _count_export_ready_rows(state),
            len(state.segment_rows),
            "Approve, edit, or reject every extracted segment row.",
            "segment-review",
        ),
        _review_task(
            "Required fields",
            _count_complete_non_rejected_rows(state),
            _count_non_rejected_rows(state),
            "Confirm value, unit, period, page reference, and evidence.",
            "segment-review",
        ),
        _review_task(
            "Validation issues",
            _count_non_blocking_issues(state),
            len(state.validation_issues),
            "Resolve blocking issues and acknowledge required warnings.",
            "validation",
        ),
        _review_task(
            "NACE mappings",
            _count_selected_nace_rows(state),
            _count_nace_applicable_rows(state),
            "Accept or override activity classifications for reviewed rows.",
            "nace-review",
        ),
        _review_task(
            "ESG factors",
            _count_reviewed_esg_factors(state),
            len(state.esg_factors),
            "Approve, edit, reject, or relink extracted ESG factors.",
            "esg-review",
        ),
        _review_task(
            "Export gate",
            1 if can_export(state) else 0,
            1,
            "Document approval unlocks final CSV/XLSX/JSON export.",
            "export",
        ),
    ]


def build_summary_cards(state: DocumentReviewState) -> dict[str, str | int]:
    return {
        "company": state.document.company_name,
        "document": state.document.document_name,
        "fiscal_period": state.document.fiscal_period or "",
        "pages": state.page_count,
        "rows": len(state.segment_rows),
        "pending_rows": state.pending_row_count,
        "flagged_rows": state.flagged_row_count,
        "esg_factors": len(state.esg_factors),
        "reconciliation": state.reconciliation_status,
    }


def segment_table_rows(state: DocumentReviewState) -> list[dict[str, Any]]:
    latest_notes = _latest_segment_notes(state)
    return [
        {
            "id": row.id,
            "segment_name": row.segment_name,
            "revenue_raw": row.revenue_raw or "",
            "revenue_value": _decimal_to_text(row.revenue_value),
            "normalized_value": _decimal_to_text(row.normalized_value),
            "currency": row.currency or "",
            "scale": row.scale or "",
            "period_label": row.period_label or "",
            "page_ref": row.page_ref or "",
            "section_ref": row.section_ref or "",
            "confidence": row.confidence,
            "status": row.status,
            "nace_code": _selected_nace_code(state, row.id),
            "nace_label": _selected_nace_label(state, row.id),
            "reviewer_note": latest_notes.get(row.id, ""),
        }
        for row in state.segment_rows
    ]


def changed_segment_rows(
    original_rows: list[dict[str, Any]],
    edited_rows: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], str | None]]:
    original_by_id = {row["id"]: row for row in original_rows}
    changes: list[tuple[str, dict[str, Any], str | None]] = []
    for edited in edited_rows:
        row_id = edited.get("id")
        if not row_id or row_id not in original_by_id:
            continue
        original = original_by_id[row_id]
        field_changes = {
            field: _clean_table_value(edited.get(field))
            for field in TABLE_EDIT_FIELDS
            if _clean_table_value(edited.get(field)) != _clean_table_value(original.get(field))
        }
        note = _clean_table_value(edited.get("reviewer_note"))
        original_note = _clean_table_value(original.get("reviewer_note"))
        if field_changes or note != original_note:
            changes.append((row_id, field_changes, note if note != original_note else None))
    return changes


def esg_factor_table_rows(
    state: DocumentReviewState,
    *,
    company_wide: bool,
) -> list[dict[str, Any]]:
    segment_names = {row.id: row.segment_name for row in state.segment_rows}
    return [
        {
            "id": factor.id,
            "segment_id": factor.segment_id or "",
            "segment_name": segment_names.get(factor.segment_id or "", ""),
            "factor_type": factor.factor_type,
            "polarity": factor.polarity,
            "description": factor.description,
            "page_ref": factor.page_ref or "",
            "evidence_text": factor.evidence_text,
            "confidence": factor.confidence,
            "status": state.esg_status_by_factor.get(factor.id, ESG_STATUS_PENDING),
        }
        for factor in state.esg_factors
        if factor.is_company_wide is company_wide
    ]


def changed_esg_factor_rows(
    original_rows: list[dict[str, Any]],
    edited_rows: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    original_by_id = {row["id"]: row for row in original_rows}
    changes: list[tuple[str, dict[str, Any]]] = []
    for edited in edited_rows:
        factor_id = edited.get("id")
        if not factor_id or factor_id not in original_by_id:
            continue
        original = original_by_id[factor_id]
        field_changes = {
            field: _clean_table_value(edited.get(field))
            for field in ESG_EDIT_FIELDS
            if _clean_table_value(edited.get(field)) != _clean_table_value(original.get(field))
        }
        if field_changes:
            changes.append((factor_id, field_changes))
    return changes


def can_export(state: DocumentReviewState) -> bool:
    return (
        state.document.status == DOCUMENT_STATUS_APPROVED
        and state.pending_row_count == 0
        and not state.approval_check.blockers
    )


def _review_task(
    label: str,
    done: int,
    total: int,
    detail: str,
    target: str,
) -> dict[str, str | int]:
    if total <= 0:
        return {
            "label": label,
            "percent": 100,
            "detail": f"{detail} None found.",
            "target": target,
        }
    percent = round((done / total) * 100)
    return {
        "label": label,
        "percent": max(0, min(100, percent)),
        "detail": f"{done}/{total}. {detail}",
        "target": target,
    }


def _count_export_ready_rows(state: DocumentReviewState) -> int:
    return sum(row.status in EXPORT_READY_SEGMENT_STATUSES for row in state.segment_rows)


def _count_non_rejected_rows(state: DocumentReviewState) -> int:
    return sum(row.status != SEGMENT_STATUS_REJECTED for row in state.segment_rows)


def _count_complete_non_rejected_rows(state: DocumentReviewState) -> int:
    return sum(
        row.status != SEGMENT_STATUS_REJECTED
        and _row_has_required_fields(row, state.evidence_by_segment.get(row.id, ()))
        for row in state.segment_rows
    )


def _count_non_blocking_issues(state: DocumentReviewState) -> int:
    return sum(not issue.blocks_approval for issue in state.validation_issues)


def _count_selected_nace_rows(state: DocumentReviewState) -> int:
    return sum(
        _row_needs_nace_mapping(row) and row.id in state.nace_selection_by_segment
        for row in state.segment_rows
    )


def _count_nace_applicable_rows(state: DocumentReviewState) -> int:
    return sum(_row_needs_nace_mapping(row) for row in state.segment_rows)


def _count_reviewed_esg_factors(state: DocumentReviewState) -> int:
    return sum(
        state.esg_status_by_factor.get(factor.id, ESG_STATUS_PENDING) in REVIEWED_ESG_STATUSES
        for factor in state.esg_factors
    )


def _row_has_required_fields(row: Any, evidence: tuple[Any, ...]) -> bool:
    has_page_or_section = bool(row.page_ref or row.section_ref)
    has_evidence = any(getattr(item, "snippet_text", "").strip() for item in evidence)
    return all(
        (
            bool(row.segment_name.strip()),
            row.normalized_value is not None,
            bool(row.currency),
            bool(row.scale),
            bool(row.period_label),
            has_page_or_section,
            has_evidence,
        )
    )


def _row_needs_nace_mapping(row: Any) -> bool:
    if row.status == SEGMENT_STATUS_REJECTED:
        return False

    row_type = getattr(row, "row_type", None) or classify_segment_row(
        row.segment_name,
        normalized_value=getattr(row, "normalized_value", None),
    ).row_type
    if row_type != ROW_TYPE_BUSINESS_SEGMENT:
        return False

    name = " ".join(row.segment_name.casefold().split())
    if not name:
        return False
    if name in NACE_NOT_APPLICABLE_EXACT_NAMES:
        return False
    if name.startswith(("total ", "reported ", "consolidated ")):
        return False
    if name.endswith((" total", " reported")):
        return False
    return not any(term in name for term in NACE_NOT_APPLICABLE_NAME_TERMS)


def nace_candidate_table_rows(state: DocumentReviewState, segment_id: str) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": candidate.id,
            "rank": candidate.rank,
            "code": candidate.nace_code,
            "label": candidate.nace_label,
            "level": candidate.nace_level,
            "match_score": candidate.match_score,
            "rationale": candidate.rationale or "",
        }
        for candidate in state.nace_candidates_by_segment.get(segment_id, ())
    ]


def coerce_decimal_text(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


def _latest_segment_notes(state: DocumentReviewState) -> dict[str, str]:
    notes: dict[str, str] = {}
    for event in state.review_events:
        if event.segment_id and event.note:
            notes[event.segment_id] = event.note
    return notes


def _selected_nace_code(state: DocumentReviewState, segment_id: str) -> str:
    selection = state.nace_selection_by_segment.get(segment_id)
    return selection.nace_code if selection else ""


def _selected_nace_label(state: DocumentReviewState, segment_id: str) -> str:
    selection = state.nace_selection_by_segment.get(segment_id)
    return selection.nace_label if selection else ""


def _decimal_to_text(value: Decimal | None) -> str:
    return str(value) if value is not None else ""


def _clean_table_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value
