from __future__ import annotations

import unittest

from revenue_segment_extractor.api.schemas import (
    DocumentExportResponse,
    DocumentResponse,
    ExtractionSummaryResponse,
    IngestionSummaryResponse,
    SegmentRowResponse,
)
from revenue_segment_extractor.ingestion.service import IngestionSummary
from revenue_segment_extractor.models import PageCandidate
from tests.fixtures import build_document, build_segment_row


class ModelSerializationTest(unittest.TestCase):
    def test_internal_model_serializes_datetimes_and_decimals(self) -> None:
        document = build_document()

        serialized = document.to_dict()

        self.assertEqual("125000000", serialized["reported_total"])
        self.assertEqual("2026-01-02T03:04:05+00:00", serialized["created_at"])
        self.assertEqual("Example Demo Co.", serialized["company_name"])

    def test_api_schema_serializes_from_internal_model(self) -> None:
        segment = build_segment_row()

        response = SegmentRowResponse.model_validate(segment)
        serialized = response.model_dump(mode="json")

        self.assertEqual("seg_fixture", serialized["id"])
        self.assertEqual("Insurance", serialized["segment_name"])
        self.assertEqual("42", serialized["revenue_value"])
        self.assertEqual("42000000", serialized["normalized_value"])
        self.assertEqual("pending", serialized["status"])

    def test_api_response_fields_include_required_model_contract(self) -> None:
        document_fields = set(DocumentResponse.model_fields)
        segment_fields = set(SegmentRowResponse.model_fields)
        ingestion_fields = set(IngestionSummaryResponse.model_fields)
        extraction_fields = set(ExtractionSummaryResponse.model_fields)
        export_fields = set(DocumentExportResponse.model_fields)

        self.assertTrue(
            {
                "id",
                "company_name",
                "document_name",
                "source_path",
                "fiscal_period",
                "status",
                "reported_total",
                "currency",
                "scale",
                "created_at",
                "updated_at",
                "analysis_notes",
            }.issubset(document_fields)
        )
        self.assertTrue(
            {
                "id",
                "document_id",
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
                "status",
                "extraction_method",
                "created_at",
                "updated_at",
            }.issubset(segment_fields)
        )
        self.assertTrue(
            {
                "document",
                "page_count",
                "parsed_page_count",
                "candidate_count",
                "no_text_pages",
                "candidate_pages",
            }.issubset(ingestion_fields)
        )
        self.assertTrue(
            {
                "document",
                "prompt_version",
                "provider_name",
                "model",
                "candidate_page_count",
                "bundle_count",
                "extracted_row_count",
                "persisted_row_count",
                "validation_issue_count",
                "segment_rows",
                "validation_issues",
            }.issubset(extraction_fields)
        )
        self.assertTrue(
            {
                "document_id",
                "output_dir",
                "csv_path",
                "json_path",
                "xlsx_path",
                "exported_at",
                "records",
            }.issubset(export_fields)
        )

    def test_ingestion_summary_schema_serializes_candidates(self) -> None:
        document = build_document()
        candidate = PageCandidate(
            id="candidate_fixture",
            document_id=document.id,
            page_number=12,
            relevance_score=8.5,
            matched_signals_json={"terms": [{"term": "operating segments", "weight": 8.5}]},
            reason="matched terms: operating segments.",
        )
        summary = IngestionSummary(
            document=document,
            page_count=20,
            parsed_page_count=20,
            candidate_count=1,
            no_text_pages=(3,),
            candidate_pages=(candidate,),
        )

        serialized = IngestionSummaryResponse.model_validate(summary).model_dump(mode="json")

        self.assertEqual("doc_fixture", serialized["document"]["id"])
        self.assertEqual([3], serialized["no_text_pages"])
        self.assertEqual(12, serialized["candidate_pages"][0]["page_number"])


if __name__ == "__main__":
    unittest.main()
