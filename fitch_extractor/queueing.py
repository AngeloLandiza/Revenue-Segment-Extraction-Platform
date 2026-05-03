from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fitch_extractor.extraction.providers import _redact_sensitive
from fitch_extractor.models import DocumentQueueJob
from fitch_extractor.persistence.repository import SQLiteRepository
from fitch_extractor.workflow import DocumentAnalysisResult, run_document_analysis


QueueHandler = Callable[[DocumentQueueJob], DocumentAnalysisResult]


@dataclass(frozen=True)
class QueueProcessingResult:
    job: DocumentQueueJob
    analysis: DocumentAnalysisResult | None
    error_message: str | None

    @property
    def succeeded(self) -> bool:
        return self.error_message is None


class DocumentQueueService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def enqueue_document(
        self,
        *,
        document_id: str,
        requested_by: str,
        provider_name: str,
        model: str | None,
    ) -> DocumentQueueJob:
        return self.repository.create_document_queue_job(
            document_id=document_id,
            requested_by=requested_by or "unknown_reviewer",
            provider_name=provider_name,
            model=model,
        )

    def process_next(
        self,
        *,
        worker_id: str,
        handler: QueueHandler | None = None,
    ) -> QueueProcessingResult | None:
        job = self.repository.claim_next_document_queue_job(worker_id=worker_id)
        if job is None:
            return None

        job_handler = handler or self._run_job
        try:
            analysis = job_handler(job)
        except Exception as exc:
            message = _redact_sensitive(str(exc))
            failed_job = self.repository.fail_document_queue_job(job.id, message)
            return QueueProcessingResult(
                job=failed_job,
                analysis=None,
                error_message=message,
            )

        completed_job = self.repository.complete_document_queue_job(job.id)
        return QueueProcessingResult(
            job=completed_job,
            analysis=analysis,
            error_message=None,
        )

    def _run_job(self, job: DocumentQueueJob) -> DocumentAnalysisResult:
        return run_document_analysis(
            self.repository,
            job.document_id,
            provider_name=job.provider_name,
            model=job.model,
        )
