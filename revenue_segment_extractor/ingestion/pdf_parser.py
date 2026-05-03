from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import pdfplumber

from revenue_segment_extractor.ingestion.fallbacks import (
    CallablePageTextFallbackProvider,
    PageTextFallbackError,
    PageTextFallbackProvider,
    PageTextFallbackResult,
)


MIN_MEANINGFUL_TEXT_CHARS = 30
PageTextFallback = Callable[[Path, int], str | None]

_LANGUAGE_MARKERS = {
    "en": (
        "operating segments",
        "reportable segments",
        "business segments",
        "revenue",
        "sales",
        "turnover",
    ),
    "es": (
        "segmentos operativos",
        "segmentos de negocio",
        "ingresos",
        "ventas",
        "cifra de negocios",
        "facturacion",
    ),
    "fr": (
        "secteurs operationnels",
        "segments operationnels",
        "secteurs d activite",
        "chiffre d affaires",
        "produits",
        "ventes",
    ),
    "de": (
        "operative segmente",
        "berichtspflichtige segmente",
        "geschaeftssegmente",
        "geschaftssegmente",
        "umsatz",
        "erloese",
        "erlose",
    ),
    "it": (
        "segmenti operativi",
        "settori operativi",
        "ricavi",
        "vendite",
        "fatturato",
    ),
    "pt": (
        "segmentos operacionais",
        "segmentos de negocio",
        "receita",
        "receitas",
        "vendas",
        "volume de negocios",
    ),
}


@dataclass(frozen=True)
class ParsedPdfPage:
    page_number: int
    text: str
    blocks_json: dict[str, Any]
    tables_json: dict[str, Any]
    language: str
    parser_sources: tuple[str, ...]
    has_text: bool


def parse_pdf(
    pdf_path: str | Path,
    *,
    text_fallback: PageTextFallback | None = None,
    fallback_provider: PageTextFallbackProvider | None = None,
) -> list[ParsedPdfPage]:
    path = _validate_pdf_path(pdf_path)
    parsed_pages: list[ParsedPdfPage] = []
    resolved_fallback_provider = fallback_provider
    if resolved_fallback_provider is None and text_fallback is not None:
        resolved_fallback_provider = CallablePageTextFallbackProvider(text_fallback)

    with fitz.open(path) as pymupdf_document, pdfplumber.open(path) as plumber_document:
        for page_index, page in enumerate(pymupdf_document):
            page_number = page_index + 1
            text = _normalize_page_text(page.get_text("text"))
            has_text = not is_low_text_page(text)
            fallback_status = _fallback_status("not_needed")
            parser_sources = ["pymupdf", "pdfplumber"]
            fallback_result: PageTextFallbackResult | None = None

            if not has_text:
                fallback_status = _fallback_status(
                    "available_not_configured",
                    reason="Page has too little extractable text for reliable parsing.",
                )
                if resolved_fallback_provider is not None:
                    fallback_result, fallback_status = _run_fallback_provider(
                        resolved_fallback_provider,
                        path,
                        page_number,
                    )
                    fallback_text = _normalize_page_text(
                        fallback_result.text if fallback_result is not None else ""
                    )
                    if not is_low_text_page(fallback_text):
                        text = fallback_text
                        has_text = True
                        assert fallback_result is not None
                        parser_sources.append(fallback_result.parser_source)

            plumber_page = (
                plumber_document.pages[page_index]
                if page_index < len(plumber_document.pages)
                else None
            )
            blocks = _extract_text_blocks(page)
            if fallback_result is not None and fallback_status["status"] == "applied":
                blocks.append(_fallback_text_block(fallback_result))
            parsed_pages.append(
                ParsedPdfPage(
                    page_number=page_number,
                    text=text,
                    blocks_json={
                        "page": _page_dimensions(page),
                        "blocks": blocks,
                        "text_fallback": fallback_status,
                    },
                    tables_json=_extract_tables(plumber_page),
                    language=detect_language(text),
                    parser_sources=tuple(parser_sources),
                    has_text=has_text,
                )
            )

    return parsed_pages


def render_page_to_png(
    pdf_path: str | Path,
    page_number: int,
    output_path: str | Path,
    *,
    zoom: float = 2.0,
) -> Path:
    path = _validate_pdf_path(pdf_path)
    if page_number < 1:
        raise ValueError("page_number must be 1-based")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(path) as document:
        if page_number > document.page_count:
            raise ValueError(f"page_number {page_number} exceeds page count {document.page_count}")
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pixmap.save(output)

    return output


def render_page_with_bbox_to_png(
    pdf_path: str | Path,
    page_number: int,
    output_path: str | Path,
    bbox: dict[str, Any],
    *,
    zoom: float = 2.0,
) -> Path:
    path = _validate_pdf_path(pdf_path)
    if page_number < 1:
        raise ValueError("page_number must be 1-based")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(path) as document:
        if page_number > document.page_count:
            raise ValueError(f"page_number {page_number} exceeds page count {document.page_count}")
        page = document[page_number - 1]
        rect = _bbox_rect(bbox, page.rect)
        page.draw_rect(
            rect,
            color=(0.95, 0.05, 0.05),
            fill=(1.0, 0.9, 0.1),
            width=2.0,
            stroke_opacity=0.95,
            fill_opacity=0.22,
            overlay=True,
        )
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pixmap.save(output)

    return output


def detect_language(_text: str) -> str:
    normalized_text = _normalize_for_language(_text)
    if not normalized_text:
        return "unknown"

    scores = {
        language: sum(1 for marker in markers if marker in normalized_text)
        for language, markers in _LANGUAGE_MARKERS.items()
    }
    best_language, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score == 0:
        return "unknown"
    tied_languages = [
        language for language, score in scores.items() if score == best_score
    ]
    return best_language if len(tied_languages) == 1 else "unknown"


def is_low_text_page(text: str) -> bool:
    alnum_count = sum(1 for character in text if character.isalnum())
    return alnum_count < MIN_MEANINGFUL_TEXT_CHARS


def _validate_pdf_path(pdf_path: str | Path) -> Path:
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {path}")
    return path


def _normalize_page_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _normalize_for_language(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def _fallback_status(
    status: str,
    *,
    provider_name: str | None = None,
    parser_source: str | None = None,
    reason: str | None = None,
    error: str | None = None,
    warnings: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "status": status,
        "provider_name": provider_name,
        "parser_source": parser_source,
        "reason": reason,
        "error": error,
        "warnings": list(warnings),
        "extension_point": "Optional OCR or vision providers can populate low-text pages.",
    }


def _run_fallback_provider(
    provider: PageTextFallbackProvider,
    pdf_path: Path,
    page_number: int,
) -> tuple[PageTextFallbackResult | None, dict[str, Any]]:
    try:
        result = provider.extract_text(pdf_path, page_number)
    except PageTextFallbackError as exc:
        return None, _fallback_status(
            "failed",
            provider_name=provider.name,
            parser_source=provider.parser_source,
            error=str(exc),
        )
    except Exception as exc:
        return None, _fallback_status(
            "failed",
            provider_name=provider.name,
            parser_source=provider.parser_source,
            error=f"{type(exc).__name__}: {exc}",
        )

    if result is None:
        return None, _fallback_status(
            "attempted_no_text",
            provider_name=provider.name,
            parser_source=provider.parser_source,
            reason="Provider returned no text.",
        )

    normalized_text = _normalize_page_text(result.text)
    if is_low_text_page(normalized_text):
        return result, _fallback_status(
            "attempted_no_text",
            provider_name=result.provider_name,
            parser_source=result.parser_source,
            reason="Provider text was below the low-text threshold.",
            warnings=result.warnings,
        )

    return result, _fallback_status(
        "applied",
        provider_name=result.provider_name,
        parser_source=result.parser_source,
        reason="Fallback text replaced low-text parser output.",
        warnings=result.warnings,
    )


def _page_dimensions(page: fitz.Page) -> dict[str, float | int]:
    rect = page.rect
    return {
        "width": round(rect.width, 3),
        "height": round(rect.height, 3),
        "rotation": int(page.rotation),
    }


def _extract_text_blocks(page: fitz.Page) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in page.get_text("blocks"):
        if len(block) < 7:
            continue
        x0, y0, x1, y1, text, block_number, block_type = block[:7]
        normalized_text = _normalize_page_text(str(text))
        if not normalized_text:
            continue
        blocks.append(
            {
                "block_index": int(block_number),
                "block_type": int(block_type),
                "bbox": _bbox_dict(x0, y0, x1, y1),
                "text": normalized_text,
                "parser_source": "pymupdf",
            }
        )
    return blocks


def _fallback_text_block(result: PageTextFallbackResult) -> dict[str, Any]:
    return {
        "block_index": "fallback_text",
        "block_type": "fallback_text",
        "bbox": None,
        "text": _normalize_page_text(result.text),
        "parser_source": result.parser_source,
        "provider_name": result.provider_name,
    }


def _extract_tables(plumber_page: Any | None) -> dict[str, Any]:
    if plumber_page is None:
        return {"tables": [], "errors": ["pdfplumber page was not available"]}

    tables: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        found_tables = plumber_page.find_tables()
    except Exception as exc:  # pdfplumber may fail on malformed page geometry.
        found_tables = []
        errors.append(f"find_tables failed: {type(exc).__name__}: {exc}")

    if found_tables:
        for table_index, table in enumerate(found_tables):
            rows = _clean_rows(table.extract())
            tables.append(
                {
                    "table_index": table_index,
                    "source": "pdfplumber.find_tables",
                    "bbox": _bbox_from_pdfplumber(getattr(table, "bbox", None)),
                    "rows": rows,
                    "cells": _extract_table_cells(table, rows),
                }
            )
    else:
        try:
            extracted_tables = plumber_page.extract_tables() or []
        except Exception as exc:  # Keep page parsing usable while recording parser failure.
            extracted_tables = []
            errors.append(f"extract_tables failed: {type(exc).__name__}: {exc}")
        for table_index, rows in enumerate(extracted_tables):
            tables.append(
                {
                    "table_index": table_index,
                    "source": "pdfplumber.extract_tables",
                    "bbox": None,
                    "rows": _clean_rows(rows),
                    "cells": [],
                }
            )

    return {"tables": tables, "errors": errors}


def _clean_rows(rows: list[list[Any | None]] | None) -> list[list[str]]:
    if not rows:
        return []
    return [
        ["" if cell is None else str(cell).strip() for cell in row]
        for row in rows
    ]


def _extract_table_cells(table: Any, rows: list[list[str]]) -> list[dict[str, Any]]:
    raw_cells = list(getattr(table, "cells", []) or [])
    if not raw_cells:
        return []

    row_count = len(rows)
    column_count = max((len(row) for row in rows), default=0)
    structured_cells: list[dict[str, Any]] = []

    if row_count and column_count and len(raw_cells) >= row_count * column_count:
        for cell_index, raw_bbox in enumerate(raw_cells[: row_count * column_count]):
            row_index = cell_index // column_count
            column_index = cell_index % column_count
            structured_cells.append(
                {
                    "row_index": row_index,
                    "column_index": column_index,
                    "text": rows[row_index][column_index],
                    "bbox": _bbox_from_pdfplumber(raw_bbox),
                }
            )
        return structured_cells

    for cell_index, raw_bbox in enumerate(raw_cells):
        structured_cells.append(
            {
                "cell_index": cell_index,
                "bbox": _bbox_from_pdfplumber(raw_bbox),
            }
        )
    return structured_cells


def _bbox_from_pdfplumber(raw_bbox: Any) -> dict[str, float] | None:
    if raw_bbox is None:
        return None
    if isinstance(raw_bbox, dict):
        x0 = raw_bbox.get("x0")
        y0 = raw_bbox.get("top", raw_bbox.get("y0"))
        x1 = raw_bbox.get("x1")
        y1 = raw_bbox.get("bottom", raw_bbox.get("y1"))
    elif isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) >= 4:
        x0, y0, x1, y1 = raw_bbox[:4]
    else:
        return None
    return _bbox_dict(x0, y0, x1, y1)


def _bbox_dict(x0: Any, y0: Any, x1: Any, y1: Any) -> dict[str, float]:
    return {
        "x0": round(float(x0), 3),
        "y0": round(float(y0), 3),
        "x1": round(float(x1), 3),
        "y1": round(float(y1), 3),
    }


def _bbox_rect(bbox: dict[str, Any], page_rect: fitz.Rect) -> fitz.Rect:
    try:
        rect = fitz.Rect(
            float(bbox["x0"]),
            float(bbox["y0"]),
            float(bbox["x1"]),
            float(bbox["y1"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("bbox must include numeric x0, y0, x1, and y1 values") from exc

    clipped = rect & page_rect
    if clipped.is_empty or clipped.is_infinite:
        raise ValueError("bbox is outside the page bounds")
    return clipped
