from __future__ import annotations

import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from revenue_segment_extractor.evaluation import (
    DocumentEvaluationContext,
    GoldRow,
    MatchThresholds,
    PredictedRow,
    RowMatch,
    classify_failure,
    compute_metrics,
    evaluate_rows,
    load_gold_files,
    load_prediction_files,
    page_refs_match,
    segment_similarity,
    values_match,
    write_reports,
)


class EvaluationMatchingTest(unittest.TestCase):
    def test_segment_similarity_normalizes_punctuation_and_case(self) -> None:
        self.assertGreater(segment_similarity("Renewables / Bioenergy", "renewables bioenergy"), 0.85)

    def test_values_match_uses_scaled_absolute_values(self) -> None:
        thresholds = MatchThresholds(value_absolute_tolerance=Decimal("10"))
        self.assertTrue(values_match(Decimal("42000000"), Decimal("42000005"), thresholds))
        self.assertFalse(values_match(Decimal("42000000"), Decimal("43000000"), thresholds))

    def test_page_refs_match_first_page_number(self) -> None:
        self.assertTrue(page_refs_match("p. 88", "page 88"))
        self.assertFalse(page_refs_match("p. 88", "page 89"))
        self.assertIsNone(page_refs_match("", "page 89"))

    def test_evaluate_rows_matches_wrong_value_for_failure_analysis(self) -> None:
        gold = [
            GoldRow(
                document_name="annual-report.pdf",
                company_name="Example Co",
                fiscal_period="FY2025",
                segment_name="Networks",
                revenue_value=Decimal("42"),
                currency="USD",
                scale="millions",
                page_ref="p. 12",
            )
        ]
        predicted = [
            PredictedRow(
                document_name="annual-report.pdf",
                company_name="Example Co",
                fiscal_period="2025",
                segment_name="Network operations",
                revenue_value=Decimal("41"),
                currency="USD",
                scale="millions",
                page_ref="p. 12",
                review_status="edited",
            )
        ]

        report = evaluate_rows(gold, predicted)

        self.assertEqual(1, report.metrics.matched_rows)
        self.assertEqual("wrong_value", report.matches[0].failure_type)
        self.assertEqual(0.0, report.metrics.precision)
        self.assertEqual(1.0, report.metrics.reviewer_edit_rate)


class EvaluationMetricsTest(unittest.TestCase):
    def test_compute_metrics_counts_precision_recall_and_document_context(self) -> None:
        gold = GoldRow(
            document_name="annual-report.pdf",
            company_name="Example Co",
            fiscal_period="FY2025",
            segment_name="Insurance",
            revenue_value=Decimal("42"),
            currency="USD",
            scale="millions",
            page_ref="p. 12",
        )
        predicted = PredictedRow(
            document_name="annual-report.pdf",
            company_name="Example Co",
            fiscal_period="FY2025",
            segment_name="Insurance",
            revenue_value=Decimal("42"),
            currency="USD",
            scale="millions",
            page_ref="p. 12",
            review_status="approved",
        )
        metrics = compute_metrics(
            [
                RowMatch(
                    gold=gold,
                    predicted=predicted,
                    score=1.0,
                    segment_similarity=1.0,
                    value_exact=True,
                    period_match=True,
                    page_ref_match=True,
                    status="matched",
                    failure_type=None,
                )
            ],
            {
                "annual-report.pdf": DocumentEvaluationContext(
                    validation_issue_count=2,
                    reconciliation_passed=True,
                    time_seconds=30.0,
                )
            },
        )

        self.assertEqual(1.0, metrics.precision)
        self.assertEqual(1.0, metrics.recall)
        self.assertEqual(1.0, metrics.f1)
        self.assertEqual(1.0, metrics.exact_value_accuracy)
        self.assertEqual(1.0, metrics.page_reference_accuracy)
        self.assertEqual(1.0, metrics.reconciliation_pass_rate)
        self.assertEqual(2.0, metrics.average_validation_issues_per_document)
        self.assertEqual(30.0, metrics.average_time_seconds_per_document)


class EvaluationFailureTaxonomyTest(unittest.TestCase):
    def test_notes_classify_non_english_and_ocr_failures(self) -> None:
        gold = GoldRow(
            document_name="annual-report.pdf",
            company_name="Example Co",
            fiscal_period="FY2025",
            segment_name="Networks",
            revenue_value=Decimal("42"),
            currency="USD",
            scale="millions",
            notes="Non-English table heading caused ambiguity.",
        )
        match = RowMatch(
            gold=gold,
            predicted=None,
            score=0,
            segment_similarity=0,
            value_exact=False,
            period_match=False,
            page_ref_match=None,
            status="missing",
            failure_type=None,
        )

        self.assertEqual("non_english_issue", classify_failure(match))

    def test_duplicate_prediction_classifies_duplicate_segment(self) -> None:
        predicted = PredictedRow(
            document_name="annual-report.pdf",
            company_name="Example Co",
            fiscal_period="FY2025",
            segment_name="Insurance",
            revenue_value=Decimal("42"),
            currency="USD",
            scale="millions",
        )
        match = RowMatch(
            gold=None,
            predicted=predicted,
            score=0,
            segment_similarity=0,
            value_exact=False,
            period_match=False,
            page_ref_match=None,
            status="extra",
            failure_type=None,
        )

        self.assertEqual("duplicate_segment", classify_failure(match, duplicate_predicted=True))


class EvaluationIntegrationTest(unittest.TestCase):
    def test_loads_files_and_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gold_path = root / "gold.csv"
            pred_dir = root / "export"
            pred_dir.mkdir()
            reports_dir = root / "reports"
            with gold_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=[
                        "document_name",
                        "company_name",
                        "fiscal_period",
                        "segment_name",
                        "revenue_value",
                        "currency",
                        "scale",
                        "page_ref",
                        "nace_code",
                        "notes",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "document_name": "annual-report.pdf",
                        "company_name": "Example Co",
                        "fiscal_period": "FY2025",
                        "segment_name": "Insurance",
                        "revenue_value": "42",
                        "currency": "USD",
                        "scale": "millions",
                        "page_ref": "p. 12",
                        "nace_code": "65.12",
                        "notes": "",
                    }
                )
            (pred_dir / "audit_export.json").write_text(
                json.dumps(
                    {
                        "document": {
                            "company_name": "Example Co",
                            "document_name": "annual-report.pdf",
                            "fiscal_period": "FY2025",
                            "created_at": "2026-01-01T00:00:00+00:00",
                        },
                        "segment_rows": [
                            {
                                "current_values": {
                                    "segment_name": "Insurance",
                                    "revenue_value": "42",
                                    "currency": "USD",
                                    "scale": "millions",
                                    "normalized_value": "42000000",
                                    "page_ref": "p. 12",
                                    "status": "approved",
                                },
                                "nace_selection": {"nace_code": "65.12"},
                                "review_events": [],
                            }
                        ],
                        "validation_issues": [],
                        "export_timestamp": "2026-01-01T00:01:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            gold_rows = load_gold_files([gold_path])
            prediction_input = load_prediction_files([pred_dir])
            report = evaluate_rows(
                gold_rows,
                list(prediction_input.rows),
                document_contexts=prediction_input.document_contexts,
            )
            write_reports(report, reports_dir)

            self.assertEqual(1.0, report.metrics.precision)
            self.assertEqual(60.0, report.metrics.average_time_seconds_per_document)
            self.assertTrue((reports_dir / "evaluation_summary.md").exists())
            self.assertTrue((reports_dir / "evaluation_results.csv").exists())
            self.assertTrue((reports_dir / "failure_analysis.md").exists())


if __name__ == "__main__":
    unittest.main()
