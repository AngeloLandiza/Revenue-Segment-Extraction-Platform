from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from revenue_segment_extractor.ingestion.page_relevance import (
    DEFAULT_CANDIDATE_LIMIT,
    score_pages,
    select_candidate_pages,
)
from revenue_segment_extractor.ingestion.fallbacks import (
    PageTextFallbackProvider,
    create_page_text_fallback_provider,
)
from revenue_segment_extractor.ingestion.metadata import InferredDocumentMetadata, infer_document_metadata
from revenue_segment_extractor.ingestion.pdf_parser import parse_pdf
from revenue_segment_extractor.models import Document, PageCandidate, SerializableModel
from revenue_segment_extractor.persistence.repository import SQLiteRepository


@dataclass(frozen=True)
class IngestionSummary(SerializableModel):
    document: Document
    page_count: int
    parsed_page_count: int
    candidate_count: int
    no_text_pages: tuple[int, ...]
    candidate_pages: tuple[PageCandidate, ...]


class PdfIngestionService:
    def __init__(
        self,
        repository: SQLiteRepository,
        fallback_provider: PageTextFallbackProvider | None = None,
    ) -> None:
        self.repository = repository
        self.fallback_provider = fallback_provider or create_page_text_fallback_provider()

    def ingest_pdf(
        self,
        *,
        pdf_path: str | Path,
        company_name: str | None = None,
        document_name: str | None = None,
        fiscal_period: str | None = None,
        reported_total: Decimal | None = None,
        currency: str | None = None,
        scale: str | None = None,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> IngestionSummary:
        path = Path(pdf_path).expanduser().resolve()
        stored_document_name = (document_name or path.name).strip()
        if not stored_document_name:
            raise ValueError("document_name must not be empty")

        parsed_pdf_pages = parse_pdf(path, fallback_provider=self.fallback_provider)
        inferred = infer_document_metadata(parsed_pdf_pages, pdf_path=path)
        resolved_metadata = _resolve_metadata(
            inferred=inferred,
            company_name=company_name,
            fiscal_period=fiscal_period,
            currency=currency,
            scale=scale,
        )
        if not resolved_metadata.company_name:
            raise ValueError(
                "company_name could not be auto-detected; provide it manually for this document"
            )
        page_scores = score_pages(parsed_pdf_pages)
        selected_candidates = select_candidate_pages(page_scores, limit=candidate_limit)

        document = self.repository.create_document(
            company_name=resolved_metadata.company_name,
            document_name=stored_document_name,
            source_path=str(path),
            fiscal_period=resolved_metadata.fiscal_period,
            reported_total=reported_total,
            currency=resolved_metadata.currency,
            scale=resolved_metadata.scale,
        )
        self.repository.clear_parsed_outputs(document.id)

        parsed_pages = [
            self.repository.create_parsed_page(
                document_id=document.id,
                page_number=page.page_number,
                text=page.text,
                blocks_json=page.blocks_json,
                tables_json=page.tables_json,
                language=page.language,
                parser_sources=page.parser_sources,
                has_text=page.has_text,
            )
            for page in parsed_pdf_pages
        ]
        candidate_pages = [
            self.repository.create_page_candidate(
                document_id=document.id,
                page_number=candidate.page_number,
                relevance_score=candidate.relevance_score,
                matched_signals_json=candidate.matched_signals,
                reason=candidate.reason,
            )
            for candidate in selected_candidates
        ]

        return IngestionSummary(
            document=document,
            page_count=len(parsed_pdf_pages),
            parsed_page_count=len(parsed_pages),
            candidate_count=len(candidate_pages),
            no_text_pages=tuple(page.page_number for page in parsed_pdf_pages if not page.has_text),
            candidate_pages=tuple(candidate_pages),
        )


def _resolve_metadata(
    *,
    inferred: InferredDocumentMetadata,
    company_name: str | None,
    fiscal_period: str | None,
    currency: str | None,
    scale: str | None,
) -> InferredDocumentMetadata:
    return InferredDocumentMetadata(
        company_name=_clean_hint(company_name) or inferred.company_name,
        fiscal_period=_clean_hint(fiscal_period) or inferred.fiscal_period,
        currency=_clean_hint(currency) or inferred.currency,
        scale=_clean_hint(scale) or inferred.scale,
    )


def _clean_hint(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
