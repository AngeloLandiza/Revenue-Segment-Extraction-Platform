from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from revenue_segment_extractor.ingestion import (
    FakePageTextFallbackProvider,
    PdfIngestionService,
    infer_document_metadata,
    is_low_text_page,
    locate_evidence_snippet,
    parse_pdf,
    render_page_to_png,
    render_page_with_bbox_to_png,
    score_pages,
    select_candidate_pages,
)
from revenue_segment_extractor.persistence import SQLiteRepository, connect_database, initialize_database


class PdfIngestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pdf_path = Path(self.temp_dir.name) / "sample-10k.pdf"
        _write_sample_pdf(self.pdf_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_extracts_text_blocks_tables_and_no_text_pages(self) -> None:
        pages = parse_pdf(self.pdf_path)

        self.assertEqual(4, len(pages))
        self.assertTrue(pages[0].has_text)
        self.assertIn("Annual report overview", pages[0].text)
        self.assertEqual("unknown", pages[0].language)

        first_block = pages[1].blocks_json["blocks"][0]
        self.assertIn("bbox", first_block)
        self.assertTrue({"x0", "y0", "x1", "y1"}.issubset(first_block["bbox"]))

        self.assertGreaterEqual(len(pages[1].tables_json["tables"]), 1)
        table = pages[1].tables_json["tables"][0]
        self.assertGreaterEqual(len(table["rows"]), 3)
        self.assertIn("External revenue", pages[1].text)

        self.assertFalse(pages[3].has_text)
        self.assertEqual("", pages[3].text)
        self.assertEqual(
            "available_not_configured",
            pages[3].blocks_json["text_fallback"]["status"],
        )

    def test_low_text_detection_uses_alphanumeric_threshold(self) -> None:
        self.assertTrue(is_low_text_page("   123   "))
        self.assertFalse(is_low_text_page("Revenue by operating segment was $120 million."))

    def test_fake_fallback_provider_interface_returns_source_metadata(self) -> None:
        provider = FakePageTextFallbackProvider({4: "Segmentos operativos\nIngresos 120"})

        result = provider.extract_text(self.pdf_path, 4)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("ocr", result.parser_source)
        self.assertEqual("fake_page_text_fallback", result.provider_name)
        self.assertEqual([(self.pdf_path, 4)], provider.calls)

    def test_parse_pdf_persists_fallback_text_source_and_language(self) -> None:
        provider = FakePageTextFallbackProvider(
            {
                4: (
                    "Segmentos operativos\n"
                    "Ingresos por segmento y ventas totales, USD millones\n"
                    "Comercial 120"
                )
            }
        )

        pages = parse_pdf(self.pdf_path, fallback_provider=provider)

        fallback_page = pages[3]
        self.assertTrue(fallback_page.has_text)
        self.assertIn("Ingresos por segmento", fallback_page.text)
        self.assertIn("ocr", fallback_page.parser_sources)
        self.assertEqual("es", fallback_page.language)
        self.assertEqual("applied", fallback_page.blocks_json["text_fallback"]["status"])
        self.assertEqual("ocr", fallback_page.blocks_json["text_fallback"]["parser_source"])
        fallback_blocks = [
            block
            for block in fallback_page.blocks_json["blocks"]
            if block.get("parser_source") == "ocr"
        ]
        self.assertEqual(1, len(fallback_blocks))

    def test_ingestion_persists_fallback_text(self) -> None:
        connection = connect_database(":memory:")
        initialize_database(connection)
        repository = SQLiteRepository(connection)
        provider = FakePageTextFallbackProvider(
            {
                4: (
                    "Note 5 - Operating segments\n"
                    "Revenue by segment, USD millions\n"
                    "Industrial $42"
                )
            },
            parser_source="vision",
        )
        try:
            summary = PdfIngestionService(repository, fallback_provider=provider).ingest_pdf(
                pdf_path=self.pdf_path,
                company_name="Example Demo Co.",
                candidate_limit=5,
            )

            stored_pages = repository.list_parsed_pages(summary.document.id)
            fallback_page = next(page for page in stored_pages if page.page_number == 4)
            self.assertEqual((), summary.no_text_pages)
            self.assertIn("Industrial $42", fallback_page.text)
            self.assertIn("vision", fallback_page.parser_sources)
            self.assertEqual("applied", fallback_page.blocks_json["text_fallback"]["status"])
        finally:
            connection.close()

    def test_scores_multilingual_revenue_segment_terms(self) -> None:
        class PageFixture:
            page_number = 1
            text = (
                "Secteurs operationnels\n"
                "Chiffre d affaires par secteur et total produits, EUR millions\n"
                "Distribution 120"
            )
            tables_json = {"tables": []}

        score = score_pages([PageFixture()])[0]

        self.assertGreater(score.relevance_score, 8)
        matched_terms = {match["term"] for match in score.matched_signals["terms"]}
        self.assertIn("chiffre d affaires par secteur", matched_terms)
        self.assertIn("secteurs operationnels", matched_terms)

    def test_scores_candidate_pages_and_includes_adjacent_pages(self) -> None:
        pages = parse_pdf(self.pdf_path)
        scores = score_pages(pages)
        candidates = select_candidate_pages(scores, limit=5)
        candidate_page_numbers = [candidate.page_number for candidate in candidates]

        self.assertEqual(2, candidate_page_numbers[0])
        self.assertIn(3, candidate_page_numbers)
        page_two = next(candidate for candidate in candidates if candidate.page_number == 2)
        self.assertGreater(page_two.relevance_score, 10)
        self.assertIn("terms", page_two.matched_signals)

        page_three = next(candidate for candidate in candidates if candidate.page_number == 3)
        self.assertIn("proximity", page_three.matched_signals)

    def test_locates_approximate_evidence_bbox(self) -> None:
        pages = parse_pdf(self.pdf_path)

        matches = locate_evidence_snippet(pages[1], "External revenue USD millions")

        self.assertGreaterEqual(len(matches), 1)
        self.assertEqual(2, matches[0]["page_number"])
        self.assertIsNotNone(matches[0]["bbox"])

    def test_renders_page_to_png_for_ui_evidence(self) -> None:
        output_path = Path(self.temp_dir.name) / "page-2.png"

        rendered = render_page_to_png(self.pdf_path, 2, output_path)

        self.assertEqual(output_path, rendered)
        self.assertTrue(output_path.exists())
        self.assertGreater(output_path.stat().st_size, 0)

    def test_renders_page_to_png_with_evidence_bbox_highlight(self) -> None:
        output_path = Path(self.temp_dir.name) / "page-2-highlight.png"
        bbox = {"x0": 45, "y0": 120, "x1": 260, "y1": 190}

        rendered = render_page_with_bbox_to_png(self.pdf_path, 2, output_path, bbox)

        self.assertEqual(output_path, rendered)
        self.assertTrue(output_path.exists())
        self.assertGreater(output_path.stat().st_size, 0)

    def test_ingestion_persists_pages_and_candidates(self) -> None:
        connection = connect_database(":memory:")
        initialize_database(connection)
        repository = SQLiteRepository(connection)
        try:
            summary = PdfIngestionService(repository).ingest_pdf(
                pdf_path=self.pdf_path,
                company_name="Example Demo Co.",
                candidate_limit=5,
            )

            stored_pages = repository.list_parsed_pages(summary.document.id)
            stored_candidates = repository.list_page_candidates(summary.document.id)

            self.assertEqual(4, summary.page_count)
            self.assertEqual(4, len(stored_pages))
            self.assertEqual((4,), summary.no_text_pages)
            self.assertEqual(summary.candidate_count, len(stored_candidates))
            self.assertIn(2, [candidate.page_number for candidate in stored_candidates])
            self.assertIn(3, [candidate.page_number for candidate in stored_candidates])
        finally:
            connection.close()

    def test_infers_metadata_when_ingestion_hints_are_blank(self) -> None:
        connection = connect_database(":memory:")
        initialize_database(connection)
        repository = SQLiteRepository(connection)
        try:
            summary = PdfIngestionService(repository).ingest_pdf(
                pdf_path=self.pdf_path,
                candidate_limit=5,
            )

            self.assertEqual("Example Demo Co.", summary.document.company_name)
            self.assertEqual("FY2025", summary.document.fiscal_period)
            self.assertEqual("USD", summary.document.currency)
            self.assertEqual("millions", summary.document.scale)
        finally:
            connection.close()

    def test_manual_metadata_hints_override_inferred_values(self) -> None:
        connection = connect_database(":memory:")
        initialize_database(connection)
        repository = SQLiteRepository(connection)
        try:
            summary = PdfIngestionService(repository).ingest_pdf(
                pdf_path=self.pdf_path,
                company_name="Manual Co.",
                fiscal_period="FY2024",
                currency="EUR",
                scale="thousands",
                candidate_limit=5,
            )

            self.assertEqual("Manual Co.", summary.document.company_name)
            self.assertEqual("FY2024", summary.document.fiscal_period)
            self.assertEqual("EUR", summary.document.currency)
            self.assertEqual("thousands", summary.document.scale)
        finally:
            connection.close()

    def test_metadata_inference_does_not_guess_low_confidence_company_name(self) -> None:
        pages = parse_pdf(self.pdf_path)
        redacted_pages = [
            type(
                "PageFixture",
                (),
                {
                    "page_number": page.page_number,
                    "text": page.text.replace("Example Demo Co.", "Annual report overview"),
                },
            )()
            for page in pages
        ]

        inferred = infer_document_metadata(redacted_pages)

        self.assertIsNone(inferred.company_name)


def _write_sample_pdf(path: Path) -> None:
    document = fitz.open()

    overview_page = document.new_page(width=612, height=792)
    overview_page.insert_text(
        (72, 72),
        "Example Demo Co.\nAnnual Report 2025\nAnnual report overview\n"
        "This page describes governance and strategy.",
        fontsize=11,
    )

    segment_page = document.new_page(width=612, height=792)
    segment_page.insert_text((72, 72), "Note 4 - Operating Segments", fontsize=14)
    segment_page.insert_text(
        (72, 96),
        "Revenue by segment and external revenue, USD millions",
        fontsize=11,
    )
    _draw_table(
        segment_page,
        x=72,
        y=130,
        column_widths=[150, 150, 150],
        row_height=28,
        rows=[
            ["Segment", "External revenue", "Total"],
            ["Commercial", "$120", "$120"],
            ["Consumer", "$180", "$180"],
            ["Total", "$300", "$300"],
        ],
    )

    continuation_page = document.new_page(width=612, height=792)
    continuation_page.insert_text(
        (72, 72),
        "Segment reporting continued\nCommercial and Consumer business lines reconcile to total revenue of $300 million.",
        fontsize=11,
    )

    document.new_page(width=612, height=792)
    document.save(path)
    document.close()


def _draw_table(
    page: fitz.Page,
    *,
    x: float,
    y: float,
    column_widths: list[float],
    row_height: float,
    rows: list[list[str]],
) -> None:
    table_width = sum(column_widths)
    table_height = row_height * len(rows)

    for row_index in range(len(rows) + 1):
        y_pos = y + row_index * row_height
        page.draw_line((x, y_pos), (x + table_width, y_pos), width=0.8)

    x_pos = x
    page.draw_line((x_pos, y), (x_pos, y + table_height), width=0.8)
    for width in column_widths:
        x_pos += width
        page.draw_line((x_pos, y), (x_pos, y + table_height), width=0.8)

    for row_index, row in enumerate(rows):
        cell_x = x
        for column_index, cell_text in enumerate(row):
            cell_rect = fitz.Rect(
                cell_x + 4,
                y + row_index * row_height + 7,
                cell_x + column_widths[column_index] - 4,
                y + (row_index + 1) * row_height - 4,
            )
            page.insert_textbox(cell_rect, cell_text, fontsize=9)
            cell_x += column_widths[column_index]


if __name__ == "__main__":
    unittest.main()
