from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import fitz
from pydantic import ValidationError

from revenue_segment_extractor.extraction import (
    ExtractionSettings,
    FakeRevenueExtractionProvider,
    LLMExtractionResponse,
    RevenueExtractionOutput,
    RevenueExtractionService,
    select_extraction_candidates,
)
from revenue_segment_extractor.extraction.candidate_selection import is_table_of_contents_page
from revenue_segment_extractor.extraction.providers import LLMExtractionRequest
from revenue_segment_extractor.extraction.deduplication import PreparedRevenueRow, deduplicate_rows
from revenue_segment_extractor.extraction.periods import keep_latest_year_rows
from revenue_segment_extractor.extraction.prompts import build_first_pass_extraction_prompt
from revenue_segment_extractor.extraction.row_selection import keep_primary_table_rows
from revenue_segment_extractor.extraction.table_alignment import align_rows_to_preferred_metric
from revenue_segment_extractor.ingestion import FakePageTextFallbackProvider, PdfIngestionService
from revenue_segment_extractor.models import SEGMENT_STATUS_READY_FOR_REVIEW, PageCandidate, ParsedPage
from revenue_segment_extractor.persistence import SQLiteRepository, connect_database, initialize_database


@dataclass(frozen=True)
class PromptDocumentFixture:
    company_name: str = "Example Demo Co."
    document_name: str = "sample-10k.pdf"
    fiscal_period: str | None = "FY2025"
    reported_total: Decimal | None = None
    currency: str | None = "USD"
    scale: str | None = "millions"


@dataclass(frozen=True)
class PromptPageFixture:
    page_number: int = 2
    text: str = "Note 4 - Operating Segments\nRevenue by segment, USD millions"
    tables_json: dict | None = None

    def __post_init__(self) -> None:
        if self.tables_json is None:
            object.__setattr__(
                self,
                "tables_json",
                {
                    "tables": [
                        {
                            "rows": [
                                ["Segment", "External revenue", "Total"],
                                ["Commercial", "$120", "$120"],
                                ["Consumer", "$180", "$180"],
                            ]
                        }
                    ]
                },
            )


class RevenueExtractionTest(unittest.TestCase):
    def test_prompt_includes_strict_extraction_instructions_and_candidate_tables(self) -> None:
        prompt = build_first_pass_extraction_prompt(
            document=PromptDocumentFixture(),
            pages=[PromptPageFixture()],
        )

        self.assertIn("Prompt version: first_pass_revenue_segments_v1", prompt)
        self.assertIn("Return valid JSON only", prompt)
        self.assertIn("any language or regional reporting format", prompt)
        self.assertIn("business, reportable, or operating segments", prompt)
        self.assertIn("prefer the row that represents total segment revenue", prompt)
        self.assertIn("Avoid expenses, losses, assets, EBITDA, EBIT, profit", prompt)
        self.assertIn("product/geographical segmentation", prompt)
        self.assertIn("one row for each current-period column", prompt)
        self.assertIn("Do not return prior-year comparison columns", prompt)
        self.assertIn("consolidated income statement line item such as Revenue", prompt)
        self.assertIn("including eliminations, segment totals, reclassification-to-reported columns", prompt)
        self.assertIn("Use the exact segment/table column header as segment_name", prompt)
        self.assertIn("preserve revenue_raw exactly as '-'", prompt)
        self.assertIn("Prefer financial statement note segment disclosures", prompt)
        self.assertIn("Return one primary current-period segment table per document", prompt)
        self.assertIn("Do not extract ESG, EU taxonomy, climate-impact", prompt)
        self.assertIn("Preserve raw values exactly", prompt)
        self.assertIn("Preserve original official segment names exactly as shown", prompt)
        self.assertIn("Translate explanatory notes only when needed", prompt)
        self.assertIn("Produce JSON matching the schema exactly", prompt)
        self.assertIn("| Commercial | $120 | $120 |", prompt)
        self.assertIn('"extraction_warnings"', prompt)

    def test_prompt_includes_language_guidance_for_non_english_pages(self) -> None:
        prompt = build_first_pass_extraction_prompt(
            document=PromptDocumentFixture(currency="EUR", scale="millions"),
            pages=[
                PromptPageFixture(
                    text="Segmentos operativos\nIngresos por segmento, EUR millones",
                    tables_json={
                        "tables": [
                            {
                                "rows": [
                                    ["Segmento", "Ingresos"],
                                    ["Soluciones Energia", "120"],
                                ]
                            }
                        ]
                    },
                )
            ],
        )

        self.assertIn("Extract eligible values regardless of report language", prompt)
        self.assertIn("Do not translate segment labels", prompt)
        self.assertIn("Segmentos operativos", prompt)
        self.assertIn("| Soluciones Energia | 120 |", prompt)

    def test_extraction_candidate_selection_prefers_financial_statement_segment_notes(self) -> None:
        candidates = [
            _candidate(91, [{"term": "segment reporting", "weight": 7.5}]),
            _candidate(161, []),
            _candidate(256, [{"term": "operating segments", "weight": 8.5}]),
            _candidate(257, [{"term": "external revenue", "weight": 7.0}]),
            _candidate(258, [{"term": "external revenue", "weight": 7.0}]),
        ]
        pages = [
            _parsed_page(
                91,
                "Being a creditworthy company with solid returns\nSegment reporting\nPrimary segmentation",
            ),
            _parsed_page(
                161,
                "Environment\nEnergy usage by source and energy mix\nEU taxonomy revenue",
            ),
            _parsed_page(
                256,
                "Financial statements\nNote 2 Segment information\nAlliander distinguishes reporting segments.",
            ),
            _parsed_page(
                257,
                "Financial statements\nNotes\nPrimary segmentation\nExternal income by segment",
            ),
            _parsed_page(
                258,
                "Financial statements\nProduct segmentation\nExternal revenue by product",
            ),
        ]

        selected = select_extraction_candidates(candidates, pages)

        self.assertEqual([256, 257], [candidate.page_number for candidate in selected])

    def test_extraction_candidate_selection_keeps_structural_table_candidates(self) -> None:
        candidate = PageCandidate(
            id="candidate_12",
            document_id="doc_fixture",
            page_number=12,
            relevance_score=8.0,
            matched_signals_json={
                "table_density": {"table_count": 1, "cell_count": 18},
                "numeric_density": {"numeric_count": 8},
            },
            reason="Dense numeric table without English revenue-segment terms.",
        )
        pages = [
            _parsed_page(
                12,
                "Estados financieros consolidados\nNota 7\n"
                "Linea A 2025 100 2024 90\nLinea B 2025 200 2024 180",
            )
        ]

        selected = select_extraction_candidates([candidate], pages)

        self.assertEqual([12], [candidate.page_number for candidate in selected])

    def test_extraction_candidate_selection_excludes_table_of_contents_pages(self) -> None:
        toc_text = """
        1 - Finance
        C o n t e n t
        2
        Selected group key figures
        3
        Consolidated financial statements
        14
        Segment reporting
        19
        Real estate
        26
        Financing
        37
        Financial risk management
        67
        Report of the statutory auditor
        """
        candidates = [
            _candidate(219, [{"term": "segment reporting", "weight": 7.5}]),
            _candidate(232, [{"term": "segment reporting", "weight": 7.5}]),
        ]
        pages = [
            _parsed_page(219, toc_text),
            _parsed_page(
                232,
                "Consolidated financial statements\n4 Segment reporting\n"
                "The segment structure is based on internal reporting.",
            ),
        ]

        selected = select_extraction_candidates(candidates, pages)

        self.assertTrue(is_table_of_contents_page(toc_text))
        self.assertEqual([232], [candidate.page_number for candidate in selected])

    def test_repeated_10k_table_of_contents_header_is_not_toc_page(self) -> None:
        page_text = """
        Table of Contents
        Alphabet Inc.
        The following table presents revenue information about our segments (in millions):
        Year Ended December 31,
        2023
        2024
        2025
        Revenues:
        Google Services
        $
        342,721
        Google Cloud
        58,705
        """

        self.assertFalse(is_table_of_contents_page(page_text))

    def test_extraction_candidate_selection_excludes_accounting_update_pages(self) -> None:
        candidates = [
            _candidate(199, [{"term": "segment reporting", "weight": 7.5}]),
            _candidate(347, [{"term": "business segments", "weight": 7.0}]),
        ]
        pages = [
            _parsed_page(
                199,
                "Accounting and reporting developments\nFASB standards adopted\n"
                "Segment Reporting: Improvements to Reportable Segment Disclosures",
            ),
            _parsed_page(
                347,
                "Note 32 - Business segments & Corporate\n"
                "The Firm has three reportable business segments.",
            ),
        ]

        selected = select_extraction_candidates(candidates, pages)

        self.assertEqual([347], [candidate.page_number for candidate in selected])

    def test_strict_schema_accepts_valid_output_and_rejects_missing_or_extra_fields(self) -> None:
        valid_output = RevenueExtractionOutput.model_validate_json(_response_json())

        self.assertEqual("Example Demo Co.", valid_output.company_name)
        self.assertEqual(Decimal("120"), valid_output.rows[0].revenue_value)

        missing_required = json.loads(_response_json())
        del missing_required["rows"][0]["evidence_text"]
        with self.assertRaises(ValidationError):
            RevenueExtractionOutput.model_validate(missing_required)

        with_extra = json.loads(_response_json())
        with_extra["unexpected"] = "not allowed"
        with self.assertRaises(ValidationError):
            RevenueExtractionOutput.model_validate(with_extra)

    def test_primary_segmentation_response_can_include_all_current_period_columns(self) -> None:
        output = RevenueExtractionOutput.model_validate_json(
            _response_json(
                rows=[
                    _row_json("Network operator Liander", "2,924", "Network operator Liander 2,924"),
                    _row_json("Other", "170", "Other 170"),
                    _row_json("Eliminations", "-", "Eliminations -"),
                    _row_json("Total", "3,094", "Total 3,094"),
                    _row_json(
                        "Reclassification to reported and incidental items",
                        "787",
                        "Reclassification to reported and incidental items 787",
                    ),
                    _row_json("Reported", "3,881", "Reported 3,881"),
                ]
            )
        )

        self.assertEqual(
            [
                "Network operator Liander",
                "Other",
                "Eliminations",
                "Total",
                "Reclassification to reported and incidental items",
                "Reported",
            ],
            [row.segment_name for row in output.rows],
        )
        self.assertIsNone(output.rows[2].revenue_value)
        self.assertEqual("-", output.rows[2].revenue_raw)

    def test_fake_provider_extracts_rows_from_prompt_tables(self) -> None:
        prompt = build_first_pass_extraction_prompt(
            document=PromptDocumentFixture(),
            pages=[PromptPageFixture()],
        )
        provider = FakeRevenueExtractionProvider()

        response = provider.complete_json(
            _request(prompt=prompt),
        )
        output = RevenueExtractionOutput.model_validate_json(response.content)

        self.assertEqual(["Commercial", "Consumer"], [row.segment_name for row in output.rows])
        self.assertEqual(Decimal("120"), output.rows[0].revenue_value)
        self.assertEqual("USD", output.rows[0].currency)
        self.assertEqual("millions", output.rows[0].scale)
        self.assertEqual("p. 2", output.rows[0].page_ref)

    def test_invalid_json_creates_validation_issue_without_persisting_rows(self) -> None:
        connection, repo, document_id = _repository_with_candidate_page()
        try:
            service = RevenueExtractionService(
                repo,
                FakeRevenueExtractionProvider(response_text="{not valid json"),
                ExtractionSettings(),
            )

            summary = service.extract_document(document_id)

            self.assertEqual(0, summary.persisted_row_count)
            self.assertEqual(1, summary.validation_issue_count)
            self.assertEqual([], repo.list_segment_rows(document_id))
            issue = repo.list_validation_issues(document_id)[0]
            self.assertEqual("llm_output_validation", issue.issue_type)
            self.assertEqual("error", issue.severity)
        finally:
            connection.close()

    def test_json_wrapped_in_markdown_is_extracted_before_validation(self) -> None:
        connection, repo, document_id = _repository_with_candidate_page()
        wrapped_response = "Here is the JSON:\n```json\n" + _response_json() + "\n```"
        try:
            service = RevenueExtractionService(
                repo,
                FakeRevenueExtractionProvider(response_text=wrapped_response),
                ExtractionSettings(),
            )

            summary = service.extract_document(document_id)

            self.assertEqual(1, summary.persisted_row_count)
            self.assertEqual(0, summary.validation_issue_count)
            self.assertEqual("Commercial", repo.list_segment_rows(document_id)[0].segment_name)
        finally:
            connection.close()

    def test_non_json_provider_response_creates_validation_issue(self) -> None:
        connection, repo, document_id = _repository_with_candidate_page()
        try:
            service = RevenueExtractionService(
                repo,
                FakeRevenueExtractionProvider(response_text="I cannot extract this document."),
                ExtractionSettings(),
            )

            summary = service.extract_document(document_id)

            self.assertEqual(0, summary.persisted_row_count)
            self.assertEqual(1, summary.validation_issue_count)
            self.assertEqual(
                "Provider response did not contain a JSON object",
                repo.list_validation_issues(document_id)[0].message,
            )
        finally:
            connection.close()

    def test_persists_extracted_rows_as_ready_for_review_with_evidence(self) -> None:
        connection, repo, document_id = _repository_with_candidate_page()
        try:
            service = RevenueExtractionService(
                repo,
                FakeRevenueExtractionProvider(response_text=_response_json()),
                ExtractionSettings(),
            )

            summary = service.extract_document(document_id)

            self.assertEqual(1, summary.persisted_row_count)
            stored_rows = repo.list_segment_rows(document_id)
            self.assertEqual(1, len(stored_rows))
            self.assertEqual("Commercial", stored_rows[0].segment_name)
            self.assertEqual(SEGMENT_STATUS_READY_FOR_REVIEW, stored_rows[0].status)
            self.assertEqual(Decimal("120000000"), stored_rows[0].normalized_value)
            self.assertEqual("fake:first_pass_revenue_segments_v1", stored_rows[0].extraction_method)

            evidence = repo.list_segment_evidence(stored_rows[0].id)
            self.assertEqual(1, len(evidence))
            self.assertEqual(2, evidence[0].page_number)
            self.assertIn("Commercial", evidence[0].snippet_text)
        finally:
            connection.close()

    def test_persists_all_primary_segmentation_columns_including_dash_values(self) -> None:
        rows = [
            _row_json("Network operator Liander", "2,924", "Network operator Liander 2,924"),
            _row_json("Other", "170", "Other 170"),
            _row_json("Eliminations", "-", "Eliminations -"),
            _row_json("Total", "3,094", "Total 3,094"),
            _row_json(
                "Reclassification to reported and incidental items",
                "787",
                "Reclassification to reported and incidental items 787",
            ),
            _row_json("Reported", "3,881", "Reported 3,881"),
        ]
        connection, repo, document_id = _repository_with_candidate_page()
        try:
            service = RevenueExtractionService(
                repo,
                FakeRevenueExtractionProvider(response_text=_response_json(rows=rows)),
                ExtractionSettings(),
            )

            summary = service.extract_document(document_id)

            stored_rows = repo.list_segment_rows(document_id)
            self.assertEqual(6, summary.persisted_row_count)
            self.assertEqual(
                [
                    "Network operator Liander",
                    "Other",
                    "Eliminations",
                    "Total",
                    "Reclassification to reported and incidental items",
                    "Reported",
                ],
                [row.segment_name for row in stored_rows],
            )
            eliminations = next(row for row in stored_rows if row.segment_name == "Eliminations")
            self.assertEqual("-", eliminations.revenue_raw)
            self.assertIsNone(eliminations.revenue_value)
            issue_types = {issue.issue_type for issue in repo.list_validation_issues(document_id)}
            self.assertIn("missing_revenue_value", issue_types)
        finally:
            connection.close()

    def test_extraction_uses_filtered_financial_statement_candidates_only(self) -> None:
        connection, repo, document_id = _repository_with_mixed_candidate_pages()
        provider = RecordingProvider()
        try:
            summary = RevenueExtractionService(
                repo,
                provider,
                ExtractionSettings(),
            ).extract_document(document_id)

            self.assertEqual(2, summary.candidate_page_count)
            combined_prompts = "\n".join(provider.prompts)
            self.assertNotIn("Page 161", combined_prompts)
            self.assertNotIn("Page 258", combined_prompts)
            self.assertIn("Page 257", combined_prompts)
            self.assertNotIn("Energy usage by source and energy mix", combined_prompts)
        finally:
            connection.close()

    def test_extraction_adds_next_page_for_segment_note_intro(self) -> None:
        connection, repo, document_id = _repository_with_continuation_candidate_pages()
        provider = RecordingProvider()
        try:
            summary = RevenueExtractionService(
                repo,
                provider,
                ExtractionSettings(),
            ).extract_document(document_id)

            self.assertEqual(1, summary.candidate_page_count)
            combined_prompts = "\n".join(provider.prompts)
            self.assertIn("Page 391", combined_prompts)
            self.assertIn("Page 392", combined_prompts)
            self.assertIn("Net interest income", combined_prompts)
        finally:
            connection.close()

    def test_no_extraction_eligible_candidates_is_warning_not_error(self) -> None:
        connection = connect_database(":memory:")
        initialize_database(connection)
        repo = SQLiteRepository(connection)
        document = repo.create_document(
            company_name="Regulatory Bank",
            document_name="pillar-3.pdf",
            source_path="pillar-3.pdf",
        )
        repo.create_parsed_page(
            document_id=document.id,
            page_number=1,
            text="Capital adequacy and credit risk tables",
            blocks_json={"blocks": []},
            tables_json={"tables": []},
            language="unknown",
            parser_sources=("pymupdf",),
            has_text=True,
        )
        repo.create_page_candidate(
            document_id=document.id,
            page_number=1,
            relevance_score=5.0,
            matched_signals_json={"numeric_density": {"score": 3.0}},
            reason="Risk table fixture.",
        )
        try:
            summary = RevenueExtractionService(
                repo,
                EmptyDiscoveryProvider(),
                ExtractionSettings(),
            ).extract_document(document.id)

            self.assertEqual(0, summary.persisted_row_count)
            self.assertEqual("warning", summary.validation_issues[0].severity)
            self.assertEqual(
                "no_extraction_eligible_candidate_pages",
                summary.validation_issues[0].issue_type,
            )
        finally:
            connection.close()

    def test_llm_candidate_discovery_fallback_extracts_missed_page(self) -> None:
        connection, repo, document_id = _repository_with_missed_revenue_equivalent_page()
        provider = DiscoveryFallbackProvider()
        try:
            summary = RevenueExtractionService(
                repo,
                provider,
                ExtractionSettings(),
            ).extract_document(document_id)

            self.assertEqual(1, summary.persisted_row_count)
            self.assertEqual("Jurisdiction A", repo.list_segment_rows(document_id)[0].segment_name)
            self.assertTrue(
                any("Prompt version: candidate_page_discovery_v1" in prompt for prompt in provider.prompts)
            )
            self.assertTrue(any("Page 5" in prompt for prompt in provider.prompts))
        finally:
            connection.close()

    def test_rejects_provider_rows_outside_prompt_bundle(self) -> None:
        connection, repo, document_id = _repository_with_candidate_page(page_number=257)
        try:
            service = RevenueExtractionService(
                repo,
                FakeRevenueExtractionProvider(
                    response_text=_response_json(
                        rows=[
                            {
                                **_row_json(
                                    "Non-high climate impact sectors",
                                    "2334",
                                    "EU taxonomy energy intensity revenue",
                                ),
                                "page_ref": "p. 161",
                            }
                        ]
                    )
                ),
                ExtractionSettings(),
            )

            summary = service.extract_document(document_id)

            self.assertEqual(0, summary.persisted_row_count)
            self.assertEqual([], repo.list_segment_rows(document_id))
            issues = repo.list_validation_issues(document_id)
            self.assertEqual("row_page_outside_prompt_bundle", issues[0].issue_type)
        finally:
            connection.close()

    def test_rejects_generic_consolidated_revenue_line_item_as_segment(self) -> None:
        connection, repo, document_id = _repository_with_candidate_page()
        try:
            service = RevenueExtractionService(
                repo,
                FakeRevenueExtractionProvider(
                    response_text=_response_json(
                        rows=[
                            {
                                **_row_json("Revenue", "$120", "Revenue $120"),
                                "metric_basis": "Total consolidated revenue (not a segment breakdown)",
                            }
                        ]
                    )
                ),
                ExtractionSettings(),
            )

            summary = service.extract_document(document_id)

            self.assertEqual(0, summary.persisted_row_count)
            self.assertEqual([], repo.list_segment_rows(document_id))
            self.assertEqual(
                "non_segment_revenue_disclosure",
                repo.list_validation_issues(document_id)[0].issue_type,
            )
        finally:
            connection.close()

    def test_deduplicates_identical_rows_but_keeps_different_evidence(self) -> None:
        output = RevenueExtractionOutput.model_validate_json(
            _response_json(
                rows=[
                    _row_json("Commercial", "$120", "Commercial | $120"),
                    _row_json("Commercial", "$120", "Commercial | $120"),
                    _row_json("Commercial", "$120", "Commercial external revenue $120"),
                ]
            )
        )
        prepared_rows = [
            PreparedRevenueRow(
                source_row=row,
                normalized_value=row.revenue_value * Decimal("1000000"),
                page_number=2,
                fiscal_period="FY2025",
            )
            for row in output.rows
        ]

        deduped_rows, duplicate_count = deduplicate_rows(prepared_rows)

        self.assertEqual(1, duplicate_count)
        self.assertEqual(2, len(deduped_rows))

    def test_latest_period_filter_keeps_only_latest_detected_year(self) -> None:
        output = RevenueExtractionOutput.model_validate_json(
            _response_json(
                rows=[
                    {**_row_json("Commercial", "$120", "Commercial 2025 $120"), "period_label": "FY2025"},
                    {**_row_json("Commercial", "$100", "Commercial 2024 $100"), "period_label": "FY2024"},
                    {**_row_json("Consumer", "$180", "Consumer current period $180"), "period_label": None},
                ]
            )
        )

        result = keep_latest_year_rows(output.rows)

        self.assertEqual(2025, result.latest_year)
        self.assertEqual(1, result.skipped_count)
        self.assertEqual(["Commercial", "Consumer"], [row.segment_name for row in result.rows])

    def test_aligns_external_revenue_rows_to_total_revenue_metric_when_present(self) -> None:
        output = RevenueExtractionOutput.model_validate_json(
            _response_json(
                rows=[
                    {**_row_json("Offshore", "53,207", "External revenue Offshore 53,207"), "metric_basis": "External revenue"},
                    {**_row_json("Onshore", "2,886", "External revenue Onshore 2,886"), "metric_basis": "External revenue"},
                    {**_row_json("Total", "73,244", "External revenue Total 73,244"), "metric_basis": "External revenue"},
                ]
            )
        )
        page = _parsed_page(
            2,
            "2025 income statement\nDKKm\nOffshore\nOnshore\nTotal\n"
            "External revenue\n53,207\n2,886\n73,244\n"
            "Intra-group revenue\n1,590\n-\n-\n"
            "Revenue\n54,797\n2,886\n73,244\nCost of sales\n(27,360)\n(36)\n(38,984)",
        )

        aligned = align_rows_to_preferred_metric(output.rows, page)

        self.assertEqual("Revenue", aligned[0].metric_basis)
        self.assertEqual("54,797", aligned[0].revenue_raw)
        self.assertEqual(Decimal("54797"), aligned[0].revenue_value)

    def test_aligns_external_income_rows_to_current_year_total_income_pairs(self) -> None:
        output = RevenueExtractionOutput.model_validate_json(
            _response_json(
                rows=[
                    {**_row_json("Network operator Liander", "2,924", "External income Liander"), "metric_basis": "External income"},
                    {**_row_json("Other", "170", "External income Other"), "metric_basis": "External income"},
                    {**_row_json("Eliminations", "-", "External income Eliminations"), "metric_basis": "External income"},
                    {**_row_json("Total", "3,094", "External income Total"), "metric_basis": "External income"},
                ]
            )
        )
        page = _parsed_page(
            2,
            "Primary segmentation\nEUR million\nNetwork operator Liander\nOther\nEliminations\nTotal\n"
            "2024\n2023\n2024\n2023\n2024\n2023\n2024\n2023\n"
            "External income\n2,924\n2,519\n170\n255\n-\n-\n3,094\n2,774\n"
            "Internal income\n3\n4\n508\n427\n-511\n-431\n-\n-\n"
            "Total income\n2,927 2,523\n678\n682\n-511\n-431\n3,094 2,774",
        )

        aligned = align_rows_to_preferred_metric(output.rows, page)

        self.assertEqual(["2,927", "678", "-511", "3,094"], [row.revenue_raw for row in aligned])
        self.assertEqual(Decimal("-511"), aligned[2].revenue_value)

    def test_primary_table_selection_drops_secondary_duplicate_page_groups(self) -> None:
        output = RevenueExtractionOutput.model_validate_json(
            _response_json(
                rows=[
                    _row_json("Services", "$19,649", "Services page 23"),
                    _row_json("Markets", "$19,836", "Markets page 23"),
                    _row_json("Banking", "$6,201", "Banking page 23"),
                    _row_json("Wealth", "$7,512", "Wealth page 23"),
                    _row_json("Total Citigroup net revenues", "$81,139", "Total page 23"),
                    {**_row_json("Services", "$19,649", "Services page 178"), "page_ref": "p. 178"},
                    {**_row_json("Markets", "$19,836", "Markets page 178"), "page_ref": "p. 178"},
                    {**_row_json("Banking", "$6,201", "Banking page 178"), "page_ref": "p. 178"},
                    {**_row_json("Wealth", "$7,512", "Wealth page 178"), "page_ref": "p. 178"},
                    {**_row_json("Total Citi", "$81,139", "Total page 178"), "page_ref": "p. 178"},
                ]
            )
        )
        prepared_rows = [
            PreparedRevenueRow(
                source_row=row,
                normalized_value=row.revenue_value,
                page_number=23 if "23" in row.evidence_text else 178,
                fiscal_period=row.period_label,
            )
            for row in output.rows
        ]

        result = keep_primary_table_rows(prepared_rows)

        self.assertEqual(5, result.skipped_count)
        self.assertEqual(5, len(result.rows))
        self.assertTrue(all(row.page_number == 23 for row in result.rows))

    def test_extraction_skips_prior_period_rows_before_persistence(self) -> None:
        rows = [
            {**_row_json("Commercial", "$120", "Commercial 2025 $120"), "period_label": "FY2025"},
            {**_row_json("Commercial", "$100", "Commercial 2024 $100"), "period_label": "FY2024"},
        ]
        connection, repo, document_id = _repository_with_candidate_page()
        try:
            service = RevenueExtractionService(
                repo,
                FakeRevenueExtractionProvider(response_text=_response_json(rows=rows)),
                ExtractionSettings(),
            )

            summary = service.extract_document(document_id)

            stored_rows = repo.list_segment_rows(document_id)
            self.assertEqual(1, summary.persisted_row_count)
            self.assertEqual("$120", stored_rows[0].revenue_raw)
            self.assertEqual("FY2025", stored_rows[0].period_label)
            self.assertEqual(
                "prior_period_row_skipped",
                repo.list_validation_issues(document_id)[0].issue_type,
            )
        finally:
            connection.close()

    def test_integration_parses_pdf_ranks_candidates_fake_extracts_and_stores_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "sample-10k.pdf"
            _write_sample_pdf(pdf_path)
            connection = connect_database(":memory:")
            initialize_database(connection)
            repo = SQLiteRepository(connection)
            try:
                ingestion_summary = PdfIngestionService(repo).ingest_pdf(
                    pdf_path=pdf_path,
                    company_name="Example Demo Co.",
                    fiscal_period="FY2025",
                    candidate_limit=5,
                )

                extraction_summary = RevenueExtractionService(
                    repo,
                    FakeRevenueExtractionProvider(),
                    ExtractionSettings(),
                ).extract_document(ingestion_summary.document.id)

                stored_rows = repo.list_segment_rows(ingestion_summary.document.id)
                self.assertGreaterEqual(extraction_summary.candidate_page_count, 1)
                self.assertEqual(2, extraction_summary.persisted_row_count)
                self.assertEqual(["Commercial", "Consumer"], [row.segment_name for row in stored_rows])
                self.assertTrue(
                    all(row.status == SEGMENT_STATUS_READY_FOR_REVIEW for row in stored_rows)
                )
                self.assertEqual(
                    Decimal("120000000"),
                    next(row for row in stored_rows if row.segment_name == "Commercial").normalized_value,
                )
            finally:
                connection.close()

    def test_integration_fake_fallback_text_extracts_and_stores_ocr_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scanned.pdf"
            _write_blank_pdf(pdf_path)
            connection = connect_database(":memory:")
            initialize_database(connection)
            repo = SQLiteRepository(connection)
            try:
                ingestion_summary = PdfIngestionService(
                    repo,
                    fallback_provider=FakePageTextFallbackProvider(
                        {
                            1: (
                                "Note 4 - Operating Segments\n"
                                "Revenue by segment, USD millions\n"
                                "Commercial external revenue $120"
                            )
                        }
                    ),
                ).ingest_pdf(
                    pdf_path=pdf_path,
                    company_name="Example Demo Co.",
                    fiscal_period="FY2025",
                    currency="USD",
                    scale="millions",
                    candidate_limit=5,
                )

                extraction_summary = RevenueExtractionService(
                    repo,
                    OcrFallbackRevenueProvider(),
                    ExtractionSettings(),
                ).extract_document(ingestion_summary.document.id)

                stored_rows = repo.list_segment_rows(ingestion_summary.document.id)
                evidence = repo.list_segment_evidence(stored_rows[0].id)
                self.assertEqual(1, extraction_summary.persisted_row_count)
                self.assertEqual("Commercial", stored_rows[0].segment_name)
                self.assertEqual("ocr", evidence[0].parser_source)
                self.assertIn("Commercial external revenue $120", evidence[0].snippet_text)
            finally:
                connection.close()


def _request(prompt: str):
    from revenue_segment_extractor.extraction.providers import LLMExtractionRequest

    return LLMExtractionRequest(
        prompt=prompt,
        model="fake-model",
        prompt_version="first_pass_revenue_segments_v1",
        max_tokens=4000,
    )


def _repository_with_candidate_page_for_number(page_number: int):
    connection = connect_database(":memory:")
    initialize_database(connection)
    repo = SQLiteRepository(connection)
    document = repo.create_document(
        company_name="Example Demo Co.",
        document_name="sample-10k.pdf",
        source_path="sample-10k.pdf",
        fiscal_period="FY2025",
        currency="USD",
        scale="millions",
    )
    repo.create_parsed_page(
        document_id=document.id,
        page_number=page_number,
        text="Financial statements\nNote 4 - Operating Segments\nCommercial external revenue $120",
        blocks_json={
            "blocks": [
                {
                    "block_index": 1,
                    "block_type": 0,
                    "text": "Commercial external revenue $120",
                    "bbox": {"x0": 1, "y0": 2, "x1": 3, "y1": 4},
                }
            ]
        },
        tables_json={
            "tables": [
                {
                    "rows": [
                        ["Segment", "External revenue", "Total"],
                        ["Commercial", "$120", "$120"],
                    ]
                }
            ]
        },
        language="unknown",
        parser_sources=("pymupdf", "pdfplumber"),
        has_text=True,
    )
    repo.create_page_candidate(
        document_id=document.id,
        page_number=page_number,
        relevance_score=12.0,
        matched_signals_json={"terms": [{"term": "operating segments", "weight": 8.5}]},
        reason="Matched operating segment and revenue signals.",
    )
    return connection, repo, document.id


def _repository_with_candidate_page(page_number: int = 2):
    return _repository_with_candidate_page_for_number(page_number)


def _repository_with_mixed_candidate_pages():
    connection = connect_database(":memory:")
    initialize_database(connection)
    repo = SQLiteRepository(connection)
    document = repo.create_document(
        company_name="Alliander",
        document_name="annual-report.pdf",
        source_path="annual-report.pdf",
    )
    pages = [
        (91, "Being a creditworthy company with solid returns\nSegment reporting\nPrimary segmentation"),
        (161, "Environment\nEnergy usage by source and energy mix\nEU taxonomy revenue"),
        (256, "Financial statements\nNote 2 Segment information\nOperating segments"),
        (257, "Financial statements\nNotes\nPrimary segmentation\nExternal income by segment"),
        (258, "Financial statements\nProduct segmentation\nExternal revenue"),
    ]
    for page_number, text in pages:
        repo.create_parsed_page(
            document_id=document.id,
            page_number=page_number,
            text=text,
            blocks_json={"blocks": []},
            tables_json={"tables": []},
            language="unknown",
            parser_sources=("pymupdf",),
            has_text=True,
        )
    for page_number, terms in [
        (91, [{"term": "segment reporting", "weight": 7.5}]),
        (161, []),
        (256, [{"term": "operating segments", "weight": 8.5}]),
        (257, [{"term": "external revenue", "weight": 7.0}]),
        (258, [{"term": "external revenue", "weight": 7.0}]),
    ]:
        repo.create_page_candidate(
            document_id=document.id,
            page_number=page_number,
            relevance_score=12.0,
            matched_signals_json={"terms": terms},
            reason="fixture",
        )
    return connection, repo, document.id


def _repository_with_continuation_candidate_pages():
    connection = connect_database(":memory:")
    initialize_database(connection)
    repo = SQLiteRepository(connection)
    document = repo.create_document(
        company_name="Commerzbank",
        document_name="annual-report.pdf",
        source_path="annual-report.pdf",
    )
    for page_number, text, tables_json in [
        (
            391,
            "Financial Statements\n(60) Segment reporting\n"
            "Segment reporting reflects the results of operating segments.",
            {"tables": []},
        ),
        (
            392,
            "2025 €m\nPrivate and Small Business Customers\nCorporate Clients\n"
            "Net interest income\n4,713\n2,498",
            {"tables": [{"rows": [["Segment", "Net interest income"], ["Private", "4,713"]]}]},
        ),
    ]:
        repo.create_parsed_page(
            document_id=document.id,
            page_number=page_number,
            text=text,
            blocks_json={"blocks": []},
            tables_json=tables_json,
            language="unknown",
            parser_sources=("pymupdf",),
            has_text=True,
        )
    repo.create_page_candidate(
        document_id=document.id,
        page_number=391,
        relevance_score=20.0,
        matched_signals_json={"terms": [{"term": "segment reporting", "weight": 7.5}]},
        reason="Segment note intro.",
    )
    return connection, repo, document.id


def _repository_with_missed_revenue_equivalent_page():
    connection = connect_database(":memory:")
    initialize_database(connection)
    repo = SQLiteRepository(connection)
    document = repo.create_document(
        company_name="Example Demo Co.",
        document_name="sample.pdf",
        source_path="sample.pdf",
    )
    for page_number, text, tables_json in [
        (
            1,
            "Financial statements\nNote 1 Operating segments\n"
            "This page contains only asset balances and risk exposure.",
            {"tables": [{"rows": [["Assets", "2025"], ["Loans", "100"]]}]},
        ),
        (
            5,
            "Regulatory disclosure\nA table splits a revenue-equivalent measure by reporting dimension.",
            {
                "tables": [
                    {
                        "rows": [
                            ["Reporting dimension", "Current period measure"],
                            ["Jurisdiction A", "220,031"],
                        ]
                    }
                ]
            },
        ),
    ]:
        repo.create_parsed_page(
            document_id=document.id,
            page_number=page_number,
            text=text,
            blocks_json={"blocks": []},
            tables_json=tables_json,
            language="unknown",
            parser_sources=("pymupdf",),
            has_text=True,
        )
    repo.create_page_candidate(
        document_id=document.id,
        page_number=1,
        relevance_score=12.0,
        matched_signals_json={"terms": [{"term": "operating segments", "weight": 8.5}]},
        reason="Initial deterministic candidate misses the later page.",
    )
    return connection, repo, document.id


class RecordingProvider:
    name = "recording"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        self.prompts.append(request.prompt)
        return LLMExtractionResponse(
            content=json.dumps(
                {
                    "company_name": "Alliander",
                    "document_name": "annual-report.pdf",
                    "fiscal_period": None,
                    "reported_total": None,
                    "currency": None,
                    "scale": None,
                    "rows": [],
                    "extraction_warnings": [],
                }
            ),
            model=request.model,
            provider_name=self.name,
        )


class DiscoveryFallbackProvider:
    name = "discovery"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        self.prompts.append(request.prompt)
        if request.prompt_version == "candidate_page_discovery_v1":
            return LLMExtractionResponse(
                content=json.dumps(
                    {
                        "selected_pages": [
                            {
                                "page_number": 5,
                                "reason": "A table appears to split a revenue-equivalent measure by reporting dimension.",
                                "confidence": 0.92,
                            }
                        ],
                        "extraction_warnings": [],
                    }
                ),
                model=request.model,
                provider_name=self.name,
            )
        if "Page 5" in request.prompt:
            return LLMExtractionResponse(
                content=_response_json(
                    rows=[
                        {
                            **_row_json(
                                "Jurisdiction A",
                                "220,031",
                                "Jurisdiction A revenue-equivalent measure 220,031",
                            ),
                            "currency": "EUR",
                            "scale": "thousands",
                            "metric_basis": "Revenue-equivalent measure",
                            "page_ref": "p. 5",
                        }
                    ]
                ),
                model=request.model,
                provider_name=self.name,
            )
        return LLMExtractionResponse(
            content=json.dumps(
                {
                    "company_name": "Example Demo Co.",
                    "document_name": "sample.pdf",
                    "fiscal_period": None,
                    "reported_total": None,
                    "currency": None,
                    "scale": None,
                    "rows": [],
                    "extraction_warnings": ["No rows in initial deterministic candidate bundle."],
                }
            ),
            model=request.model,
            provider_name=self.name,
        )


class EmptyDiscoveryProvider(RecordingProvider):
    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        self.prompts.append(request.prompt)
        if request.prompt_version == "candidate_page_discovery_v1":
            return LLMExtractionResponse(
                content=json.dumps({"selected_pages": [], "extraction_warnings": []}),
                model=request.model,
                provider_name=self.name,
            )
        return super().complete_json(request)


class OcrFallbackRevenueProvider:
    name = "ocr_fallback_fixture"

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        row = {
            **_row_json(
                "Commercial",
                "$120",
                "Commercial external revenue $120",
            ),
            "page_ref": "p. 1",
        }
        return LLMExtractionResponse(
            content=_response_json(rows=[row]),
            model=request.model,
            provider_name=self.name,
        )


def _candidate(page_number: int, terms: list[dict]) -> PageCandidate:
    return PageCandidate(
        id=f"candidate_{page_number}",
        document_id="doc_fixture",
        page_number=page_number,
        relevance_score=10.0,
        matched_signals_json={"terms": terms},
        reason="fixture",
    )


def _parsed_page(page_number: int, text: str) -> ParsedPage:
    from tests.fixtures import FIXED_TIME

    return ParsedPage(
        id=f"page_{page_number}",
        document_id="doc_fixture",
        page_number=page_number,
        text=text,
        blocks_json={"blocks": []},
        tables_json={"tables": []},
        language="unknown",
        parser_sources=("pymupdf",),
        has_text=True,
        created_at=FIXED_TIME,
    )


def _response_json(rows: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "company_name": "Example Demo Co.",
            "document_name": "sample-10k.pdf",
            "fiscal_period": "FY2025",
            "reported_total": None,
            "currency": "USD",
            "scale": "millions",
            "rows": rows or [_row_json("Commercial", "$120", "Commercial external revenue $120")],
            "extraction_warnings": [],
        }
    )


def _row_json(segment_name: str, revenue_raw: str, evidence_text: str) -> dict:
    revenue_value = None if revenue_raw.strip() == "-" else revenue_raw.replace("$", "").replace(",", "")
    return {
        "segment_name": segment_name,
        "revenue_raw": revenue_raw,
        "revenue_value": revenue_value,
        "currency": "USD",
        "scale": "millions",
        "period_label": "FY2025",
        "page_ref": "p. 2",
        "section_ref": "Note 4 - Operating Segments",
        "metric_basis": "External revenue",
        "evidence_text": evidence_text,
        "confidence": 0.91,
        "extraction_notes": "Unit test fixture.",
    }


def _write_sample_pdf(path: Path) -> None:
    document = fitz.open()

    overview_page = document.new_page(width=612, height=792)
    overview_page.insert_text(
        (72, 72),
        "Annual report overview\nThis page describes governance and strategy.",
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

    document.save(path)
    document.close()


def _write_blank_pdf(path: Path) -> None:
    document = fitz.open()
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
