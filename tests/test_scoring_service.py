from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from revenue_segment_extractor.exporting import ExportService
from revenue_segment_extractor.models import (
    DOCUMENT_STATUS_APPROVED,
    SEGMENT_STATUS_APPROVED,
    NaceSelection,
)
from revenue_segment_extractor.persistence import (
    ReviewService,
    SQLiteRepository,
    connect_database,
    initialize_database,
)
from revenue_segment_extractor.scoring import ScoringService, clamp_score


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


class ScoringServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect_database(":memory:")
        initialize_database(self.connection)
        self.repo = SQLiteRepository(
            self.connection,
            id_factory=SequentialIds(),
            clock=IncrementingClock(),
        )
        self.review = ReviewService(self.repo)
        self.service = ScoringService(self.repo)

    def tearDown(self) -> None:
        self.connection.close()

    def test_base_score_lookup_uses_exact_nace_code(self) -> None:
        lookup = self.service.base_score_for_segment(
            NaceSelection(
                segment_id="seg_001",
                nace_code="65.12",
                nace_label="Non-life insurance",
                nace_level=4,
                match_score=0.9,
                rationale="fixture",
                source="test",
                reviewer="analyst@example.com",
                updated_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )

        self.assertEqual(2.4, lookup.score)
        self.assertIn("65.12", lookup.rationale)

    def test_esg_adjustment_calculation_uses_reviewed_positive_factor(self) -> None:
        document = self._create_document(reported_total=Decimal("100"))
        segment = self._create_segment(document.id, "Renewables", Decimal("100"))
        factor = self.repo.create_esg_factor(
            document_id=document.id,
            segment_id=segment.id,
            factor_type="renewable_investment",
            polarity="positive",
            description="Renewables invested in solar projects.",
            page_ref="p. 20",
            evidence_text="Renewables invested in solar projects.",
            confidence=0.9,
            is_company_wide=False,
        )
        self.review.approve_esg_factor(
            document_id=document.id,
            factor_id=factor.id,
            reviewer="analyst@example.com",
        )

        result = self.service.score_document(document.id)

        self.assertEqual(-0.5, result.segment_scores[0].adjustment_score)
        rationale = json.loads(result.segment_scores[0].rationale or "{}")
        self.assertEqual(factor.id, rationale["esg_adjustments"][0]["factor_id"])

    def test_similar_esg_factors_are_clustered_before_scoring(self) -> None:
        document = self._create_document(reported_total=Decimal("100"))
        segment = self._create_segment(document.id, "Renewables", Decimal("100"))
        first = self.repo.create_esg_factor(
            document_id=document.id,
            segment_id=segment.id,
            factor_type="renewable_investment",
            polarity="positive",
            description="Renewables invested in solar projects.",
            page_ref="p. 20",
            evidence_text="Renewables invested in solar projects.",
            confidence=0.9,
            is_company_wide=False,
            impact_mechanism="decarbonization_supply_chain",
        )
        second = self.repo.create_esg_factor(
            document_id=document.id,
            segment_id=segment.id,
            factor_type="renewable_investment",
            polarity="positive",
            description="Renewables reduced supplier carbon footprint.",
            page_ref="p. 20",
            evidence_text="Renewables reduced supplier carbon footprint.",
            confidence=0.9,
            is_company_wide=False,
            impact_mechanism="decarbonization_supply_chain",
        )
        for factor in (first, second):
            self.review.approve_esg_factor(
                document_id=document.id,
                factor_id=factor.id,
                reviewer="analyst@example.com",
            )

        result = self.service.score_document(document.id)

        self.assertEqual(-0.5, result.segment_scores[0].adjustment_score)
        rationale = json.loads(result.segment_scores[0].rationale or "{}")
        self.assertEqual(2, len(rationale["esg_adjustments"][0]["clustered_factor_ids"]))

    def test_esg_adjustment_is_capped_and_company_wide_factors_do_not_score(self) -> None:
        document = self._create_document(reported_total=Decimal("100"))
        segment = self._create_segment(document.id, "Renewables", Decimal("100"))
        factors = [
            self.repo.create_esg_factor(
                document_id=document.id,
                segment_id=segment.id,
                factor_type="renewable_investment",
                polarity="positive",
                description=f"Distinct segment-specific positive factor {index}.",
                page_ref=f"p. {20 + index}",
                evidence_text=f"Distinct segment-specific positive factor {index}.",
                confidence=0.9,
                is_company_wide=False,
                impact_mechanism=f"mechanism_{index}",
            )
            for index in range(3)
        ]
        company_wide = self.repo.create_esg_factor(
            document_id=document.id,
            segment_id=segment.id,
            factor_type="emissions_target",
            polarity="positive",
            description="Company-wide net zero target.",
            page_ref="p. 99",
            evidence_text="Company-wide net zero target.",
            confidence=0.9,
            is_company_wide=True,
            score_relevant=False,
        )
        for factor in (*factors, company_wide):
            self.review.approve_esg_factor(
                document_id=document.id,
                factor_id=factor.id,
                reviewer="analyst@example.com",
            )

        result = self.service.score_document(document.id)

        self.assertEqual(-0.75, result.segment_scores[0].adjustment_score)
        rationale = json.loads(result.segment_scores[0].rationale or "{}")
        self.assertTrue(rationale["esg_adjustments"][-1]["cap_applied"])

    def test_score_caps_keep_segment_score_inside_configured_scale(self) -> None:
        self.assertEqual(1.0, clamp_score(0.2, 1.0, 5.0))
        self.assertEqual(5.0, clamp_score(6.2, 1.0, 5.0))

    def test_revenue_weight_uses_document_total_denominator(self) -> None:
        document = self._create_document(reported_total=Decimal("200"))
        self._create_segment(document.id, "Insurance", Decimal("50"))

        result = self.service.score_document(document.id)

        self.assertEqual(Decimal("200"), result.denominator_value)
        self.assertEqual("document_reported_total", result.denominator_source)
        self.assertEqual(0.25, result.segment_scores[0].weight_share)

    def test_total_row_is_excluded_and_can_supply_denominator(self) -> None:
        document = self._create_document(reported_total=None)
        first = self._create_segment(document.id, "Retail", Decimal("60"))
        second = self._create_segment(document.id, "Services", Decimal("40"))
        total = self._create_segment(document.id, "Total revenue", Decimal("100"))

        result = self.service.score_document(document.id)

        self.assertEqual("reviewed_total_row", result.denominator_source)
        self.assertEqual({first.id, second.id}, {score.segment_id for score in result.segment_scores})
        self.assertIn(total.id, result.excluded_segment_ids)
        self.assertEqual([0.6, 0.4], [score.weight_share for score in result.segment_scores])

    def test_company_score_is_weighted_average_of_segment_scores(self) -> None:
        document = self._create_document(reported_total=None)
        power = self._create_segment(document.id, "Power", Decimal("75"))
        insurance = self._create_segment(document.id, "Insurance", Decimal("25"))
        self.repo.upsert_nace_selection(
            segment_id=power.id,
            nace_code="35.11",
            nace_label="Production of electricity",
            nace_level=4,
            match_score=0.9,
            rationale="fixture",
            source="test",
            reviewer="analyst@example.com",
        )
        self.repo.upsert_nace_selection(
            segment_id=insurance.id,
            nace_code="65.12",
            nace_label="Non-life insurance",
            nace_level=4,
            match_score=0.9,
            rationale="fixture",
            source="test",
            reviewer="analyst@example.com",
        )

        result = self.service.score_document(document.id)

        self.assertIsNotNone(result.company_score)
        assert result.company_score is not None
        self.assertEqual(3.825, result.company_score.weighted_average_score)
        self.assertEqual(1.0, result.company_score.included_weight_share)

    def test_missing_nace_uses_default_base_score(self) -> None:
        document = self._create_document(reported_total=Decimal("100"))
        self._create_segment(document.id, "Unmapped Segment", Decimal("100"))

        result = self.service.score_document(document.id)

        self.assertEqual(3.0, result.segment_scores[0].base_score)
        rationale = json.loads(result.segment_scores[0].rationale or "{}")
        self.assertIn("No NACE code selected", rationale["base_score_rationale"])

    def test_export_includes_score_fields(self) -> None:
        document = self._create_document(
            reported_total=Decimal("100"),
            status=DOCUMENT_STATUS_APPROVED,
        )
        segment = self._create_segment(document.id, "Insurance", Decimal("100"))
        self.repo.upsert_nace_selection(
            segment_id=segment.id,
            nace_code="65.12",
            nace_label="Non-life insurance",
            nace_level=4,
            match_score=0.9,
            rationale="fixture",
            source="test",
            reviewer="analyst@example.com",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = ExportService(self.repo, temp_dir).export_document(document.id)
            with bundle.csv_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual("2.4", rows[0]["base_score"])
        self.assertEqual("2.4", rows[0]["segment_score"])
        self.assertEqual("1.0", rows[0]["weight_share"])
        self.assertIn("Prototype demo score only", rows[0]["scoring_model_label"])

    def _create_document(
        self,
        *,
        reported_total: Decimal | None,
        status: str = "new",
    ):
        return self.repo.create_document(
            company_name="Example Demo Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
            fiscal_period="FY2025",
            reported_total=reported_total,
            currency="USD",
            scale="ones",
            status=status,
        )

    def _create_segment(self, document_id: str, segment_name: str, normalized_value: Decimal):
        segment = self.repo.create_segment_row(
            document_id=document_id,
            segment_name=segment_name,
            revenue_raw=str(normalized_value),
            revenue_value=normalized_value,
            currency="USD",
            scale="ones",
            period_label="FY2025",
            normalized_value=normalized_value,
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
            snippet_text=f"{segment_name} revenue {normalized_value}",
            bbox_json=None,
            parser_source="fixture",
            evidence_kind="table_row",
        )
        return segment


if __name__ == "__main__":
    unittest.main()
