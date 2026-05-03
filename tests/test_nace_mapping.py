from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from revenue_segment_extractor.exporting import ExportService
from revenue_segment_extractor.extraction.config import DEFAULT_EXTRACTION_MODEL
from revenue_segment_extractor.extraction.providers import FakeRevenueExtractionProvider
from revenue_segment_extractor.models import DOCUMENT_STATUS_APPROVED, SEGMENT_STATUS_APPROVED
from revenue_segment_extractor.nace import NaceMappingService, load_nace_nodes, retrieve_nace_candidates
from revenue_segment_extractor.nace.reference import NaceNode
from revenue_segment_extractor.nace.rerank import rerank_nace_candidates
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


class NaceMappingTest(unittest.TestCase):
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

    def test_reference_loading_detects_header_after_instruction_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nace.csv"
            _write_nace_csv(path)

            nodes = load_nace_nodes(path)

        self.assertEqual(4, len(nodes))
        self.assertEqual("section", nodes[0].level)
        self.assertEqual("65.12", nodes[-1].code)
        self.assertEqual(("K", "65", "65.1", "65.12"), nodes[-1].hierarchy_path_codes)
        self.assertEqual(5, nodes[-1].source_row_number)

    def test_mapping_service_defaults_to_configured_extraction_model(self) -> None:
        service = NaceMappingService(self.repo, reference_nodes=_sample_nodes())

        self.assertEqual(DEFAULT_EXTRACTION_MODEL, service.model)

    def test_candidate_generation_uses_text_fuzzy_and_keyword_overlap(self) -> None:
        nodes = _sample_nodes()

        candidates = retrieve_nace_candidates(
            nodes,
            segment_name="Insurance underwriting",
            evidence_text="Non-life insurance premium revenue.",
            limit=3,
        )

        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual("65.12", candidates[0].node.code)
        self.assertGreater(candidates[0].score, 0)
        self.assertIn("insurance", candidates[0].rationale.lower())

    def test_invalid_invented_llm_codes_are_rejected(self) -> None:
        candidates = retrieve_nace_candidates(
            _sample_nodes(),
            segment_name="Insurance",
            evidence_text="Insurance premium revenue.",
            limit=3,
        )
        provider = FakeRevenueExtractionProvider(
            response_text=json.dumps(
                {
                    "ranked_candidates": [
                        {
                            "code": "99.99",
                            "rank": 1,
                            "rationale": "Invented code should be rejected.",
                        }
                    ]
                }
            )
        )

        with self.assertRaises(ValueError):
            rerank_nace_candidates(
                segment_name="Insurance",
                evidence_text="Insurance premium revenue.",
                context="",
                candidates=candidates,
                provider=provider,
            )

    def test_fake_llm_reranking_preserves_reference_candidates(self) -> None:
        candidates = retrieve_nace_candidates(
            _sample_nodes(),
            segment_name="Insurance",
            evidence_text="Insurance premium revenue.",
            limit=3,
        )
        provider = FakeRevenueExtractionProvider(
            response_text=json.dumps(
                {
                    "ranked_candidates": [
                        {
                            "code": candidates[0].node.code,
                            "rank": 1,
                            "rationale": "Best supported candidate.",
                        }
                    ]
                }
            )
        )

        reranked = rerank_nace_candidates(
            segment_name="Insurance",
            evidence_text="Insurance premium revenue.",
            context="",
            candidates=candidates,
            provider=provider,
        )

        self.assertEqual(candidates[0].node.code, reranked[0].node.code)
        self.assertEqual("Best supported candidate.", reranked[0].rationale)

    def test_mapping_service_stores_top_three_candidates(self) -> None:
        document, segment = self._create_document_and_segment()
        self.repo.create_segment_evidence(
            segment_id=segment.id,
            document_id=document.id,
            page_number=12,
            snippet_text="Insurance, banking, and software segment context.",
            bbox_json=None,
            parser_source="fixture",
            evidence_kind="table_row",
        )

        result = NaceMappingService(
            self.repo,
            reference_nodes=_sample_nodes(),
            provider=FakeRevenueExtractionProvider(),
        ).map_segment(segment)

        stored = self.repo.list_nace_candidates(segment.id)
        self.assertGreaterEqual(len(stored), 3)
        self.assertLessEqual(len(stored), 5)
        self.assertEqual(tuple(stored), result.candidates)
        self.assertEqual(
            list(range(1, len(stored) + 1)),
            [candidate.rank for candidate in stored],
        )
        self.assertIsNotNone(self.repo.get_nace_selection(segment.id))
        self.assertEqual("mapped", result.decision)

    def test_llm_mapping_uses_nearby_context_for_power_generation_segment(self) -> None:
        document, segment = self._create_document_and_segment(segment_name="Offshore")
        self.repo.create_segment_evidence(
            segment_id=segment.id,
            document_id=document.id,
            page_number=12,
            snippet_text="2025 income statement DKKm Offshore Onshore Bioenergy & Other.",
            bbox_json=None,
            parser_source="fixture",
            evidence_kind="table_row",
        )
        self.repo.create_parsed_page(
            document_id=document.id,
            page_number=14,
            text=(
                "Revenue DKKm Offshore Onshore Bioenergy & Other. "
                "Generation of power and revenue from construction of wind farms."
            ),
            blocks_json={},
            tables_json={},
            language="en",
            parser_sources=("fixture",),
            has_text=True,
        )

        result = NaceMappingService(
            self.repo,
            reference_nodes=_sample_nodes(),
            provider=FakeRevenueExtractionProvider(),
        ).map_segment(segment)
        selection = self.repo.get_nace_selection(segment.id)

        self.assertEqual("mapped", result.decision)
        self.assertEqual("35.11", result.selected_code)
        self.assertEqual("35.11", selection.nace_code if selection else None)
        self.assertEqual("llm_candidate", selection.source if selection else None)

    def test_total_row_is_marked_not_applicable_and_auto_selection_is_cleared(self) -> None:
        document, segment = self._create_document_and_segment(segment_name="Total revenue")
        self.repo.upsert_nace_selection(
            segment_id=segment.id,
            nace_code="63.12",
            nace_label="Web portals",
            nace_level=4,
            match_score=0.2,
            rationale="Old deterministic auto-selection.",
            source="candidate",
        )

        result = NaceMappingService(
            self.repo,
            reference_nodes=_sample_nodes(),
            provider=FakeRevenueExtractionProvider(),
        ).map_segment(segment)

        self.assertEqual("not_applicable", result.decision)
        self.assertIsNone(self.repo.get_nace_selection(segment.id))
        self.assertEqual([], self.repo.list_nace_candidates(segment.id))

    def test_multilingual_total_row_is_not_nace_applicable(self) -> None:
        document, segment = self._create_document_and_segment(segment_name="I alt")

        result = NaceMappingService(
            self.repo,
            reference_nodes=_sample_nodes(),
            provider=FakeRevenueExtractionProvider(),
        ).map_segment(segment)

        self.assertEqual("not_applicable", result.decision)
        self.assertIsNone(result.selected_code)
        self.assertEqual([], self.repo.list_nace_candidates(segment.id))

    def test_mixed_segment_keeps_candidates_but_requires_review(self) -> None:
        document, segment = self._create_document_and_segment(segment_name="Bioenergy & Other")
        self.repo.create_segment_evidence(
            segment_id=segment.id,
            document_id=document.id,
            page_number=12,
            snippet_text=(
                "Bioenergy & Other includes CHP power generation, heat generation, "
                "and gas sales."
            ),
            bbox_json=None,
            parser_source="fixture",
            evidence_kind="table_row",
        )

        result = NaceMappingService(
            self.repo,
            reference_nodes=_sample_nodes(),
            provider=FakeRevenueExtractionProvider(),
        ).map_segment(segment)

        self.assertEqual("needs_review", result.decision)
        self.assertGreater(len(result.candidates), 0)
        self.assertIsNone(self.repo.get_nace_selection(segment.id))

    def test_existing_reference_code_outside_candidates_marks_segment_for_review(self) -> None:
        document, segment = self._create_document_and_segment(segment_name="Distribution")
        self.repo.create_segment_evidence(
            segment_id=segment.id,
            document_id=document.id,
            page_number=12,
            snippet_text="Distribution revenue from delivering electricity to customers.",
            bbox_json=None,
            parser_source="fixture",
            evidence_kind="table_row",
        )
        provider = FakeRevenueExtractionProvider(
            response_text=json.dumps(
                {
                    "decision": "mapped",
                    "selected_code": "65.12",
                    "confidence": 0.8,
                    "rationale": "Incorrectly returned an existing but unsupplied code.",
                    "ranked_candidates": [
                        {
                            "code": "65.12",
                            "rank": 1,
                            "confidence": 0.8,
                            "rationale": "Incorrect existing code.",
                        }
                    ],
                }
            )
        )

        result = NaceMappingService(
            self.repo,
            reference_nodes=_sample_nodes(),
            provider=provider,
        ).map_segment(segment)

        issues = self.repo.list_validation_issues(document.id)
        self.assertEqual("needs_review", result.decision)
        self.assertIsNone(result.selected_code)
        self.assertIsNone(self.repo.get_nace_selection(segment.id))
        self.assertTrue(
            any(
                issue.issue_type == "nace_mapping_candidate_validation"
                and "outside the supplied candidate set" in issue.message
                for issue in issues
            )
        )

    def test_utility_infrastructure_code_is_candidate_not_auto_selection_for_mixed_services(self) -> None:
        document, segment = self._create_document_and_segment(segment_name="Commercial services")
        self.repo.create_segment_evidence(
            segment_id=segment.id,
            document_id=document.id,
            page_number=12,
            snippet_text=(
                "Commercial services include electric vehicle charging infrastructure "
                "and electrical service upgrades."
            ),
            bbox_json=None,
            parser_source="fixture",
            evidence_kind="table_row",
        )

        result = NaceMappingService(
            self.repo,
            reference_nodes=_sample_nodes(),
            provider=FakeRevenueExtractionProvider(),
        ).map_segment(segment)

        self.assertIn("42.22", {candidate.nace_code for candidate in result.candidates})
        self.assertEqual("needs_review", result.decision)
        self.assertIsNone(self.repo.get_nace_selection(segment.id))

    def test_reviewer_override_is_preserved_when_mapping_reruns(self) -> None:
        document, segment = self._create_document_and_segment(segment_name="Total revenue")
        self.review.override_segment_nace(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
            nace_code="65.12",
            nace_label="Non-life insurance",
            nace_level=4,
            rationale="Reviewer override fixture.",
        )

        NaceMappingService(
            self.repo,
            reference_nodes=_sample_nodes(),
            provider=FakeRevenueExtractionProvider(),
        ).map_segment(segment)

        selection = self.repo.get_nace_selection(segment.id)
        self.assertEqual("65.12", selection.nace_code if selection else None)
        self.assertEqual("reviewer_override", selection.source if selection else None)

    def test_reviewer_override_is_stored_and_logged(self) -> None:
        document, segment = self._create_document_and_segment()

        selection = self.review.override_segment_nace(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
            nace_code="64.19",
            nace_label="Other monetary intermediation",
            nace_level=4,
            rationale="Reviewer determined the segment is banking.",
            note="Manual override after reading segment commentary.",
        )

        events = self.repo.list_review_events(document.id)
        self.assertEqual("64.19", selection.nace_code)
        self.assertEqual("reviewer_override", selection.source)
        self.assertEqual("override_nace_code", events[-1].action)
        self.assertEqual("nace_code", events[-1].field_changed)

    def test_export_includes_nace_fields(self) -> None:
        document, segment = self._create_document_and_segment(status=SEGMENT_STATUS_APPROVED)
        self.repo.create_segment_evidence(
            segment_id=segment.id,
            document_id=document.id,
            page_number=12,
            snippet_text="Insurance revenue $42 million",
            bbox_json=None,
            parser_source="fixture",
            evidence_kind="table_row",
        )
        self.review.override_segment_nace(
            document_id=document.id,
            segment_id=segment.id,
            reviewer="analyst@example.com",
            nace_code="65.12",
            nace_label="Non-life insurance",
            nace_level=4,
            match_score=0.91,
            rationale="Reviewer accepted non-life insurance mapping.",
        )
        self.review.approve_document(document_id=document.id, reviewer="analyst@example.com")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = ExportService(self.repo, temp_dir).export_document(document.id)
            with bundle.csv_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            audit = json.loads(bundle.json_path.read_text(encoding="utf-8"))

        self.assertEqual("65.12", rows[0]["nace_code"])
        self.assertEqual("Non-life insurance", rows[0]["nace_label"])
        self.assertEqual("4", rows[0]["nace_level"])
        self.assertEqual("0.91", rows[0]["nace_match_score"])
        self.assertEqual("65.12", audit["segment_rows"][0]["nace_selection"]["nace_code"])

    def _create_document_and_segment(
        self,
        *,
        segment_name: str = "Insurance",
        status: str = SEGMENT_STATUS_APPROVED,
    ):
        document = self.repo.create_document(
            company_name="Example Demo Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
            fiscal_period="FY2025",
            reported_total=Decimal("42000000"),
            currency="USD",
            scale="millions",
        )
        segment = self.repo.create_segment_row(
            document_id=document.id,
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
            status=status,
            extraction_method="fixture",
        )
        return document, segment


def _write_nace_csv(path: Path) -> None:
    rows = [
        ["NACE Rev.2 grouped hierarchy"],
        ["Use outline controls"],
        [],
        [
            "level_depth",
            "level",
            "node_code",
            "node_name",
            "node_key",
            "parent_key",
            "section_code",
            "section_name",
            "division_code",
            "division_name",
            "group_code",
            "group_name",
            "class_code",
            "class_name",
            "hierarchy_path_codes",
            "hierarchy_path_names",
            "source_row_number",
        ],
        [
            "1",
            "section",
            "K",
            "FINANCIAL AND INSURANCE ACTIVITIES",
            "section:K",
            "",
            "K",
            "FINANCIAL AND INSURANCE ACTIVITIES",
            "",
            "",
            "",
            "",
            "",
            "",
            "K",
            "FINANCIAL AND INSURANCE ACTIVITIES",
            "2",
        ],
        [
            "2",
            "division",
            "65",
            "Insurance, reinsurance and pension funding",
            "division:65",
            "section:K",
            "K",
            "FINANCIAL AND INSURANCE ACTIVITIES",
            "65",
            "Insurance, reinsurance and pension funding",
            "",
            "",
            "",
            "",
            "K | 65",
            "FINANCIAL AND INSURANCE ACTIVITIES | Insurance, reinsurance and pension funding",
            "3",
        ],
        [
            "3",
            "group",
            "65.1",
            "Insurance",
            "group:65.1",
            "division:65",
            "K",
            "FINANCIAL AND INSURANCE ACTIVITIES",
            "65",
            "Insurance, reinsurance and pension funding",
            "65.1",
            "Insurance",
            "",
            "",
            "K | 65 | 65.1",
            "FINANCIAL AND INSURANCE ACTIVITIES | Insurance, reinsurance and pension funding | Insurance",
            "4",
        ],
        [
            "4",
            "class",
            "65.12",
            "Non-life insurance",
            "class:65.12",
            "group:65.1",
            "K",
            "FINANCIAL AND INSURANCE ACTIVITIES",
            "65",
            "Insurance, reinsurance and pension funding",
            "65.1",
            "Insurance",
            "65.12",
            "Non-life insurance",
            "K | 65 | 65.1 | 65.12",
            (
                "FINANCIAL AND INSURANCE ACTIVITIES | Insurance, reinsurance and pension "
                "funding | Insurance | Non-life insurance"
            ),
            "5",
        ],
    ]
    with path.open("w", newline="", encoding="utf-8") as file:
        csv.writer(file).writerows(rows)


def _sample_nodes() -> tuple[NaceNode, ...]:
    return (
        _node(
            level_depth=1,
            level="section",
            code="K",
            label="FINANCIAL AND INSURANCE ACTIVITIES",
            path_codes=("K",),
            path_names=("FINANCIAL AND INSURANCE ACTIVITIES",),
        ),
        _node(
            level_depth=2,
            level="division",
            code="65",
            label="Insurance, reinsurance and pension funding",
            path_codes=("K", "65"),
            path_names=(
                "FINANCIAL AND INSURANCE ACTIVITIES",
                "Insurance, reinsurance and pension funding",
            ),
        ),
        _node(
            level_depth=4,
            level="class",
            code="65.12",
            label="Non-life insurance",
            path_codes=("K", "65", "65.1", "65.12"),
            path_names=(
                "FINANCIAL AND INSURANCE ACTIVITIES",
                "Insurance, reinsurance and pension funding",
                "Insurance",
                "Non-life insurance",
            ),
        ),
        _node(
            level_depth=4,
            level="class",
            code="64.19",
            label="Other monetary intermediation",
            path_codes=("K", "64", "64.1", "64.19"),
            path_names=(
                "FINANCIAL AND INSURANCE ACTIVITIES",
                "Financial service activities",
                "Monetary intermediation",
                "Other monetary intermediation",
            ),
        ),
        _node(
            level_depth=4,
            level="class",
            code="62.01",
            label="Computer programming activities",
            path_codes=("J", "62", "62.0", "62.01"),
            path_names=(
                "INFORMATION AND COMMUNICATION",
                "Computer programming, consultancy and related activities",
                "Computer programming, consultancy and related activities",
                "Computer programming activities",
            ),
        ),
        _node(
            level_depth=4,
            level="class",
            code="35.11",
            label="Production of electricity",
            path_codes=("D", "35", "35.1", "35.11"),
            path_names=(
                "ELECTRICITY, GAS, STEAM AND AIR CONDITIONING SUPPLY",
                "Electricity, gas, steam and air conditioning supply",
                "Electric power generation, transmission and distribution",
                "Production of electricity",
            ),
        ),
        _node(
            level_depth=4,
            level="class",
            code="35.13",
            label="Distribution of electricity",
            path_codes=("D", "35", "35.1", "35.13"),
            path_names=(
                "ELECTRICITY, GAS, STEAM AND AIR CONDITIONING SUPPLY",
                "Electricity, gas, steam and air conditioning supply",
                "Electric power generation, transmission and distribution",
                "Distribution of electricity",
            ),
        ),
        _node(
            level_depth=4,
            level="class",
            code="42.22",
            label="Construction of utility projects for electricity and telecommunications",
            path_codes=("F", "42", "42.2", "42.22"),
            path_names=(
                "CONSTRUCTION",
                "Civil engineering",
                "Construction of utility projects",
                "Construction of utility projects for electricity and telecommunications",
            ),
        ),
    )


def _node(
    *,
    level_depth: int,
    level: str,
    code: str,
    label: str,
    path_codes: tuple[str, ...],
    path_names: tuple[str, ...],
) -> NaceNode:
    return NaceNode(
        level_depth=level_depth,
        level=level,
        code=code,
        label=label,
        node_key=f"{level}:{code}",
        parent_key=None,
        section_code=path_codes[0],
        section_name=path_names[0],
        division_code=path_codes[1] if len(path_codes) > 1 else None,
        division_name=path_names[1] if len(path_names) > 1 else None,
        group_code=path_codes[2] if len(path_codes) > 2 else None,
        group_name=path_names[2] if len(path_names) > 2 else None,
        class_code=code if level == "class" else None,
        class_name=label if level == "class" else None,
        hierarchy_path_codes=path_codes,
        hierarchy_path_names=path_names,
        source_row_number=level_depth,
    )


if __name__ == "__main__":
    unittest.main()
