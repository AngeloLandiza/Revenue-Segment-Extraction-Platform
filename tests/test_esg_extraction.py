from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError

from revenue_segment_extractor.exporting import ExportService
from revenue_segment_extractor.extraction import (
    EsgExtractionService,
    ExtractedEsgFactor,
    FakeRevenueExtractionProvider,
    link_esg_factor_to_segment,
    select_esg_candidate_pages,
    should_discard_esg_factor,
)
from revenue_segment_extractor.models import DOCUMENT_STATUS_APPROVED, SEGMENT_STATUS_APPROVED
from revenue_segment_extractor.persistence import (
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


class EsgExtractionTest(unittest.TestCase):
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

    def test_keyword_retrieval_selects_esg_pages_near_segments(self) -> None:
        document = self._create_document()
        segment = self._create_segment(document.id, "Renewables", page_ref="p. 10")
        self.repo.create_parsed_page(
            document_id=document.id,
            page_number=11,
            text="Renewables segment invested in solar and wind projects to reduce emissions.",
            blocks_json={},
            tables_json={},
            language="en",
            parser_sources=("fixture",),
            has_text=True,
        )
        self.repo.create_parsed_page(
            document_id=document.id,
            page_number=30,
            text="ESG index GRI index TCFD index governance cross-reference.",
            blocks_json={},
            tables_json={},
            language="en",
            parser_sources=("fixture",),
            has_text=True,
        )

        candidates = select_esg_candidate_pages(
            self.repo.list_parsed_pages(document.id),
            [segment],
        )

        self.assertEqual([11], [candidate.page.page_number for candidate in candidates])
        self.assertIn("renewable", candidates[0].matched_terms)

    def test_strict_esg_schema_rejects_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            ExtractedEsgFactor.model_validate(
                {
                    "factor_type": "renewable_investment",
                    "polarity": "positive",
                    "description": "Solar investment.",
                    "page_ref": "p. 5",
                    "evidence_text": "Solar investment.",
                    "confidence": 0.8,
                    "is_company_wide": False,
                    "segment_name": "Renewables",
                    "linked_business_activity": "renewable generation",
                    "linkage_rationale": "Evidence mentions Renewables.",
                    "extra": "not allowed",
                }
            )

    def test_fake_llm_esg_extraction_persists_segment_linked_factor(self) -> None:
        document = self._create_document()
        segment = self._create_segment(document.id, "Renewables", page_ref="p. 4")
        self.repo.create_parsed_page(
            document_id=document.id,
            page_number=5,
            text="The Renewables segment invested in new solar projects.",
            blocks_json={},
            tables_json={},
            language="en",
            parser_sources=("fixture",),
            has_text=True,
        )

        summary = EsgExtractionService(
            self.repo,
            FakeRevenueExtractionProvider(),
        ).extract_document(document.id)

        self.assertEqual(1, summary.persisted_factor_count)
        factor = summary.esg_factors[0]
        self.assertEqual(segment.id, factor.segment_id)
        self.assertEqual("renewable_investment", factor.factor_type)
        self.assertFalse(factor.is_company_wide)
        self.assertEqual("direct_segment_name", factor.segment_link_type)
        self.assertEqual("E", factor.esg_category)
        self.assertTrue(factor.score_relevant)

    def test_segment_linking_requires_explicit_segment_or_activity_match(self) -> None:
        document = self._create_document()
        renewable = self._create_segment(document.id, "Renewables")
        self._create_segment(document.id, "Retail")

        linked = link_esg_factor_to_segment(
            ExtractedEsgFactor(
                factor_type="renewable_investment",
                polarity="positive",
                description="Renewables segment added wind capacity.",
                page_ref="p. 8",
                evidence_text="Renewables segment added wind capacity.",
                confidence=0.8,
                is_company_wide=False,
                segment_name="Renewables",
                linked_business_activity="wind generation",
                linkage_rationale="The evidence names the Renewables segment.",
            ),
            self.repo.list_segment_rows(document.id),
        )
        uncertain = link_esg_factor_to_segment(
            ExtractedEsgFactor(
                factor_type="emissions_target",
                polarity="positive",
                description="The company set a net zero target.",
                page_ref="p. 9",
                evidence_text="The company set a net zero target.",
                confidence=0.8,
                is_company_wide=False,
                segment_name=None,
                linked_business_activity=None,
                linkage_rationale="No segment is named.",
            ),
            self.repo.list_segment_rows(document.id),
        )

        self.assertEqual(renewable.id, linked.id if linked else None)
        self.assertIsNone(uncertain)

    def test_esg_factor_does_not_link_to_total_row(self) -> None:
        document = self._create_document()
        self._create_segment(document.id, "Total revenue")

        linked = link_esg_factor_to_segment(
            ExtractedEsgFactor(
                factor_type="emissions_target",
                polarity="positive",
                description="Total revenue segment reduced emissions.",
                page_ref="p. 8",
                evidence_text="Total revenue segment reduced emissions.",
                confidence=0.8,
                is_company_wide=False,
                segment_name="Total revenue",
                linked_business_activity="reported total",
                linkage_rationale="The evidence names only the total row.",
                segment_link_type="direct_segment_name",
                esg_category="E",
                score_relevant=True,
                impact_mechanism="emissions_target",
            ),
            self.repo.list_segment_rows(document.id),
        )

        self.assertIsNone(linked)

    def test_company_wide_factor_is_stored_without_segment(self) -> None:
        document = self._create_document()
        self._create_segment(document.id, "Insurance")
        response = json.dumps(
            {
                "factors": [
                    {
                        "factor_type": "emissions_target",
                        "polarity": "positive",
                        "description": "The company set a net zero target.",
                        "page_ref": "p. 12",
                        "evidence_text": "The company set a net zero target.",
                        "confidence": 0.7,
                        "is_company_wide": True,
                        "segment_name": None,
                        "linked_business_activity": None,
                        "linkage_rationale": "The evidence is company-wide.",
                    }
                ],
                "extraction_warnings": [],
            }
        )
        self.repo.create_parsed_page(
            document_id=document.id,
            page_number=12,
            text="The company set a net zero target.",
            blocks_json={},
            tables_json={},
            language="en",
            parser_sources=("fixture",),
            has_text=True,
        )

        summary = EsgExtractionService(
            self.repo,
            FakeRevenueExtractionProvider(response),
        ).extract_document(document.id)

        self.assertEqual(1, summary.persisted_factor_count)
        self.assertIsNone(summary.esg_factors[0].segment_id)
        self.assertTrue(summary.esg_factors[0].is_company_wide)

    def test_generic_esg_boilerplate_is_rejected_when_unlinked(self) -> None:
        factor = ExtractedEsgFactor(
            factor_type="governance_policy",
            polarity="neutral",
            description="ESG index cross-reference to governance policy.",
            page_ref="p. 99",
            evidence_text="ESG index cross-reference to governance policy.",
            confidence=0.5,
            is_company_wide=True,
            segment_name=None,
            linked_business_activity=None,
            linkage_rationale="No segment linkage.",
        )

        self.assertTrue(should_discard_esg_factor(factor))

    def test_esg_review_actions_log_events_and_update_linkage(self) -> None:
        document = self._create_document()
        segment = self._create_segment(document.id, "Renewables")
        factor = self.repo.create_esg_factor(
            document_id=document.id,
            segment_id=None,
            factor_type="emissions_target",
            polarity="positive",
            description="Company target.",
            page_ref="p. 3",
            evidence_text="Company target.",
            confidence=0.7,
            is_company_wide=True,
        )

        self.review.relink_esg_factor(
            document_id=document.id,
            factor_id=factor.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
        )
        self.review.approve_esg_factor(
            document_id=document.id,
            factor_id=factor.id,
            reviewer="analyst@example.com",
        )

        updated = self.repo.get_esg_factor(factor.id)
        events = self.repo.list_review_events(document.id)
        self.assertEqual(segment.id, updated.segment_id if updated else None)
        self.assertIn("relink_esg_factor", [event.action for event in events])
        self.assertEqual("approved", events[-1].new_value)

    def test_export_includes_approved_esg_summary_and_full_audit_records(self) -> None:
        document = self._create_document(status=DOCUMENT_STATUS_APPROVED)
        segment = self._create_segment(document.id, "Renewables", status=SEGMENT_STATUS_APPROVED)
        self.repo.create_segment_evidence(
            segment_id=segment.id,
            document_id=document.id,
            page_number=4,
            snippet_text="Renewables revenue $42 million",
            bbox_json=None,
            parser_source="fixture",
            evidence_kind="table_row",
        )
        factor = self.repo.create_esg_factor(
            document_id=document.id,
            segment_id=segment.id,
            factor_type="renewable_investment",
            polarity="positive",
            description="Renewables invested in solar projects.",
            page_ref="p. 5",
            evidence_text="Renewables invested in solar projects.",
            confidence=0.9,
            is_company_wide=False,
        )
        self.review.approve_esg_factor(
            document_id=document.id,
            factor_id=factor.id,
            reviewer="analyst@example.com",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = ExportService(self.repo, temp_dir).export_document(document.id)
            with bundle.csv_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            payload = json.loads(bundle.json_path.read_text(encoding="utf-8"))

        self.assertIn("renewable_investment", rows[0]["esg_factor_summary"])
        self.assertEqual(1, len(payload["esg_factors"]))
        self.assertEqual("approved", payload["esg_factors"][0]["review_status"])

    def _create_document(self, *, status: str = "new"):
        return self.repo.create_document(
            company_name="Example Demo Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
            fiscal_period="FY2025",
            reported_total=Decimal("42000000"),
            currency="USD",
            scale="millions",
            status=status,
        )

    def _create_segment(
        self,
        document_id: str,
        segment_name: str,
        *,
        page_ref: str = "p. 1",
        status: str = "pending",
    ):
        return self.repo.create_segment_row(
            document_id=document_id,
            segment_name=segment_name,
            revenue_raw="$42 million",
            revenue_value=Decimal("42"),
            currency="USD",
            scale="millions",
            period_label="FY2025",
            normalized_value=Decimal("42000000"),
            page_ref=page_ref,
            section_ref="Revenue by segment",
            metric_basis="revenue",
            confidence=0.91,
            status=status,
            extraction_method="fixture",
        )


if __name__ == "__main__":
    unittest.main()
