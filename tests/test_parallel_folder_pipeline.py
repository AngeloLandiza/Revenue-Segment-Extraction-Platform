from __future__ import annotations

import unittest
from types import SimpleNamespace

from parallel_folder_pipeline import (
    METADATA_FIELDS,
    _is_manual_metadata_error,
    _manual_metadata_result,
    _missing_document_metadata,
    _with_manual_metadata,
)


class ParallelFolderPipelineTest(unittest.TestCase):
    def test_missing_document_metadata_reports_blank_fields(self) -> None:
        document = SimpleNamespace(
            company_name="Example Co.",
            fiscal_period="",
            currency=None,
            scale="millions",
        )

        self.assertEqual(("fiscal_period", "currency"), _missing_document_metadata(document))

    def test_manual_metadata_result_marks_request_for_parent_prompt(self) -> None:
        result = _manual_metadata_result(
            "report.pdf",
            missing_fields=METADATA_FIELDS,
            error="company_name could not be auto-detected",
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["manual_input_required"])
        self.assertEqual("metadata", result["phase"])
        self.assertEqual(METADATA_FIELDS, result["missing_fields"])

    def test_manual_metadata_retry_merges_existing_payload(self) -> None:
        payload = {
            "pdf": "report.pdf",
            "database": "fitch.sqlite3",
            "provider": "fake",
            "candidate_limit": 15,
            "metadata": {"currency": "USD"},
        }

        retry = _with_manual_metadata(
            payload,
            {"company_name": "Example Co.", "currency": "EUR"},
        )

        self.assertEqual("Example Co.", retry["metadata"]["company_name"])
        self.assertEqual("EUR", retry["metadata"]["currency"])
        self.assertEqual("USD", payload["metadata"]["currency"])

    def test_manual_metadata_error_detection_is_specific(self) -> None:
        self.assertTrue(
            _is_manual_metadata_error(
                "company_name could not be auto-detected; provide it manually"
            )
        )
        self.assertFalse(_is_manual_metadata_error("Expected a .pdf file"))


if __name__ == "__main__":
    unittest.main()
