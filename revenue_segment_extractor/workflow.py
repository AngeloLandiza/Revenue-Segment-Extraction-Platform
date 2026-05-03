from __future__ import annotations

from dataclasses import dataclass, replace

from revenue_segment_extractor.extraction import (
    EsgExtractionService,
    ExtractionSettings,
    RevenueExtractionService,
    create_provider,
)
from revenue_segment_extractor.extraction.providers import LLMProviderError
from revenue_segment_extractor.extraction.usage import TrackedLLMProvider, WorkflowUsageTracker
from revenue_segment_extractor.nace import NaceMappingService
from revenue_segment_extractor.persistence.repository import SQLiteRepository


@dataclass(frozen=True)
class DocumentAnalysisResult:
    document_id: str
    tracker: WorkflowUsageTracker
    warnings: tuple[str, ...]


def run_document_analysis(
    repository: SQLiteRepository,
    document_id: str,
    *,
    provider_name: str | None = None,
    model: str | None = None,
) -> DocumentAnalysisResult:
    settings = ExtractionSettings.from_env()
    if provider_name is not None:
        settings = replace(settings, provider_name=provider_name)
    if model is not None:
        settings = replace(settings, model=model)

    tracker = WorkflowUsageTracker()
    provider = TrackedLLMProvider(create_provider(settings.provider_name), tracker)
    warnings: list[str] = []

    try:
        RevenueExtractionService(
            repository,
            provider,
            settings,
            verification_provider=provider,
            arbitration_provider=provider,
        ).extract_document(document_id)

        try:
            NaceMappingService(
                repository,
                provider=provider,
                model=settings.model,
            ).map_document(document_id)
        except (FileNotFoundError, ValueError, LLMProviderError) as exc:
            warnings.append(f"NACE mapping skipped: {exc}")

        EsgExtractionService(repository, provider, settings).extract_document(document_id)
    finally:
        tracker.stop()

    return DocumentAnalysisResult(
        document_id=document_id,
        tracker=tracker,
        warnings=tuple(warnings),
    )
