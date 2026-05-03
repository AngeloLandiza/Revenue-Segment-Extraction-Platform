from revenue_segment_extractor.ingestion.evidence import locate_evidence_snippet
from revenue_segment_extractor.ingestion.fallbacks import (
    CallablePageTextFallbackProvider,
    CallableVisionTextFallbackProvider,
    FakePageTextFallbackProvider,
    PageTextFallbackProvider,
    PageTextFallbackResult,
    PageTextFallbackSettings,
    TesseractCliOcrProvider,
    create_page_text_fallback_provider,
)
from revenue_segment_extractor.ingestion.page_relevance import (
    PageRelevance,
    score_pages,
    select_candidate_pages,
)
from revenue_segment_extractor.ingestion.metadata import (
    InferredDocumentMetadata,
    infer_document_metadata,
)
from revenue_segment_extractor.ingestion.pdf_parser import (
    ParsedPdfPage,
    is_low_text_page,
    parse_pdf,
    render_page_to_png,
    render_page_with_bbox_to_png,
)
from revenue_segment_extractor.ingestion.service import IngestionSummary, PdfIngestionService

__all__ = [
    "CallablePageTextFallbackProvider",
    "CallableVisionTextFallbackProvider",
    "FakePageTextFallbackProvider",
    "InferredDocumentMetadata",
    "IngestionSummary",
    "PageRelevance",
    "PageTextFallbackProvider",
    "PageTextFallbackResult",
    "PageTextFallbackSettings",
    "ParsedPdfPage",
    "PdfIngestionService",
    "TesseractCliOcrProvider",
    "create_page_text_fallback_provider",
    "is_low_text_page",
    "locate_evidence_snippet",
    "infer_document_metadata",
    "parse_pdf",
    "render_page_to_png",
    "render_page_with_bbox_to_png",
    "score_pages",
    "select_candidate_pages",
]
