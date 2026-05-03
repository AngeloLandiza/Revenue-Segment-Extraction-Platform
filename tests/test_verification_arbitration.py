from __future__ import annotations

import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from revenue_segment_extractor.extraction import ExtractionSettings, FakeRevenueExtractionProvider
from revenue_segment_extractor.extraction.arbitration import should_run_arbitration
from revenue_segment_extractor.extraction.config import DEFAULT_ARBITRATION_MODEL
from revenue_segment_extractor.extraction.providers import LLMExtractionRequest, LLMExtractionResponse
from revenue_segment_extractor.extraction.schemas import ExtractedRevenueRow, RevenueExtractionOutput
from revenue_segment_extractor.extraction.service import RevenueExtractionService
from revenue_segment_extractor.extraction.validation import DeterministicValidationIssue
from revenue_segment_extractor.extraction.verification import run_second_pass_verification
from revenue_segment_extractor.models import SEGMENT_STATUS_NEEDS_REVIEW
from revenue_segment_extractor.persistence import SQLiteRepository, connect_database, initialize_database
from tests.fixtures import build_document
from tests.test_normalization_validation import _normalized_row, _parsed_page_with_table


class VerificationAndArbitrationTest(unittest.TestCase):
    def test_default_arbitration_model_uses_extraction_model(self) -> None:
        settings = ExtractionSettings()

        self.assertEqual(DEFAULT_ARBITRATION_MODEL, settings.arbitration_model)
        self.assertEqual(settings.model, settings.arbitration_model)
        self.assertFalse(settings.enable_arbitration)

    def test_env_arbitration_model_defaults_to_env_extraction_model(self) -> None:
        with patch.dict(
            "os.environ",
            {"RSE_EXTRACTION_MODEL": "claude-sonnet-custom"},
            clear=True,
        ):
            settings = ExtractionSettings.from_env()

        self.assertEqual("claude-sonnet-custom", settings.model)
        self.assertEqual(settings.model, settings.arbitration_model)

    def test_fake_second_pass_verification_returns_strict_output(self) -> None:
        provider = FakeRevenueExtractionProvider(
            response_text=json.dumps(
                {
                    "confirmed_rows": [],
                    "suspected_errors": [
                        {
                            "segment_name": "Commercial",
                            "issue_type": "currency_mismatch",
                            "suggested_action": "review",
                            "rationale": "The row currency differs from the table heading.",
                        }
                    ],
                    "missing_rows": [],
                    "correction_suggestions": [],
                    "rationale": "One row needs human review.",
                }
            )
        )

        result = run_second_pass_verification(
            provider=provider,
            document=build_document(),
            pages=[_parsed_page_with_table([["Segment", "Revenue"], ["Commercial", "120"]])],
            rows=[_normalized_row()],
            validation_issues=[],
            model="fake-model",
            max_tokens=1000,
            temperature=0.0,
        )

        self.assertIsNotNone(result.output)
        self.assertEqual("currency_mismatch", result.output.suspected_errors[0].issue_type)

    def test_arbitration_trigger_logic_is_not_called_for_clean_documents(self) -> None:
        self.assertFalse(
            should_run_arbitration(
                validation_issues=[],
                verification_output=None,
                verification_failed=False,
            )
        )

    def test_arbitration_trigger_logic_runs_for_validation_or_verification_failures(self) -> None:
        warning = DeterministicValidationIssue(
            row_index=0,
            severity="warning",
            issue_type="total_reconciliation_mismatch",
            message="Mismatch.",
        )

        self.assertTrue(
            should_run_arbitration(
                validation_issues=[warning],
                verification_output=None,
                verification_failed=False,
            )
        )
        self.assertTrue(
            should_run_arbitration(
                validation_issues=[],
                verification_output=None,
                verification_failed=True,
            )
        )

    def test_arbitration_runs_only_for_uncertain_cases(self) -> None:
        clean_connection, clean_repo, clean_document_id = _repository_with_revenue_row(
            reported_total=Decimal("120000000")
        )
        clean_arbitration = _RecordingArbitrationProvider()
        try:
            RevenueExtractionService(
                clean_repo,
                _SingleRowExtractionProvider(row_currency="USD"),
                ExtractionSettings(enable_arbitration=True),
                arbitration_provider=clean_arbitration,
            ).extract_document(clean_document_id)

            self.assertEqual([], clean_arbitration.requests)
        finally:
            clean_connection.close()

        uncertain_connection, uncertain_repo, uncertain_document_id = _repository_with_revenue_row(
            reported_total=Decimal("120000000")
        )
        uncertain_arbitration = _RecordingArbitrationProvider()
        try:
            RevenueExtractionService(
                uncertain_repo,
                _SingleRowExtractionProvider(row_currency="EUR"),
                ExtractionSettings(enable_arbitration=True),
                arbitration_provider=uncertain_arbitration,
            ).extract_document(uncertain_document_id)

            self.assertEqual(1, len(uncertain_arbitration.requests))
            self.assertEqual(DEFAULT_ARBITRATION_MODEL, uncertain_arbitration.requests[0].model)
            issue_types = {
                issue.issue_type for issue in uncertain_repo.list_validation_issues(uncertain_document_id)
            }
            self.assertIn("llm_opus_arbitration_result", issue_types)
        finally:
            uncertain_connection.close()

    def test_integration_normalizes_validates_verifies_and_stores_issues(self) -> None:
        connection = connect_database(":memory:")
        initialize_database(connection)
        repo = SQLiteRepository(connection)
        document = repo.create_document(
            company_name="Example Demo Co.",
            document_name="sample-10k.pdf",
            source_path="sample-10k.pdf",
            fiscal_period="FY2025",
            reported_total=Decimal("120000000"),
            currency="USD",
            scale="millions",
        )
        repo.create_parsed_page(
            document_id=document.id,
            page_number=2,
            text="Financial statements\nNote 4 Operating segments\nCommercial external revenue EUR 120",
            blocks_json={"blocks": [{"text": "Commercial external revenue EUR 120"}]},
            tables_json={"tables": [{"rows": [["Segment", "Revenue"], ["Commercial", "120"]]}]},
            language="unknown",
            parser_sources=("pymupdf",),
            has_text=True,
        )
        repo.create_page_candidate(
            document_id=document.id,
            page_number=2,
            relevance_score=12.0,
            matched_signals_json={"terms": [{"term": "operating segments", "weight": 8.5}]},
            reason="Fixture segment note.",
        )
        try:
            summary = RevenueExtractionService(
                repo,
                _SingleRowExtractionProvider(),
                ExtractionSettings(),
                verification_provider=_SuspectedErrorVerificationProvider(),
            ).extract_document(document.id)

            stored_rows = repo.list_segment_rows(document.id)
            issue_types = {issue.issue_type for issue in repo.list_validation_issues(document.id)}
            self.assertEqual(1, summary.persisted_row_count)
            self.assertEqual(SEGMENT_STATUS_NEEDS_REVIEW, stored_rows[0].status)
            self.assertIn("currency_mismatch", issue_types)
            self.assertIn("llm_verification_suspected_error", issue_types)
        finally:
            connection.close()


class _SingleRowExtractionProvider:
    name = "single-row"

    def __init__(self, *, row_currency: str = "EUR") -> None:
        self.row_currency = row_currency

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        row = ExtractedRevenueRow(
            segment_name="Commercial",
            revenue_raw="120",
            revenue_value=Decimal("120"),
            currency=self.row_currency,
            scale="millions",
            period_label="FY2025",
            page_ref="p. 2",
            section_ref="Note 4 Operating segments",
            metric_basis="Revenue",
            evidence_text=f"Commercial external revenue {self.row_currency} 120",
            confidence=0.9,
            extraction_notes="Fixture.",
        )
        output = RevenueExtractionOutput(
            company_name="Example Demo Co.",
            document_name="sample-10k.pdf",
            fiscal_period="FY2025",
            reported_total=Decimal("120000000"),
            currency="USD",
            scale="millions",
            rows=[row],
            extraction_warnings=[],
        )
        return LLMExtractionResponse(
            content=output.model_dump_json(),
            model=request.model,
            provider_name=self.name,
        )


class _SuspectedErrorVerificationProvider:
    name = "verification"

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        return LLMExtractionResponse(
            content=json.dumps(
                {
                    "confirmed_rows": [],
                    "suspected_errors": [
                        {
                            "segment_name": "Commercial",
                            "issue_type": "currency_mismatch",
                            "suggested_action": "review source table currency",
                            "rationale": "Document context says USD while the row says EUR.",
                        }
                    ],
                    "missing_rows": [],
                    "correction_suggestions": [],
                    "rationale": "Currency mismatch remains unresolved.",
                }
            ),
            model=request.model,
            provider_name=self.name,
        )


class _RecordingArbitrationProvider:
    name = "arbitration"

    def __init__(self) -> None:
        self.requests: list[LLMExtractionRequest] = []

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        self.requests.append(request)
        return LLMExtractionResponse(
            content=json.dumps(
                {
                    "accepted_rows": [
                        {
                            "segment_name": "Commercial",
                            "page_ref": "p. 2",
                            "confidence": 0.7,
                            "rationale": "LLM arbitration accepted the row for review.",
                        }
                    ],
                    "rejected_rows": [],
                    "missing_rows": [],
                    "correction_suggestions": [],
                    "requires_human_review": True,
                    "rationale": "Currency mismatch still requires analyst review.",
                }
            ),
            model=request.model,
            provider_name=self.name,
        )


def _repository_with_revenue_row(*, reported_total: Decimal):
    connection = connect_database(":memory:")
    initialize_database(connection)
    repo = SQLiteRepository(connection)
    document = repo.create_document(
        company_name="Example Demo Co.",
        document_name="sample-10k.pdf",
        source_path="sample-10k.pdf",
        fiscal_period="FY2025",
        reported_total=reported_total,
        currency="USD",
        scale="millions",
    )
    repo.create_parsed_page(
        document_id=document.id,
        page_number=2,
        text="Financial statements\nNote 4 Operating segments\nCommercial external revenue USD 120",
        blocks_json={"blocks": [{"text": "Commercial external revenue USD 120"}]},
        tables_json={"tables": [{"rows": [["Segment", "Revenue"], ["Commercial", "120"]]}]},
        language="unknown",
        parser_sources=("pymupdf",),
        has_text=True,
    )
    repo.create_page_candidate(
        document_id=document.id,
        page_number=2,
        relevance_score=12.0,
        matched_signals_json={"terms": [{"term": "operating segments", "weight": 8.5}]},
        reason="Fixture segment note.",
    )
    return connection, repo, document.id


if __name__ == "__main__":
    unittest.main()
