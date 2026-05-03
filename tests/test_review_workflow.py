from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fitch_extractor.models import (
    DOCUMENT_STATUS_APPROVED,
    ExportRecord,
    SEGMENT_STATUS_APPROVED,
    SEGMENT_STATUS_EDITED,
    VALIDATION_ISSUE_STATUS_ACKNOWLEDGED,
    VALIDATION_ISSUE_STATUS_RESOLVED,
)
from fitch_extractor.persistence import (
    ReviewService,
    SQLiteRepository,
    connect_database,
    initialize_database,
)
from fitch_extractor.ui.review import (
    build_review_tasks,
    build_pipeline_steps,
    changed_segment_rows,
    current_pipeline_step,
    pipeline_progress,
    segment_table_rows,
)
from streamlit_app import _validation_issue_table_rows
from streamlit_app import _batch_accept_nace_candidates
from streamlit_app import _latest_export_records_by_format
from streamlit_app import _auto_approve_document


class SequentialIds:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}_{self.count:03d}"


class IncrementingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.current
        self.current = self.current + timedelta(seconds=1)
        return value


class ReviewWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect_database(":memory:")
        initialize_database(self.connection)
        self.repo = SQLiteRepository(
            self.connection,
            id_factory=SequentialIds(),
            clock=IncrementingClock(),
        )
        self.service = ReviewService(self.repo)

    def tearDown(self) -> None:
        self.connection.close()

    def test_update_segment_logs_field_events_and_marks_row_edited(self) -> None:
        document = self._create_document()
        segment = self._create_complete_segment(document.id)

        updated = self.service.update_segment_row(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
            changes={"normalized_value": "43000000", "currency": "EUR"},
            note="Corrected to source table.",
        )

        self.assertEqual(SEGMENT_STATUS_EDITED, updated.status)
        self.assertEqual(Decimal("43000000"), updated.normalized_value)
        events = self.repo.list_review_events(document.id)
        self.assertEqual(
            ["normalized_value", "currency", "status"],
            [event.field_changed for event in events],
        )
        self.assertEqual("42000000", events[0].old_value)
        self.assertEqual("43000000", events[0].new_value)

    def test_manual_row_addition_creates_evidence_and_review_event(self) -> None:
        document = self._create_document()

        row = self.service.add_manual_segment_row(
            document_id=document.id,
            reviewer="analyst@example.com",
            segment_name="Asset Management",
            revenue_raw="$8 million",
            revenue_value="8",
            normalized_value="8000000",
            currency="USD",
            scale="millions",
            period_label="FY2025",
            page_ref="p. 14",
            evidence_text="Asset Management revenue was $8 million.",
            note="Added missing segment.",
        )

        self.assertEqual(SEGMENT_STATUS_EDITED, row.status)
        self.assertEqual("manual_review", row.extraction_method)
        self.assertEqual(1, len(self.repo.list_segment_evidence(row.id)))
        events = self.repo.list_review_events(document.id)
        self.assertEqual("add_manual_segment_row", events[0].action)
        self.assertIn("Asset Management", events[0].new_value or "")

    def test_document_approval_gating_requires_addressed_rows_and_issue_review(self) -> None:
        document = self._create_document()
        segment = self._create_complete_segment(document.id)
        self.repo.create_validation_issue(
            document_id=document.id,
            severity="error",
            issue_type="missing_evidence_text",
            message="A row had no evidence text.",
            segment_id=segment.id,
        )
        reconciliation_issue = self.repo.create_validation_issue(
            document_id=document.id,
            severity="warning",
            issue_type="total_reconciliation_mismatch",
            message="Segment total does not reconcile.",
        )

        check = self.service.check_document_approval(document.id)
        self.assertFalse(check.can_approve)
        self.assertTrue(any("still need review" in blocker for blocker in check.blockers))

        self.service.approve_segment_row(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
        )
        with self.assertRaises(ValueError):
            self.service.mark_validation_issue(
                document_id=document.id,
                issue_id=reconciliation_issue.id,
                reviewer="analyst@example.com",
                status=VALIDATION_ISSUE_STATUS_ACKNOWLEDGED,
            )

        self.service.mark_validation_issue(
            document_id=document.id,
            issue_id=reconciliation_issue.id,
            reviewer="analyst@example.com",
            status=VALIDATION_ISSUE_STATUS_ACKNOWLEDGED,
            note="Reviewed rounding bridge; accepted for prototype output.",
        )
        check = self.service.check_document_approval(document.id)
        self.assertFalse(check.can_approve)
        self.assertTrue(any("Blocking issue unresolved" in blocker for blocker in check.blockers))

        error_issue = self.repo.list_validation_issues(document.id)[0]
        self.service.mark_validation_issue(
            document_id=document.id,
            issue_id=error_issue.id,
            reviewer="analyst@example.com",
            status=VALIDATION_ISSUE_STATUS_RESOLVED,
            note="Evidence was added.",
        )

        approved = self.service.approve_document(
            document_id=document.id,
            reviewer="analyst@example.com",
        )
        self.assertEqual(DOCUMENT_STATUS_APPROVED, approved.status)

    def test_export_remains_blocked_until_document_approval(self) -> None:
        document = self._create_document()
        segment = self._create_complete_segment(document.id)
        self.service.approve_segment_row(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
        )

        with self.assertRaises(ValueError):
            self.repo.create_export_record(
                document_id=document.id,
                format="csv",
                path="outputs/revenue.csv",
            )

        self.service.approve_document(document_id=document.id, reviewer="analyst@example.com")
        export = self.repo.create_export_record(
            document_id=document.id,
            format="csv",
            path="outputs/revenue.csv",
        )
        self.assertEqual("csv", export.format)

    def test_streamlit_review_helpers_detect_table_changes(self) -> None:
        document = self._create_document()
        segment = self._create_complete_segment(document.id)
        self.service.approve_segment_row(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
            note="Looks good.",
        )
        state = self.service.get_document_review_state(document.id)

        table_rows = segment_table_rows(state)
        edited_rows = [dict(table_rows[0], currency="EUR", reviewer_note="Currency corrected.")]
        changes = changed_segment_rows(table_rows, edited_rows)

        self.assertEqual([(segment.id, {"currency": "EUR"}, "Currency corrected.")], changes)
        self.assertEqual("complete", build_pipeline_steps(state)[2]["status"])

    def test_pipeline_helpers_return_compact_step_labels_and_progress(self) -> None:
        document = self._create_document()
        self.repo.create_parsed_page(
            document_id=document.id,
            page_number=1,
            text="Note 4 - Segment revenue by business line.",
            blocks_json={"blocks": []},
            tables_json={"tables": []},
            language="en",
            parser_sources=("fixture",),
            has_text=True,
        )
        segment = self._create_complete_segment(document.id)
        self.service.approve_segment_row(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
        )
        state = self.service.get_document_review_state(document.id)

        steps = build_pipeline_steps(state)

        self.assertEqual("Parse PDF", steps[0]["stage"])
        self.assertEqual("Find Evidence", steps[1]["stage"])
        self.assertEqual("Analyst Review", steps[5]["stage"])
        self.assertGreater(pipeline_progress(steps), 0)
        self.assertEqual(6, current_pipeline_step(steps))

    def test_review_tasks_show_approval_readiness_progress(self) -> None:
        document = self._create_document()
        segment = self._create_complete_segment(document.id)
        self.service.approve_segment_row(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
        )
        state = self.service.get_document_review_state(document.id)

        tasks = {str(task["label"]): task for task in build_review_tasks(state)}

        self.assertEqual(100, tasks["Row decisions"]["percent"])
        self.assertEqual("segment-review", tasks["Row decisions"]["target"])
        self.assertEqual(100, tasks["Required fields"]["percent"])
        self.assertEqual("validation", tasks["Validation issues"]["target"])
        self.assertEqual(0, tasks["Export gate"]["percent"])
        self.assertEqual("export", tasks["Export gate"]["target"])

    def test_nace_review_progress_ignores_rollup_and_reconciliation_rows(self) -> None:
        document = self._create_document()
        operating_segments = [
            self._create_complete_segment(document.id, segment_name="Insurance"),
            self._create_complete_segment(document.id, segment_name="Banking"),
            self._create_complete_segment(document.id, segment_name="Cloud Services"),
        ]
        self._create_complete_segment(document.id, segment_name="Total revenue")
        self._create_complete_segment(document.id, segment_name="Eliminations")
        self._create_complete_segment(document.id, segment_name="Reported revenue")

        for segment in operating_segments:
            self.service.override_segment_nace(
                document_id=document.id,
                segment_id=segment.id,
                reviewer="analyst@example.com",
                nace_code="65.12",
                nace_label="Non-life insurance",
                nace_level=4,
            )

        state = self.service.get_document_review_state(document.id)
        tasks = {str(task["label"]): task for task in build_review_tasks(state)}

        self.assertEqual(100, tasks["NACE mappings"]["percent"])
        self.assertTrue(str(tasks["NACE mappings"]["detail"]).startswith("3/3."))

    def test_optional_arbitration_provider_error_does_not_block_approval(self) -> None:
        document = self._create_document()
        segment = self._create_complete_segment(document.id)
        self.repo.create_validation_issue(
            document_id=document.id,
            severity="error",
            issue_type="llm_arbitration_provider_error",
            message="Anthropic extraction failed for optional arbitration.",
            segment_id=None,
        )

        self.service.approve_segment_row(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
        )
        check = self.service.check_document_approval(document.id)

        self.assertTrue(check.can_approve)
        self.assertFalse(check.blockers)
        self.assertTrue(check.warnings)

    def test_validation_issue_table_rows_include_review_selection_context(self) -> None:
        document = self._create_document()
        issue = self.repo.create_validation_issue(
            document_id=document.id,
            severity="warning",
            issue_type="scale_check",
            message="Scale inferred from table header.",
        )
        state = self.service.get_document_review_state(document.id)

        rows = _validation_issue_table_rows(state)

        self.assertEqual(issue.id, rows[0]["issue_id"])
        self.assertEqual("warning", rows[0]["severity"])
        self.assertEqual("scale_check", rows[0]["issue_type"])
        self.assertEqual("open", rows[0]["review_status"])
        self.assertFalse(rows[0]["blocks_approval"])

    def test_batch_nace_accepts_highest_ranked_selected_candidate(self) -> None:
        document = self._create_document()
        segment = self._create_complete_segment(document.id)
        weaker = self.repo.create_nace_candidate(
            segment_id=segment.id,
            nace_code="65.1",
            nace_label="Insurance",
            nace_level=3,
            rank=2,
            match_score=0.70,
            rationale="Broader insurance match.",
        )
        stronger = self.repo.create_nace_candidate(
            segment_id=segment.id,
            nace_code="65.12",
            nace_label="Non-life insurance",
            nace_level=4,
            rank=1,
            match_score=0.92,
            rationale="Best segment match.",
        )
        state = self.service.get_document_review_state(document.id)

        _batch_accept_nace_candidates(
            self.service,
            state,
            segment.id,
            "analyst@example.com",
            [weaker.id, stronger.id],
        )

        selection = self.repo.get_nace_selection(segment.id)
        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual("65.12", selection.nace_code)

    def test_latest_export_records_by_format_keeps_newest_file_per_format(self) -> None:
        older_csv = ExportRecord(
            id="export_001",
            document_id="doc_001",
            format="csv",
            path="exports/doc_001/old.csv",
            created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        )
        latest_csv = ExportRecord(
            id="export_002",
            document_id="doc_001",
            format="CSV",
            path="exports/doc_001/revenue_segments.csv",
            created_at=datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC),
        )
        latest_json = ExportRecord(
            id="export_003",
            document_id="doc_001",
            format="json",
            path="exports/doc_001/audit_export.json",
            created_at=datetime(2026, 1, 2, 3, 4, 7, tzinfo=UTC),
        )

        records = _latest_export_records_by_format([older_csv, latest_csv, latest_json])

        self.assertEqual(latest_csv, records["csv"])
        self.assertEqual(latest_json, records["json"])

    def test_auto_approve_document_reviews_open_items_then_approves_document(self) -> None:
        document = self._create_document()
        segment = self._create_complete_segment(document.id, segment_name="Insurance")
        self.repo.create_nace_candidate(
            segment_id=segment.id,
            nace_code="65.12",
            nace_label="Non-life insurance",
            nace_level=4,
            rank=1,
            match_score=0.92,
            rationale="Best segment match.",
        )
        validation_issue = self.repo.create_validation_issue(
            document_id=document.id,
            segment_id=segment.id,
            severity="warning",
            issue_type="scale_check",
            message="Scale inferred from table header.",
        )
        esg_factor = self.repo.create_esg_factor(
            document_id=document.id,
            segment_id=segment.id,
            factor_type="emissions",
            polarity="negative",
            description="Insurance operations expose underwriting emissions risk.",
            page_ref="p. 45",
            evidence_text="Insurance operations expose underwriting emissions risk.",
            confidence=0.81,
            is_company_wide=False,
        )
        state = self.service.get_document_review_state(document.id)

        summary = _auto_approve_document(self.service, state, "analyst@example.com")

        approved_document = self.repo.get_document(document.id)
        assert approved_document is not None
        refreshed = self.service.get_document_review_state(document.id)
        self.assertEqual(DOCUMENT_STATUS_APPROVED, approved_document.status)
        self.assertEqual({"rows": 1, "nace": 1, "validation": 1, "esg": 1}, summary)
        self.assertEqual(SEGMENT_STATUS_APPROVED, refreshed.segment_rows[0].status)
        self.assertIsNotNone(self.repo.get_nace_selection(segment.id))
        self.assertEqual(
            VALIDATION_ISSUE_STATUS_ACKNOWLEDGED,
            self.repo.get_validation_issue_review(validation_issue.id).status,
        )
        self.assertEqual("approved", refreshed.esg_status_by_factor[esg_factor.id])

    def _create_document(self):
        return self.repo.create_document(
            company_name="Example Fitch Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
            fiscal_period="FY2025",
            reported_total=Decimal("42000000"),
            currency="USD",
            scale="millions",
        )

    def _create_complete_segment(self, document_id: str, segment_name: str = "Insurance"):
        segment = self.repo.create_segment_row(
            document_id=document_id,
            segment_name=segment_name,
            revenue_raw="$42 million",
            revenue_value=Decimal("42"),
            currency="USD",
            scale="millions",
            period_label="FY2025",
            normalized_value=Decimal("42000000"),
            page_ref="p. 12",
            section_ref="Revenue by segment",
            metric_basis="revenue",
            confidence=0.91,
            extraction_method="fixture",
        )
        self.repo.create_segment_evidence(
            segment_id=segment.id,
            document_id=document_id,
            page_number=12,
            snippet_text=f"{segment_name} revenue $42 million",
            bbox_json={"x0": 1, "y0": 2, "x1": 3, "y1": 4},
            parser_source="pdfplumber",
            evidence_kind="table_row",
        )
        return segment


if __name__ == "__main__":
    unittest.main()
