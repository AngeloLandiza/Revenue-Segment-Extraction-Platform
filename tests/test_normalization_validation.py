from __future__ import annotations

import unittest
from decimal import Decimal

from fitch_extractor.extraction.normalization import (
    normalize_currency,
    normalize_page_reference,
    normalize_period_label,
    normalize_revenue_value,
    normalize_scale,
    normalize_extracted_row,
)
from fitch_extractor.extraction.schemas import ExtractedRevenueRow, RevenueExtractionOutput
from fitch_extractor.extraction.validation import (
    ValidationConfig,
    reconcile_totals,
    validate_normalized_rows,
)
from fitch_extractor.models import ParsedPage
from tests.fixtures import FIXED_TIME, build_document


class NormalizationTest(unittest.TestCase):
    def test_normalizes_currency_symbols_and_codes(self) -> None:
        self.assertEqual("USD", normalize_currency("$").value)
        self.assertEqual("EUR", normalize_currency("EUR").value)
        self.assertEqual("GBP", normalize_currency("£").value)
        self.assertEqual("SAR", normalize_currency("Saudi Riyals").value)

    def test_infers_currency_and_scale_from_page_context(self) -> None:
        output = RevenueExtractionOutput(
            company_name="Example Fitch Co.",
            document_name="sample.pdf",
            fiscal_period="FY2025",
            reported_total=None,
            currency=None,
            scale=None,
            rows=[],
            extraction_warnings=[],
        )
        row = ExtractedRevenueRow(
            segment_name="Retail",
            revenue_raw="5,544,986",
            revenue_value=None,
            currency=None,
            scale=None,
            period_label="FY2025",
            page_ref="p. 79",
            section_ref="Segment information",
            metric_basis="Total operating income",
            evidence_text="Retail total operating income 5,544,986",
            confidence=0.9,
            extraction_notes=None,
        )

        normalized = normalize_extracted_row(
            row,
            output,
            build_document(currency=None, scale=None, fiscal_period="FY2025"),
            page_context_text="All amounts are in Saudi Riyals thousands unless otherwise stated.",
        )

        self.assertEqual("SAR", normalized.currency)
        self.assertEqual("thousands", normalized.scale)

    def test_normalizes_scale_terms(self) -> None:
        self.assertEqual("actuals", normalize_scale("actuals").value)
        self.assertEqual("thousands", normalize_scale("$000").value)
        self.assertEqual("millions", normalize_scale("USDm").value)
        self.assertEqual("billions", normalize_scale("billions").value)

    def test_normalizes_negative_values_separators_and_decimals(self) -> None:
        self.assertEqual(Decimal("-1234.50"), normalize_revenue_value("(1,234.50)").value)
        self.assertEqual(Decimal("-1234"), normalize_revenue_value("1,234-").value)
        self.assertEqual(Decimal("1234.56"), normalize_revenue_value("$1,234.56").value)

    def test_dash_only_values_are_missing_not_zero(self) -> None:
        result = normalize_revenue_value("-")

        self.assertIsNone(result.value)
        self.assertIn("not treated as numeric zero", result.warnings[0].message)

    def test_em_dash_and_blank_values_are_missing(self) -> None:
        self.assertIsNone(normalize_revenue_value("\u2014").value)
        self.assertIsNone(normalize_revenue_value("").value)

    def test_normalizes_fiscal_period_labels_and_page_references(self) -> None:
        self.assertEqual("FY2025", normalize_period_label("Year ended December 31, 2025").value)
        self.assertEqual("FY2025", normalize_period_label("FY 2025").value)

        page_ref = normalize_page_reference("pages 12-13")
        self.assertEqual("pp. 12-13", page_ref.value)
        self.assertEqual(12, page_ref.page_number)


class ValidationTest(unittest.TestCase):
    def test_flags_missing_evidence_completeness(self) -> None:
        row = _normalized_row(page_ref=None)

        result = validate_normalized_rows(
            document=build_document(scale="millions"),
            rows=[row],
            parsed_pages=[],
        )

        self.assertIn("missing_page_reference", _issue_types(result))

    def test_flags_currency_scale_and_time_period_inconsistency(self) -> None:
        row = _normalized_row(currency="EUR", scale="billions", period_label="FY2024")

        result = validate_normalized_rows(
            document=build_document(currency="USD", scale="millions", fiscal_period="FY2025"),
            rows=[row],
            parsed_pages=[],
        )

        self.assertTrue(
            {"currency_mismatch", "scale_mismatch", "time_period_mismatch"}.issubset(
                _issue_types(result)
            )
        )

    def test_rejects_non_revenue_metric_basis_for_core_rows(self) -> None:
        invalid_metrics = ["Expenses", "Losses", "Assets", "Profit", "EBIT", "EBITDA"]
        for metric in invalid_metrics:
            with self.subTest(metric=metric):
                row = _normalized_row(metric_basis=metric, evidence_text=f"Commercial {metric} 120")

                result = validate_normalized_rows(
                    document=build_document(scale="millions"),
                    rows=[row],
                    parsed_pages=[],
                )

                self.assertIn(0, result.blocking_row_indexes)
                self.assertIn("invalid_metric_basis", _issue_types(result))

    def test_allows_valid_revenue_metric_with_broad_financial_table_evidence(self) -> None:
        row = _normalized_row(
            segment_name="Services",
            metric_basis="Revenues",
            evidence_text=(
                "Services revenues 19,649 income (loss) from continuing operations "
                "before taxes 4,200"
            ),
        )

        result = validate_normalized_rows(
            document=build_document(scale="millions"),
            rows=[row],
            parsed_pages=[],
        )

        self.assertNotIn("invalid_metric_basis", _issue_types(result))
        self.assertNotIn(0, result.blocking_row_indexes)

    def test_allows_revenue_reconciliation_row_with_loss_word_in_label(self) -> None:
        row = _normalized_row(
            segment_name="Hedging gains (losses)",
            revenue_raw="-127",
            metric_basis="Revenues",
            evidence_text="Hedging gains (losses) revenues (127)",
        )

        result = validate_normalized_rows(
            document=build_document(scale="millions"),
            rows=[row],
            parsed_pages=[],
        )

        self.assertNotIn("invalid_metric_basis", _issue_types(result))
        self.assertNotIn("consolidated_income_statement_line_item", _issue_types(result))

    def test_detects_duplicate_segments(self) -> None:
        rows = [
            _normalized_row(segment_name="Commercial", revenue_raw="100"),
            _normalized_row(segment_name="Commercial", revenue_raw="120"),
        ]

        result = validate_normalized_rows(
            document=build_document(scale="millions"),
            rows=rows,
            parsed_pages=[],
        )

        self.assertIn("duplicate_segment_candidate", _issue_types(result))

    def test_reconciles_totals_with_explicit_total_row(self) -> None:
        rows = [
            _normalized_row(segment_name="Commercial", revenue_raw="100", scale="actuals"),
            _normalized_row(segment_name="Consumer", revenue_raw="200", scale="actuals"),
            _normalized_row(segment_name="Total", revenue_raw="300", scale="actuals"),
        ]

        result = reconcile_totals(
            document=build_document(reported_total=None, scale="actuals"),
            rows=rows,
            config=ValidationConfig(absolute_tolerance=Decimal("0")),
        )

        self.assertEqual("matched", result.status)

    def test_reconciles_totals_without_explicit_total_row(self) -> None:
        rows = [
            _normalized_row(segment_name="Commercial", revenue_raw="100", scale="actuals"),
            _normalized_row(segment_name="Consumer", revenue_raw="200", scale="actuals"),
        ]

        result = reconcile_totals(
            document=build_document(reported_total=Decimal("300"), scale="actuals"),
            rows=rows,
            config=ValidationConfig(absolute_tolerance=Decimal("0")),
        )

        self.assertEqual("matched", result.status)

    def test_flags_total_reconciliation_mismatch(self) -> None:
        rows = [
            _normalized_row(segment_name="Commercial", revenue_raw="100", scale="actuals"),
            _normalized_row(segment_name="Consumer", revenue_raw="190", scale="actuals"),
            _normalized_row(segment_name="Total", revenue_raw="300", scale="actuals"),
        ]

        result = validate_normalized_rows(
            document=build_document(reported_total=None, scale="actuals"),
            rows=rows,
            parsed_pages=[],
            config=ValidationConfig(absolute_tolerance=Decimal("0")),
        )

        self.assertIn("total_reconciliation_mismatch", _issue_types(result))

    def test_dash_only_values_remain_non_numeric_for_validation(self) -> None:
        row = _normalized_row(segment_name="Eliminations", revenue_raw="-")

        result = validate_normalized_rows(
            document=build_document(scale="millions"),
            rows=[row],
            parsed_pages=[],
        )

        self.assertIsNone(row.normalized_value)
        self.assertIn("normalization_warning", _issue_types(result))

    def test_flags_declared_segment_coverage_gaps(self) -> None:
        rows = [_normalized_row(segment_name="Commercial"), _normalized_row(segment_name="Consumer")]
        page = _parsed_page_with_table(
            [
                ["Segment", "Revenue"],
                ["Commercial", "100"],
                ["Consumer", "200"],
                ["Industrial", "300"],
            ]
        )

        result = validate_normalized_rows(
            document=build_document(scale="millions"),
            rows=rows,
            parsed_pages=[page],
        )

        self.assertIn("potential_missing_segment", _issue_types(result))

    def test_rejects_consolidated_income_statement_false_positive(self) -> None:
        row = _normalized_row(
            segment_name="Cost of sales",
            metric_basis="Cost of sales",
            evidence_text="Cost of sales (90)",
        )

        result = validate_normalized_rows(
            document=build_document(scale="millions"),
            rows=[row],
            parsed_pages=[],
        )

        self.assertIn(0, result.blocking_row_indexes)
        self.assertIn("consolidated_income_statement_line_item", _issue_types(result))

    def test_allows_geography_or_product_segments_when_metric_is_revenue(self) -> None:
        rows = [
            _normalized_row(
                segment_name="EMEA",
                section_ref="Geographical segmentation",
                metric_basis="Revenue",
                evidence_text="Geographical segmentation revenue EMEA 120",
            ),
            _normalized_row(
                segment_name="Industrial products",
                section_ref="Product segmentation",
                metric_basis="Net sales",
                evidence_text="Product segmentation net sales Industrial products 80",
            ),
        ]

        result = validate_normalized_rows(
            document=build_document(scale="millions"),
            rows=rows,
            parsed_pages=[],
        )

        self.assertNotIn("invalid_metric_basis", _issue_types(result))
        self.assertNotIn("consolidated_income_statement_line_item", _issue_types(result))


def _normalized_row(
    *,
    segment_name: str = "Commercial",
    revenue_raw: str | None = "120",
    currency: str | None = "USD",
    scale: str | None = "millions",
    period_label: str | None = "FY2025",
    page_ref: str | None = "p. 2",
    section_ref: str | None = "Operating segments",
    metric_basis: str | None = "Revenue",
    evidence_text: str = "Commercial revenue 120",
):
    output = RevenueExtractionOutput(
        company_name="Example Fitch Co.",
        document_name="sample.pdf",
        fiscal_period="FY2025",
        reported_total=None,
        currency="USD",
        scale=scale,
        rows=[],
        extraction_warnings=[],
    )
    row = ExtractedRevenueRow(
        segment_name=segment_name,
        revenue_raw=revenue_raw,
        revenue_value=None,
        currency=currency,
        scale=scale,
        period_label=period_label,
        page_ref=page_ref,
        section_ref=section_ref,
        metric_basis=metric_basis,
        evidence_text=evidence_text,
        confidence=0.9,
        extraction_notes=None,
    )
    return normalize_extracted_row(
        row,
        output,
        build_document(currency="USD", scale=scale or "millions", fiscal_period="FY2025"),
    )


def _parsed_page_with_table(rows: list[list[str]]) -> ParsedPage:
    return ParsedPage(
        id="page_fixture",
        document_id="doc_fixture",
        page_number=2,
        text="Operating segments revenue table",
        blocks_json={"blocks": []},
        tables_json={"tables": [{"rows": rows}]},
        language="unknown",
        parser_sources=("pdfplumber",),
        has_text=True,
        created_at=FIXED_TIME,
    )


def _issue_types(result) -> set[str]:
    return {issue.issue_type for issue in result.issues}


if __name__ == "__main__":
    unittest.main()
