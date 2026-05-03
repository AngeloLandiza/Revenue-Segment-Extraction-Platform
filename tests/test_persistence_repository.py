from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fitch_extractor.models import (
    DOCUMENT_STATUS_APPROVED,
    QUEUE_STATUS_COMPLETED,
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_RUNNING,
    SEGMENT_STATUS_APPROVED,
    EsgFactor,
    NaceCandidate,
    PageCandidate,
    ParsedPage,
    SegmentEvidence,
    SegmentScore,
    ValidationIssue,
)
from fitch_extractor.persistence import (
    ReviewService,
    SQLiteRepository,
    connect_database,
    initialize_database,
    initialize_database_file,
    reset_database_file,
)


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


class SQLiteRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect_database(":memory:")
        initialize_database(self.connection)
        self.repo = SQLiteRepository(
            self.connection,
            id_factory=SequentialIds(),
            clock=IncrementingClock(),
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_create_read_update_document_flow(self) -> None:
        document = self.repo.create_document(
            company_name="Example Fitch Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
            fiscal_period="FY2025",
            reported_total=Decimal("1000.50"),
            currency="USD",
            scale="millions",
        )

        self.assertEqual("doc_001", document.id)
        self.assertEqual(document, self.repo.get_document(document.id))

        updated = self.repo.update_document(
            document.id,
            analysis_notes="Reviewed source filing.",
            reported_total=Decimal("1001.25"),
        )

        self.assertEqual("Reviewed source filing.", updated.analysis_notes)
        self.assertEqual(Decimal("1001.25"), updated.reported_total)
        self.assertGreater(updated.updated_at, document.updated_at)
        self.assertEqual([updated], self.repo.list_documents())

    def test_document_queue_jobs_are_claimed_fifo_and_completed(self) -> None:
        first = self.repo.create_document(
            company_name="First Co.",
            document_name="first.pdf",
            source_path="fixtures/first.pdf",
        )
        second = self.repo.create_document(
            company_name="Second Co.",
            document_name="second.pdf",
            source_path="fixtures/second.pdf",
        )
        first_job = self.repo.create_document_queue_job(
            document_id=first.id,
            requested_by="analyst@example.com",
            provider_name="fake",
            model="fixture-model",
        )
        second_job = self.repo.create_document_queue_job(
            document_id=second.id,
            requested_by="analyst@example.com",
            provider_name="fake",
            model="fixture-model",
        )

        self.assertEqual(QUEUE_STATUS_PENDING, first_job.status)
        claimed = self.repo.claim_next_document_queue_job(worker_id="worker-1")

        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(first_job.id, claimed.id)
        self.assertEqual(QUEUE_STATUS_RUNNING, claimed.status)
        self.assertEqual("worker-1", claimed.worker_id)
        self.assertIsNotNone(claimed.started_at)

        completed = self.repo.complete_document_queue_job(claimed.id)

        self.assertEqual(QUEUE_STATUS_COMPLETED, completed.status)
        self.assertIsNotNone(completed.finished_at)
        self.assertEqual(
            [second_job.id],
            [
                job.id
                for job in self.repo.list_document_queue_jobs(
                    statuses=(QUEUE_STATUS_PENDING,)
                )
            ],
        )

    def test_create_read_update_segment_row_flow(self) -> None:
        document = self.repo.create_document(
            company_name="Example Fitch Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
        )

        segment = self.repo.create_segment_row(
            document_id=document.id,
            segment_name="Insurance",
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
            extraction_method="deterministic_fixture",
        )

        self.assertEqual("seg_002", segment.id)
        self.assertEqual(segment, self.repo.get_segment_row(segment.id))

        updated = self.repo.update_segment_row(
            segment.id,
            status=SEGMENT_STATUS_APPROVED,
            confidence=0.97,
        )

        self.assertEqual(SEGMENT_STATUS_APPROVED, updated.status)
        self.assertEqual(0.97, updated.confidence)
        self.assertEqual([updated], self.repo.list_segment_rows(document.id))

    def test_persists_related_entities_for_pipeline_state(self) -> None:
        document = self.repo.create_document(
            company_name="Example Fitch Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
        )
        segment = self.repo.create_segment_row(
            document_id=document.id,
            segment_name="Insurance",
        )

        page = ParsedPage(
            id="page_001",
            document_id=document.id,
            page_number=12,
            text="Revenue by segment table",
            blocks_json={"blocks": [{"text": "Revenue"}]},
            tables_json={"tables": [{"rows": 3}]},
            language="en",
            parser_sources=("pdfplumber",),
            has_text=True,
            created_at=datetime(2026, 1, 2, 3, 5, 0, tzinfo=UTC),
        )
        candidate = PageCandidate(
            id="candidate_001",
            document_id=document.id,
            page_number=12,
            relevance_score=0.88,
            matched_signals_json={"signals": ["segment", "revenue"]},
            reason="Matched revenue and segment signals.",
        )
        evidence = SegmentEvidence(
            id="evidence_001",
            segment_id=segment.id,
            document_id=document.id,
            page_number=12,
            snippet_text="Insurance revenue $42 million",
            bbox_json={"x0": 1, "y0": 2, "x1": 3, "y1": 4},
            parser_source="pdfplumber",
            evidence_kind="table_row",
        )
        issue = ValidationIssue(
            id="issue_001",
            document_id=document.id,
            segment_id=segment.id,
            severity="warning",
            issue_type="scale_check",
            message="Scale inferred from table header.",
            created_at=datetime(2026, 1, 2, 3, 6, 0, tzinfo=UTC),
        )
        nace = NaceCandidate(
            id="nace_001",
            segment_id=segment.id,
            nace_code="65.12",
            nace_label="Non-life insurance",
            nace_level=4,
            rank=1,
            match_score=0.82,
            rationale="Segment label mentions insurance.",
        )
        esg = EsgFactor(
            id="esg_001",
            segment_id=None,
            document_id=document.id,
            factor_type="climate",
            polarity="risk",
            description="Company-wide climate risk disclosure.",
            page_ref="p. 44",
            evidence_text="Climate risks may affect underwriting.",
            confidence=0.76,
            is_company_wide=True,
        )
        score = SegmentScore(
            id="score_001",
            segment_id=segment.id,
            base_score=50.0,
            adjustment_score=5.0,
            final_score=55.0,
            weight_share=0.25,
            rationale="Prototype fixture score.",
        )

        self.repo.save_parsed_page(page)
        self.repo.save_page_candidate(candidate)
        self.repo.save_segment_evidence(evidence)
        self.repo.save_validation_issue(issue)
        self.repo.save_nace_candidate(nace)
        self.repo.save_esg_factor(esg)
        self.repo.save_segment_score(score)

        self.assertEqual([page], self.repo.list_parsed_pages(document.id))
        self.assertEqual([candidate], self.repo.list_page_candidates(document.id))
        self.assertEqual([evidence], self.repo.list_segment_evidence(segment.id))
        self.assertEqual([issue], self.repo.list_validation_issues(document.id))
        self.assertEqual([nace], self.repo.list_nace_candidates(segment.id))
        self.assertEqual([esg], self.repo.list_esg_factors(document.id))
        self.assertEqual([score], self.repo.list_segment_scores(segment.id))

    def test_create_and_clear_parsed_outputs(self) -> None:
        document = self.repo.create_document(
            company_name="Example Fitch Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
        )

        page = self.repo.create_parsed_page(
            document_id=document.id,
            page_number=1,
            text="Operating segments",
            blocks_json={"blocks": []},
            tables_json={"tables": []},
            language="unknown",
            parser_sources=("pymupdf", "pdfplumber"),
            has_text=True,
        )
        candidate = self.repo.create_page_candidate(
            document_id=document.id,
            page_number=1,
            relevance_score=8.5,
            matched_signals_json={"terms": ["operating segments"]},
            reason="Matched operating segments.",
        )

        self.assertEqual([page], self.repo.list_parsed_pages(document.id))
        self.assertEqual([candidate], self.repo.list_page_candidates(document.id))

        self.repo.clear_parsed_outputs(document.id)

        self.assertEqual([], self.repo.list_parsed_pages(document.id))
        self.assertEqual([], self.repo.list_page_candidates(document.id))

    def test_review_event_logging_updates_segment_status(self) -> None:
        document = self.repo.create_document(
            company_name="Example Fitch Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
        )
        segment = self.repo.create_segment_row(
            document_id=document.id,
            segment_name="Insurance",
        )
        review_service = ReviewService(self.repo)

        event = review_service.record_segment_status_change(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
            action="approve",
            new_status=SEGMENT_STATUS_APPROVED,
            note="Evidence matches the source table.",
        )

        reviewed_segment = self.repo.get_segment_row(segment.id)
        self.assertIsNotNone(reviewed_segment)
        self.assertEqual(SEGMENT_STATUS_APPROVED, reviewed_segment.status)
        self.assertEqual("status", event.field_changed)
        self.assertEqual("pending", event.old_value)
        self.assertEqual(SEGMENT_STATUS_APPROVED, event.new_value)
        self.assertEqual([event], self.repo.list_review_events(document.id))

    def test_export_record_requires_reviewed_segments(self) -> None:
        document = self.repo.create_document(
            company_name="Example Fitch Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
        )
        segment = self.repo.create_segment_row(
            document_id=document.id,
            segment_name="Insurance",
        )

        with self.assertRaises(ValueError):
            self.repo.create_export_record(
                document_id=document.id,
                format="csv",
                path="outputs/revenue.csv",
            )

        ReviewService(self.repo).record_segment_status_change(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
            action="approve",
            new_status=SEGMENT_STATUS_APPROVED,
        )
        self.repo.update_document(document.id, status=DOCUMENT_STATUS_APPROVED)
        export = self.repo.create_export_record(
            document_id=document.id,
            format="csv",
            path="outputs/revenue.csv",
        )

        self.assertEqual("csv", export.format)
        self.assertEqual([export], self.repo.list_export_records(document.id))

    def test_initialize_and_reset_database_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "fitch.sqlite3"

            initialize_database_file(database_path)
            self.assertTrue(database_path.exists())

            reset_database_file(database_path)
            self.assertTrue(database_path.exists())


if __name__ == "__main__":
    unittest.main()
