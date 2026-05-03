from __future__ import annotations

import unittest
from unittest.mock import patch

from revenue_segment_extractor.extraction.usage import WorkflowUsageTracker
from revenue_segment_extractor.models import QUEUE_STATUS_COMPLETED, QUEUE_STATUS_FAILED
from revenue_segment_extractor.persistence import SQLiteRepository, connect_database, initialize_database
from revenue_segment_extractor.queueing import DocumentQueueService
from revenue_segment_extractor.workflow import DocumentAnalysisResult


class DocumentQueueServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect_database(":memory:")
        initialize_database(self.connection)
        self.repository = SQLiteRepository(self.connection)
        self.service = DocumentQueueService(self.repository)
        self.document = self.repository.create_document(
            company_name="Example Demo Co.",
            document_name="annual-report.pdf",
            source_path="fixtures/annual-report.pdf",
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_process_next_marks_successful_job_completed(self) -> None:
        job = self.service.enqueue_document(
            document_id=self.document.id,
            requested_by="analyst@example.com",
            provider_name="fake",
            model="fixture-model",
        )

        result = self.service.process_next(worker_id="worker-1", handler=_successful_handler)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.succeeded)
        self.assertEqual(job.id, result.job.id)
        self.assertEqual(QUEUE_STATUS_COMPLETED, result.job.status)
        self.assertIsNotNone(result.analysis)

    def test_process_next_redacts_failed_job_errors(self) -> None:
        secret = "sk-ant-test-secret-value"
        self.service.enqueue_document(
            document_id=self.document.id,
            requested_by="analyst@example.com",
            provider_name="fake",
            model="fixture-model",
        )

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": secret}):
            result = self.service.process_next(
                worker_id="worker-1",
                handler=lambda _: _failed_handler(secret),
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.succeeded)
        self.assertEqual(QUEUE_STATUS_FAILED, result.job.status)
        self.assertNotIn(secret, result.error_message or "")


def _successful_handler(job) -> DocumentAnalysisResult:
    tracker = WorkflowUsageTracker()
    tracker.stop()
    return DocumentAnalysisResult(
        document_id=job.document_id,
        tracker=tracker,
        warnings=(),
    )


def _failed_handler(secret: str) -> DocumentAnalysisResult:
    raise RuntimeError(f"provider failed with {secret}")


if __name__ == "__main__":
    unittest.main()
