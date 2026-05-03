from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fitch_extractor.exporting import EXPORT_COLUMNS, ExportService
from fitch_extractor.models import (
    DOCUMENT_STATUS_APPROVED,
    SEGMENT_STATUS_APPROVED,
    SEGMENT_STATUS_REJECTED,
)
from fitch_extractor.persistence import (
    ReviewService,
    SQLiteRepository,
    connect_database,
    initialize_database,
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


class ExportServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect_database(":memory:")
        initialize_database(self.connection)
        self.repo = SQLiteRepository(
            self.connection,
            id_factory=SequentialIds(),
            clock=IncrementingClock(),
        )
        self.review = ReviewService(self.repo)

    def tearDown(self) -> None:
        self.connection.close()

    def test_csv_export_uses_required_columns_and_reviewed_values(self) -> None:
        document_id, approved_row_id, _ = self._create_approved_document_with_rows()
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = ExportService(self.repo, temp_dir).export_document(document_id)

            with bundle.csv_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(EXPORT_COLUMNS, list(rows[0].keys()))
        self.assertEqual(1, len(rows))
        self.assertEqual("Example Fitch Co.", rows[0]["company_name"])
        self.assertEqual("Insurance", rows[0]["segment_name"])
        self.assertEqual("42", rows[0]["revenue_value"])
        self.assertEqual("42000000", rows[0]["normalized_value"])
        self.assertEqual("USD millions", rows[0]["revenue_unit"])
        self.assertEqual("Looks good.", rows[0]["reviewer_note"])
        self.assertEqual(approved_row_id, self.repo.list_segment_rows(document_id)[0].id)
        self.assertEqual(["csv", "xlsx", "json"], [record.format for record in bundle.records])

    def test_json_audit_export_includes_rejected_rows_evidence_events_and_issues(self) -> None:
        document_id, approved_row_id, rejected_row_id = self._create_approved_document_with_rows()
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = ExportService(self.repo, temp_dir).export_document(document_id)
            payload = json.loads(bundle.json_path.read_text(encoding="utf-8"))

        exported_ids = {
            row["current_values"]["id"] for row in payload["segment_rows"]
        }
        self.assertEqual({approved_row_id, rejected_row_id}, exported_ids)
        self.assertEqual(DOCUMENT_STATUS_APPROVED, payload["document"]["status"])
        self.assertEqual(2, len(payload["evidence"]))
        self.assertTrue(payload["validation_issues"])
        self.assertTrue(payload["review_events"])
        rejected_row = next(
            row
            for row in payload["segment_rows"]
            if row["current_values"]["id"] == rejected_row_id
        )
        self.assertEqual(SEGMENT_STATUS_REJECTED, rejected_row["current_values"]["status"])

    def test_export_is_blocked_before_document_approval(self) -> None:
        document = self.repo.create_document(
            company_name="Example Fitch Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
            fiscal_period="FY2025",
        )
        segment = self._create_complete_segment(document.id, "Insurance")
        self.review.approve_segment_row(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                ExportService(self.repo, temp_dir).export_document(document.id)

    def test_rejected_rows_are_excluded_from_main_csv(self) -> None:
        document_id, _, _ = self._create_approved_document_with_rows()
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = ExportService(self.repo, temp_dir).export_document(document_id)
            with bundle.csv_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(["Insurance"], [row["segment_name"] for row in rows])

    def test_xlsx_export_creates_valid_package_with_sheet_data(self) -> None:
        document_id, _, _ = self._create_approved_document_with_rows()
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = ExportService(self.repo, temp_dir).export_document(document_id)

            self.assertTrue(bundle.xlsx_path.exists())
            with zipfile.ZipFile(bundle.xlsx_path) as archive:
                names = set(archive.namelist())
                worksheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertIn("[Content_Types].xml", names)
        self.assertIn("xl/workbook.xml", names)
        self.assertIn("Insurance", worksheet)

    def _create_approved_document_with_rows(self) -> tuple[str, str, str]:
        document = self.repo.create_document(
            company_name="Example Fitch Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
            fiscal_period="FY2025",
            reported_total=Decimal("42000000"),
            currency="USD",
            scale="millions",
        )
        approved = self._create_complete_segment(document.id, "Insurance")
        rejected = self._create_complete_segment(document.id, "Eliminations")
        self.repo.create_validation_issue(
            document_id=document.id,
            segment_id=approved.id,
            severity="warning",
            issue_type="scale_check",
            message="Scale inferred from table header.",
        )
        self.review.approve_segment_row(
            document_id=document.id,
            segment_id=approved.id,
            reviewer="analyst@example.com",
            note="Looks good.",
        )
        self.review.reject_segment_row(
            document_id=document.id,
            segment_id=rejected.id,
            reviewer="analyst@example.com",
            note="Not a reportable segment.",
        )
        self.review.approve_document(
            document_id=document.id,
            reviewer="analyst@example.com",
            note="Ready for final export.",
        )
        return document.id, approved.id, rejected.id

    def _create_complete_segment(self, document_id: str, segment_name: str):
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
            status=SEGMENT_STATUS_APPROVED,
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
