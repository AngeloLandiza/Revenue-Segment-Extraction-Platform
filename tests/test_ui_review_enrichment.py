from __future__ import annotations

import unittest

from fitch_extractor.models import ParsedPage, utc_now
from fitch_extractor.ui.review import _row_needs_nace_mapping
from streamlit_app import (
    _locate_esg_evidence_bbox,
    _page_number_from_ref,
    _provider_name_from_label,
    _strip_selection_column,
)
from tests.fixtures import build_segment_row


class UiReviewEnrichmentTest(unittest.TestCase):
    def test_nace_review_progress_excludes_income_proceeds_rows_without_stored_row_type(self) -> None:
        row = build_segment_row(segment_name="Business interruption proceeds")

        self.assertFalse(_row_needs_nace_mapping(row))

    def test_nace_review_progress_includes_business_activity_rows(self) -> None:
        row = build_segment_row(segment_name="Distribution")

        self.assertTrue(_row_needs_nace_mapping(row))

    def test_esg_evidence_bbox_can_be_located_from_parsed_page(self) -> None:
        page = ParsedPage(
            id="page_fixture",
            document_id="doc_fixture",
            page_number=8,
            text="Renewable investment includes solar projects.",
            blocks_json={
                "blocks": [
                    {
                        "text": "Renewable investment includes solar projects.",
                        "bbox": {"x0": 10, "y0": 20, "x1": 200, "y1": 40},
                    }
                ]
            },
            tables_json={"tables": []},
            language="en",
            parser_sources=("pymupdf",),
            has_text=True,
            created_at=utc_now(),
        )

        bbox = _locate_esg_evidence_bbox(page, "Renewable investment includes solar")

        self.assertEqual({"x0": 10, "y0": 20, "x1": 200, "y1": 40}, bbox)

    def test_page_number_from_reference_uses_first_page_number(self) -> None:
        self.assertEqual(12, _page_number_from_ref("pp. 12-13"))
        self.assertIsNone(_page_number_from_ref("no page"))

    def test_provider_name_from_label_keeps_sidebar_mapping_stable(self) -> None:
        self.assertEqual(
            "fake",
            _provider_name_from_label("fake - deterministic local smoke test"),
        )

    def test_strip_selection_column_keeps_editor_payload_fields(self) -> None:
        rows = [{"id": "row_1", "selected": True, "segment_name": "Retail"}]

        self.assertEqual(
            [{"id": "row_1", "segment_name": "Retail"}],
            _strip_selection_column(rows),
        )


if __name__ == "__main__":
    unittest.main()
