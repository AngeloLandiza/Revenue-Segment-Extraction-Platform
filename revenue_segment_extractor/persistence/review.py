from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from revenue_segment_extractor.models import (
    DOCUMENT_STATUS_APPROVED,
    EXPORT_READY_SEGMENT_STATUSES,
    REVIEWABLE_SEGMENT_STATUSES,
    SEGMENT_STATUS_APPROVED,
    SEGMENT_STATUS_EDITED,
    SEGMENT_STATUS_REJECTED,
    VALIDATION_ISSUE_REVIEW_STATUSES,
    VALIDATION_ISSUE_STATUS_ACKNOWLEDGED,
    VALIDATION_ISSUE_STATUS_OPEN,
    VALIDATION_ISSUE_STATUS_RESOLVED,
    Document,
    NaceCandidate,
    NaceSelection,
    EsgFactor,
    ReviewEvent,
    SegmentEvidence,
    SegmentRow,
    SerializableModel,
    ValidationIssue,
    ValidationIssueReview,
)
from revenue_segment_extractor.persistence.repository import SQLiteRepository


EDITABLE_SEGMENT_FIELDS = {
    "segment_name",
    "revenue_raw",
    "revenue_value",
    "currency",
    "scale",
    "period_label",
    "normalized_value",
    "page_ref",
    "section_ref",
    "metric_basis",
    "confidence",
    "extraction_method",
}

EDITABLE_ESG_FIELDS = {
    "segment_id",
    "factor_type",
    "polarity",
    "description",
    "page_ref",
    "evidence_text",
    "confidence",
    "is_company_wide",
    "segment_link_type",
    "esg_category",
    "score_relevant",
    "impact_mechanism",
    "evidence_source",
    "cluster_key",
}

ESG_STATUS_PENDING = "pending"
ESG_STATUS_APPROVED = "approved"
ESG_STATUS_EDITED = "edited"
ESG_STATUS_REJECTED = "rejected"
REVIEWED_ESG_STATUSES = {ESG_STATUS_APPROVED, ESG_STATUS_EDITED, ESG_STATUS_REJECTED}

REQUIRED_REVIEW_FIELDS = {
    "segment_name": "Segment Name",
    "normalized_value": "Revenue Value",
    "currency": "Currency",
    "scale": "Scale",
    "period_label": "Time Period",
    "page_or_section": "Page/Section Reference",
    "evidence_text": "Evidence Text",
}

RECONCILIATION_ISSUE_TYPE = "total_reconciliation_mismatch"
OPTIONAL_LLM_REVIEW_ISSUE_TYPES = {
    "llm_arbitration_provider_error",
    "llm_arbitration_validation",
}


@dataclass(frozen=True)
class ReviewedValidationIssue(SerializableModel):
    issue: ValidationIssue
    review: ValidationIssueReview | None
    blocks_approval: bool
    why_it_matters: str


@dataclass(frozen=True)
class DocumentApprovalCheck(SerializableModel):
    can_approve: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class DocumentReviewState(SerializableModel):
    document: Document
    page_count: int
    segment_rows: tuple[SegmentRow, ...]
    evidence_by_segment: dict[str, tuple[SegmentEvidence, ...]]
    nace_candidates_by_segment: dict[str, tuple[NaceCandidate, ...]]
    nace_selection_by_segment: dict[str, NaceSelection]
    esg_factors: tuple[EsgFactor, ...]
    esg_status_by_factor: dict[str, str]
    validation_issues: tuple[ReviewedValidationIssue, ...]
    review_events: tuple[ReviewEvent, ...]
    approval_check: DocumentApprovalCheck

    @property
    def pending_row_count(self) -> int:
        return sum(row.status not in EXPORT_READY_SEGMENT_STATUSES for row in self.segment_rows)

    @property
    def flagged_row_count(self) -> int:
        flagged_segment_ids = {
            item.issue.segment_id
            for item in self.validation_issues
            if item.issue.segment_id is not None
            and _issue_review_status(item.review) != VALIDATION_ISSUE_STATUS_RESOLVED
        }
        return len(flagged_segment_ids)

    @property
    def pending_esg_factor_count(self) -> int:
        return sum(
            self.esg_status_by_factor.get(factor.id, ESG_STATUS_PENDING)
            not in REVIEWED_ESG_STATUSES
            for factor in self.esg_factors
        )

    @property
    def reconciliation_status(self) -> str:
        reconciliation_issues = [
            item
            for item in self.validation_issues
            if item.issue.issue_type == RECONCILIATION_ISSUE_TYPE
        ]
        if not reconciliation_issues:
            return "not_flagged"
        if all(
            _issue_review_status(item.review) == VALIDATION_ISSUE_STATUS_RESOLVED
            for item in reconciliation_issues
        ):
            return "resolved"
        if all(_has_reconciliation_acknowledgement(item) for item in reconciliation_issues):
            return "acknowledged"
        return "blocked"


class ReviewService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def get_document_review_state(self, document_id: str) -> DocumentReviewState:
        document = self._require_document(document_id)
        rows = tuple(self.repository.list_segment_rows(document_id))
        evidence_by_segment = {
            row.id: tuple(self.repository.list_segment_evidence(row.id)) for row in rows
        }
        nace_candidates_by_segment = {
            row.id: tuple(self.repository.list_nace_candidates(row.id)) for row in rows
        }
        nace_selection_by_segment = {
            selection.segment_id: selection
            for selection in self.repository.list_nace_selections(document_id)
        }
        issue_reviews = {
            review.issue_id: review
            for review in self.repository.list_validation_issue_reviews(document_id)
        }
        reviewed_issues = tuple(
            ReviewedValidationIssue(
                issue=issue,
                review=issue_reviews.get(issue.id),
                blocks_approval=self._issue_blocks_approval(
                    issue,
                    issue_reviews.get(issue.id),
                ),
                why_it_matters=_why_issue_matters(issue),
            )
            for issue in self.repository.list_validation_issues(document_id)
        )
        return DocumentReviewState(
            document=document,
            page_count=len(self.repository.list_parsed_pages(document_id)),
            segment_rows=rows,
            evidence_by_segment=evidence_by_segment,
            nace_candidates_by_segment=nace_candidates_by_segment,
            nace_selection_by_segment=nace_selection_by_segment,
            esg_factors=tuple(self.repository.list_esg_factors(document_id)),
            esg_status_by_factor=_latest_esg_statuses(
                self.repository.list_review_events(document_id)
            ),
            validation_issues=reviewed_issues,
            review_events=tuple(self.repository.list_review_events(document_id)),
            approval_check=self.check_document_approval(document_id),
        )

    def update_segment_row(
        self,
        *,
        document_id: str,
        segment_id: str,
        reviewer: str,
        changes: dict[str, Any],
        note: str | None = None,
    ) -> SegmentRow:
        if not changes:
            return self._require_segment(document_id, segment_id)
        unknown_fields = set(changes) - EDITABLE_SEGMENT_FIELDS
        if unknown_fields:
            raise ValueError(f"Unsupported segment review fields: {sorted(unknown_fields)}")

        segment = self._require_segment(document_id, segment_id)
        normalized_changes = {
            field: _normalize_change_value(field, value) for field, value in changes.items()
        }
        changed_fields = {
            field: value
            for field, value in normalized_changes.items()
            if getattr(segment, field) != value
        }
        if not changed_fields:
            self._record_event(
                document_id=document_id,
                segment_id=segment_id,
                reviewer=reviewer,
                action="note_segment_review",
                note=note,
            )
            return segment

        old_values = {field: getattr(segment, field) for field in changed_fields}
        if segment.status != SEGMENT_STATUS_EDITED:
            changed_fields["status"] = SEGMENT_STATUS_EDITED
            old_values["status"] = segment.status

        updated = self.repository.update_segment_row(segment_id, **changed_fields)
        for field, new_value in changed_fields.items():
            self._record_event(
                document_id=document_id,
                segment_id=segment_id,
                reviewer=reviewer,
                action="edit_segment_row",
                field_changed=field,
                old_value=_event_value(old_values[field]),
                new_value=_event_value(new_value),
                note=note,
            )
        return updated

    def accept_nace_candidate(
        self,
        *,
        document_id: str,
        segment_id: str,
        candidate_id: str,
        reviewer: str,
        note: str | None = None,
    ) -> NaceSelection:
        self._require_segment(document_id, segment_id)
        candidate = self._require_nace_candidate(segment_id, candidate_id)
        previous = self.repository.get_nace_selection(segment_id)
        selection = self.repository.upsert_nace_selection(
            segment_id=segment_id,
            nace_code=candidate.nace_code,
            nace_label=candidate.nace_label,
            nace_level=candidate.nace_level,
            match_score=candidate.match_score,
            rationale=candidate.rationale,
            source="reviewer_accept",
            reviewer=reviewer.strip() or "unknown_reviewer",
        )
        self._record_event(
            document_id=document_id,
            segment_id=segment_id,
            reviewer=reviewer,
            action="accept_nace_candidate",
            field_changed="nace_code",
            old_value=_selection_event_value(previous),
            new_value=_selection_event_value(selection),
            note=note,
        )
        return selection

    def override_segment_nace(
        self,
        *,
        document_id: str,
        segment_id: str,
        reviewer: str,
        nace_code: str,
        nace_label: str,
        nace_level: int,
        match_score: float | None = None,
        rationale: str | None = None,
        note: str | None = None,
    ) -> NaceSelection:
        self._require_segment(document_id, segment_id)
        clean_code = nace_code.strip()
        clean_label = nace_label.strip()
        if not clean_code:
            raise ValueError("nace_code must not be empty")
        if not clean_label:
            raise ValueError("nace_label must not be empty")
        if nace_level not in {1, 2, 3, 4}:
            raise ValueError("nace_level must be 1, 2, 3, or 4")

        previous = self.repository.get_nace_selection(segment_id)
        selection = self.repository.upsert_nace_selection(
            segment_id=segment_id,
            nace_code=clean_code,
            nace_label=clean_label,
            nace_level=nace_level,
            match_score=match_score,
            rationale=rationale.strip() if rationale else None,
            source="reviewer_override",
            reviewer=reviewer.strip() or "unknown_reviewer",
        )
        self._record_event(
            document_id=document_id,
            segment_id=segment_id,
            reviewer=reviewer,
            action="override_nace_code",
            field_changed="nace_code",
            old_value=_selection_event_value(previous),
            new_value=_selection_event_value(selection),
            note=note,
        )
        return selection

    def approve_segment_row(
        self,
        *,
        document_id: str,
        segment_id: str,
        reviewer: str,
        note: str | None = None,
    ) -> ReviewEvent:
        segment = self._require_segment(document_id, segment_id)
        missing_fields = _missing_required_fields(
            segment,
            self.repository.list_segment_evidence(segment.id),
        )
        if missing_fields:
            raise ValueError(f"Cannot approve segment with missing fields: {missing_fields}")
        return self.record_segment_status_change(
            document_id=document_id,
            segment_id=segment_id,
            reviewer=reviewer,
            action="approve_segment_row",
            new_status=SEGMENT_STATUS_APPROVED,
            note=note,
        )

    def reject_segment_row(
        self,
        *,
        document_id: str,
        segment_id: str,
        reviewer: str,
        note: str | None = None,
    ) -> ReviewEvent:
        return self.record_segment_status_change(
            document_id=document_id,
            segment_id=segment_id,
            reviewer=reviewer,
            action="reject_segment_row",
            new_status=SEGMENT_STATUS_REJECTED,
            note=note,
        )

    def add_manual_segment_row(
        self,
        *,
        document_id: str,
        reviewer: str,
        segment_name: str,
        revenue_raw: str | None = None,
        revenue_value: Decimal | str | int | float | None = None,
        currency: str | None = None,
        scale: str | None = None,
        period_label: str | None = None,
        normalized_value: Decimal | str | int | float | None = None,
        page_ref: str | None = None,
        section_ref: str | None = None,
        metric_basis: str | None = "revenue",
        confidence: float | None = None,
        evidence_text: str | None = None,
        evidence_page_number: int | None = None,
        evidence_parser_source: str = "manual_review",
        note: str | None = None,
    ) -> SegmentRow:
        self._require_document(document_id)
        if not segment_name.strip():
            raise ValueError("segment_name must not be empty")

        row = self.repository.create_segment_row(
            document_id=document_id,
            segment_name=segment_name.strip(),
            revenue_raw=_clean_text(revenue_raw),
            revenue_value=_to_decimal(revenue_value),
            currency=_clean_text(currency),
            scale=_clean_text(scale),
            period_label=_clean_text(period_label),
            normalized_value=_to_decimal(normalized_value),
            page_ref=_clean_text(page_ref),
            section_ref=_clean_text(section_ref),
            metric_basis=_clean_text(metric_basis),
            confidence=confidence,
            status=SEGMENT_STATUS_EDITED,
            extraction_method="manual_review",
        )
        if evidence_text and evidence_text.strip():
            self.repository.create_segment_evidence(
                segment_id=row.id,
                document_id=document_id,
                page_number=evidence_page_number or _page_number_from_ref(page_ref) or 0,
                snippet_text=evidence_text.strip(),
                bbox_json=None,
                parser_source=evidence_parser_source,
                evidence_kind="manual_review",
            )
        self._record_event(
            document_id=document_id,
            segment_id=row.id,
            reviewer=reviewer,
            action="add_manual_segment_row",
            old_value=None,
            new_value=_event_value(row.to_dict()),
            note=note,
        )
        return row

    def add_reviewer_note(
        self,
        *,
        document_id: str,
        reviewer: str,
        note: str,
        segment_id: str | None = None,
    ) -> ReviewEvent:
        if not note.strip():
            raise ValueError("note must not be empty")
        if segment_id is not None:
            self._require_segment(document_id, segment_id)
        else:
            document = self._require_document(document_id)
            self.repository.update_document(
                document_id,
                analysis_notes=_append_note(document.analysis_notes, reviewer, note),
            )
        return self._record_event(
            document_id=document_id,
            segment_id=segment_id,
            reviewer=reviewer,
            action="add_reviewer_note",
            note=note.strip(),
        )

    def mark_validation_issue(
        self,
        *,
        document_id: str,
        issue_id: str,
        reviewer: str,
        status: str,
        note: str | None = None,
    ) -> ValidationIssueReview:
        if status not in VALIDATION_ISSUE_REVIEW_STATUSES:
            raise ValueError(f"Unsupported validation issue review status: {status}")
        issue = self.repository.get_validation_issue(issue_id)
        if issue is None:
            raise KeyError(f"Validation issue not found: {issue_id}")
        if issue.document_id != document_id:
            raise ValueError("Validation issue does not belong to the supplied document")
        if (
            status == VALIDATION_ISSUE_STATUS_ACKNOWLEDGED
            and issue.issue_type == RECONCILIATION_ISSUE_TYPE
            and not (note and note.strip())
        ):
            raise ValueError("Reconciliation acknowledgement requires a reviewer note")

        previous = self.repository.get_validation_issue_review(issue_id)
        review = self.repository.upsert_validation_issue_review(
            issue_id=issue_id,
            document_id=document_id,
            status=status,
            reviewer=reviewer,
            note=note.strip() if note else None,
        )
        action = {
            VALIDATION_ISSUE_STATUS_ACKNOWLEDGED: "acknowledge_validation_issue",
            VALIDATION_ISSUE_STATUS_RESOLVED: "resolve_validation_issue",
            VALIDATION_ISSUE_STATUS_OPEN: "reopen_validation_issue",
        }[status]
        self._record_event(
            document_id=document_id,
            reviewer=reviewer,
            action=action,
            field_changed=f"validation_issue:{issue_id}:status",
            old_value=_issue_review_status(previous),
            new_value=status,
            note=note,
        )
        return review

    def update_esg_factor(
        self,
        *,
        document_id: str,
        factor_id: str,
        reviewer: str,
        changes: dict[str, Any],
        note: str | None = None,
    ) -> EsgFactor:
        factor = self._require_esg_factor(document_id, factor_id)
        if not changes:
            self._record_event(
                document_id=document_id,
                segment_id=factor.segment_id,
                reviewer=reviewer,
                action="note_esg_factor",
                field_changed=f"esg_factor:{factor_id}:note",
                note=note,
            )
            return factor

        unknown_fields = set(changes) - EDITABLE_ESG_FIELDS
        if unknown_fields:
            raise ValueError(f"Unsupported ESG factor fields: {sorted(unknown_fields)}")

        normalized_changes = {
            field: _normalize_esg_change_value(field, value)
            for field, value in changes.items()
        }
        segment_id = normalized_changes.get("segment_id", factor.segment_id)
        if segment_id is not None:
            self._require_segment(document_id, str(segment_id))
            normalized_changes["is_company_wide"] = False
        elif "segment_id" in normalized_changes:
            normalized_changes["is_company_wide"] = True

        changed_fields = {
            field: value
            for field, value in normalized_changes.items()
            if getattr(factor, field) != value
        }
        if not changed_fields:
            self._record_event(
                document_id=document_id,
                segment_id=factor.segment_id,
                reviewer=reviewer,
                action="note_esg_factor",
                field_changed=f"esg_factor:{factor_id}:note",
                note=note,
            )
            return factor

        old_values = {field: getattr(factor, field) for field in changed_fields}
        updated = self.repository.update_esg_factor(factor_id, **changed_fields)
        for field, new_value in changed_fields.items():
            self._record_event(
                document_id=document_id,
                segment_id=updated.segment_id,
                reviewer=reviewer,
                action="edit_esg_factor",
                field_changed=f"esg_factor:{factor_id}:{field}",
                old_value=_event_value(old_values[field]),
                new_value=_event_value(new_value),
                note=note,
            )
        self._record_esg_status(
            document_id=document_id,
            factor=updated,
            reviewer=reviewer,
            status=ESG_STATUS_EDITED,
            note=note,
        )
        return updated

    def unlink_esg_factor(
        self,
        *,
        document_id: str,
        factor_id: str,
        reviewer: str,
        note: str | None = None,
    ) -> EsgFactor:
        factor = self._require_esg_factor(document_id, factor_id)
        updated = self.repository.update_esg_factor(
            factor_id,
            segment_id=None,
            is_company_wide=True,
        )
        self._record_event(
            document_id=document_id,
            segment_id=factor.segment_id,
            reviewer=reviewer,
            action="unlink_esg_factor",
            field_changed=f"esg_factor:{factor_id}:segment_id",
            old_value=factor.segment_id,
            new_value=None,
            note=note,
        )
        self._record_esg_status(
            document_id=document_id,
            factor=updated,
            reviewer=reviewer,
            status=ESG_STATUS_EDITED,
            note=note,
        )
        return updated

    def relink_esg_factor(
        self,
        *,
        document_id: str,
        factor_id: str,
        segment_id: str,
        reviewer: str,
        note: str | None = None,
    ) -> EsgFactor:
        self._require_segment(document_id, segment_id)
        factor = self._require_esg_factor(document_id, factor_id)
        updated = self.repository.update_esg_factor(
            factor_id,
            segment_id=segment_id,
            is_company_wide=False,
        )
        self._record_event(
            document_id=document_id,
            segment_id=segment_id,
            reviewer=reviewer,
            action="relink_esg_factor",
            field_changed=f"esg_factor:{factor_id}:segment_id",
            old_value=factor.segment_id,
            new_value=segment_id,
            note=note,
        )
        self._record_esg_status(
            document_id=document_id,
            factor=updated,
            reviewer=reviewer,
            status=ESG_STATUS_EDITED,
            note=note,
        )
        return updated

    def approve_esg_factor(
        self,
        *,
        document_id: str,
        factor_id: str,
        reviewer: str,
        note: str | None = None,
    ) -> ReviewEvent:
        factor = self._require_esg_factor(document_id, factor_id)
        return self._record_esg_status(
            document_id=document_id,
            factor=factor,
            reviewer=reviewer,
            status=ESG_STATUS_APPROVED,
            note=note,
        )

    def reject_esg_factor(
        self,
        *,
        document_id: str,
        factor_id: str,
        reviewer: str,
        note: str | None = None,
    ) -> ReviewEvent:
        factor = self._require_esg_factor(document_id, factor_id)
        return self._record_esg_status(
            document_id=document_id,
            factor=factor,
            reviewer=reviewer,
            status=ESG_STATUS_REJECTED,
            note=note,
        )

    def check_document_approval(self, document_id: str) -> DocumentApprovalCheck:
        document = self._require_document(document_id)
        rows = self.repository.list_segment_rows(document_id)
        issues = self.repository.list_validation_issues(document_id)
        issue_reviews = {
            review.issue_id: review
            for review in self.repository.list_validation_issue_reviews(document_id)
        }

        blockers: list[str] = []
        warnings: list[str] = []
        if not document.company_name.strip():
            blockers.append("Company Name is missing.")
        if not document.document_name.strip():
            blockers.append("Document Name is missing.")
        if not rows:
            blockers.append("At least one segment row is required before document approval.")

        unaddressed_rows = [row for row in rows if row.status not in EXPORT_READY_SEGMENT_STATUSES]
        if unaddressed_rows:
            blockers.append(f"{len(unaddressed_rows)} segment row(s) still need review.")

        for row in rows:
            if row.status == SEGMENT_STATUS_REJECTED:
                continue
            missing_fields = _missing_required_fields(
                row,
                self.repository.list_segment_evidence(row.id),
            )
            if missing_fields:
                blockers.append(
                    f"Row '{row.segment_name}' is missing: {', '.join(missing_fields)}."
                )

        for issue in issues:
            review = issue_reviews.get(issue.id)
            if issue.issue_type in OPTIONAL_LLM_REVIEW_ISSUE_TYPES:
                if _issue_review_status(review) == "open":
                    warnings.append(issue.message)
            elif issue.severity == "error" and _issue_review_status(review) != "resolved":
                blockers.append(f"Blocking issue unresolved: {issue.message}")
            elif issue.issue_type == RECONCILIATION_ISSUE_TYPE:
                if not _has_reconciliation_acknowledgement(
                    ReviewedValidationIssue(
                        issue=issue,
                        review=review,
                        blocks_approval=False,
                        why_it_matters=_why_issue_matters(issue),
                    )
                ):
                    blockers.append(
                        "Total reconciliation mismatch must be resolved or acknowledged "
                        "with a reviewer note."
                    )
            elif issue.severity == "warning" and _issue_review_status(review) == "open":
                warnings.append(issue.message)

        return DocumentApprovalCheck(
            can_approve=not blockers,
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )

    def approve_document(
        self,
        *,
        document_id: str,
        reviewer: str,
        note: str | None = None,
    ) -> Document:
        check = self.check_document_approval(document_id)
        if not check.can_approve:
            raise ValueError("Document approval blocked: " + " ".join(check.blockers))
        document = self._require_document(document_id)
        updated = self.repository.update_document(document_id, status=DOCUMENT_STATUS_APPROVED)
        self._record_event(
            document_id=document_id,
            reviewer=reviewer,
            action="approve_document",
            field_changed="status",
            old_value=document.status,
            new_value=DOCUMENT_STATUS_APPROVED,
            note=note,
        )
        return updated

    def record_segment_status_change(
        self,
        *,
        document_id: str,
        segment_id: str,
        reviewer: str,
        action: str,
        new_status: str,
        note: str | None = None,
    ) -> ReviewEvent:
        if new_status not in REVIEWABLE_SEGMENT_STATUSES:
            raise ValueError(f"Unsupported segment review status: {new_status}")

        segment = self._require_segment(document_id, segment_id)
        old_status = segment.status
        self.repository.update_segment_row(segment_id, status=new_status)
        return self._record_event(
            document_id=document_id,
            segment_id=segment_id,
            reviewer=reviewer,
            action=action,
            field_changed="status",
            old_value=old_status,
            new_value=new_status,
            note=note,
        )

    def _issue_blocks_approval(
        self,
        issue: ValidationIssue,
        review: ValidationIssueReview | None,
    ) -> bool:
        if issue.issue_type in OPTIONAL_LLM_REVIEW_ISSUE_TYPES:
            return False
        if issue.severity == "error":
            return _issue_review_status(review) != VALIDATION_ISSUE_STATUS_RESOLVED
        if issue.issue_type == RECONCILIATION_ISSUE_TYPE:
            return not _has_reconciliation_acknowledgement(
                ReviewedValidationIssue(
                    issue=issue,
                    review=review,
                    blocks_approval=False,
                    why_it_matters=_why_issue_matters(issue),
                )
            )
        return False

    def _record_event(
        self,
        *,
        document_id: str,
        reviewer: str,
        action: str,
        segment_id: str | None = None,
        field_changed: str | None = None,
        old_value: str | None = None,
        new_value: str | None = None,
        note: str | None = None,
    ) -> ReviewEvent:
        return self.repository.create_review_event(
            document_id=document_id,
            segment_id=segment_id,
            reviewer=reviewer.strip() or "unknown_reviewer",
            action=action,
            field_changed=field_changed,
            old_value=old_value,
            new_value=new_value,
            note=note.strip() if note else None,
        )

    def _record_esg_status(
        self,
        *,
        document_id: str,
        factor: EsgFactor,
        reviewer: str,
        status: str,
        note: str | None = None,
    ) -> ReviewEvent:
        if status not in REVIEWED_ESG_STATUSES:
            raise ValueError(f"Unsupported ESG review status: {status}")
        previous_status = _latest_esg_statuses(
            self.repository.list_review_events(document_id)
        ).get(factor.id, ESG_STATUS_PENDING)
        return self._record_event(
            document_id=document_id,
            segment_id=factor.segment_id,
            reviewer=reviewer,
            action=f"{status}_esg_factor",
            field_changed=f"esg_factor:{factor.id}:status",
            old_value=previous_status,
            new_value=status,
            note=note,
        )

    def _require_document(self, document_id: str) -> Document:
        document = self.repository.get_document(document_id)
        if document is None:
            raise KeyError(f"Document not found: {document_id}")
        return document

    def _require_segment(self, document_id: str, segment_id: str) -> SegmentRow:
        segment = self.repository.get_segment_row(segment_id)
        if segment is None:
            raise KeyError(f"Segment row not found: {segment_id}")
        if segment.document_id != document_id:
            raise ValueError("Segment row does not belong to the supplied document")
        return segment

    def _require_esg_factor(self, document_id: str, factor_id: str) -> EsgFactor:
        factor = self.repository.get_esg_factor(factor_id)
        if factor is None:
            raise KeyError(f"ESG factor not found: {factor_id}")
        if factor.document_id != document_id:
            raise ValueError("ESG factor does not belong to the supplied document")
        return factor

    def _require_nace_candidate(self, segment_id: str, candidate_id: str) -> NaceCandidate:
        for candidate in self.repository.list_nace_candidates(segment_id):
            if candidate.id == candidate_id:
                return candidate
        raise KeyError(f"NACE candidate not found for segment {segment_id}: {candidate_id}")


def _missing_required_fields(
    row: SegmentRow,
    evidence: list[SegmentEvidence] | tuple[SegmentEvidence, ...],
) -> list[str]:
    missing: list[str] = []
    if not row.segment_name.strip():
        missing.append(REQUIRED_REVIEW_FIELDS["segment_name"])
    if row.normalized_value is None:
        missing.append(REQUIRED_REVIEW_FIELDS["normalized_value"])
    if not row.currency:
        missing.append(REQUIRED_REVIEW_FIELDS["currency"])
    if not row.scale:
        missing.append(REQUIRED_REVIEW_FIELDS["scale"])
    if not row.period_label:
        missing.append(REQUIRED_REVIEW_FIELDS["period_label"])
    if not row.page_ref and not row.section_ref:
        missing.append(REQUIRED_REVIEW_FIELDS["page_or_section"])
    if not any(item.snippet_text.strip() for item in evidence):
        missing.append(REQUIRED_REVIEW_FIELDS["evidence_text"])
    return missing


def _normalize_change_value(field: str, value: Any) -> Any:
    if field in {"revenue_value", "normalized_value"}:
        return _to_decimal(value)
    if field == "confidence" and value is not None:
        return float(value)
    if isinstance(value, str):
        return _clean_text(value)
    return value


def _normalize_esg_change_value(field: str, value: Any) -> Any:
    if field == "confidence":
        return None if value is None or value == "" else float(value)
    if field == "is_company_wide":
        return bool(value)
    if isinstance(value, str):
        return _clean_text(value)
    return value


def _to_decimal(value: Decimal | str | int | float | None) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _event_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _selection_event_value(selection: NaceSelection | None) -> str | None:
    if selection is None:
        return None
    return _event_value(
        {
            "code": selection.nace_code,
            "label": selection.nace_label,
            "level": selection.nace_level,
            "source": selection.source,
        }
    )


def _issue_review_status(review: ValidationIssueReview | None) -> str:
    return review.status if review else VALIDATION_ISSUE_STATUS_OPEN


def _latest_esg_statuses(events: list[ReviewEvent] | tuple[ReviewEvent, ...]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for event in events:
        if not event.field_changed or not event.field_changed.startswith("esg_factor:"):
            continue
        parts = event.field_changed.split(":")
        if len(parts) == 3 and parts[2] == "status" and event.new_value:
            statuses[parts[1]] = event.new_value
    return statuses


def _has_reconciliation_acknowledgement(item: ReviewedValidationIssue) -> bool:
    status = _issue_review_status(item.review)
    if status == VALIDATION_ISSUE_STATUS_RESOLVED:
        return True
    return (
        status == VALIDATION_ISSUE_STATUS_ACKNOWLEDGED
        and item.review is not None
        and bool(item.review.note and item.review.note.strip())
    )


def _why_issue_matters(issue: ValidationIssue) -> str:
    if issue.issue_type == RECONCILIATION_ISSUE_TYPE:
        return "The segment total does not reconcile to a reported total, so final output could be incomplete or double-counted."
    if issue.issue_type.startswith("missing_"):
        return "A required extraction attribute is missing and may make the row unusable in final output."
    if issue.severity == "error":
        return "This issue indicates extraction or validation failed in a way that blocks approval until resolved."
    if issue.severity == "warning":
        return "This issue should be reviewed because it may affect row accuracy."
    return "This issue records review context from the extraction pipeline."


def _append_note(existing: str | None, reviewer: str, note: str) -> str:
    entry = f"{reviewer.strip() or 'unknown_reviewer'}: {note.strip()}"
    if not existing:
        return entry
    return f"{existing}\n{entry}"


def _page_number_from_ref(page_ref: str | None) -> int | None:
    if not page_ref:
        return None
    digits = "".join(character for character in page_ref if character.isdigit())
    return int(digits) if digits else None
