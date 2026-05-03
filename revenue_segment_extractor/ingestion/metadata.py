from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class MetadataPage(Protocol):
    page_number: int
    text: str


@dataclass(frozen=True)
class InferredDocumentMetadata:
    company_name: str | None = None
    fiscal_period: str | None = None
    currency: str | None = None
    scale: str | None = None


COMPANY_SUFFIX_PATTERN = re.compile(
    r"\b(inc\.?|corp\.?|corporation|company|co\.?|ltd\.?|limited|plc|group|holdings|"
    r"s\.a\.|sa|ag|n\.v\.|nv|a/s|ab|oyj)\b",
    flags=re.IGNORECASE,
)
YEAR_PATTERN = re.compile(r"\b(20\d{2}|19\d{2})\b")
YEAR_ENDED_PATTERN = re.compile(
    r"\b(?:year|fiscal year)\s+ended\b.{0,80}\b(20\d{2}|19\d{2})\b",
    flags=re.IGNORECASE,
)
ANNUAL_REPORT_PATTERN = re.compile(
    r"\b(?:annual report|form 10-k|10-k|integrated report)\b.{0,60}\b(20\d{2}|19\d{2})\b",
    flags=re.IGNORECASE,
)
CURRENCY_CODES = ("USD", "EUR", "GBP", "CHF", "CAD", "AUD", "JPY", "CNY", "DKK", "SEK", "NOK")
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
SCALE_TERMS = (
    ("billions", ("billions", "billion", "bn")),
    ("millions", ("millions", "million", "m")),
    ("thousands", ("thousands", "thousand", "000")),
)
COMPANY_SKIP_TERMS = (
    "annual report",
    "form 10-k",
    "integrated report",
    "table of contents",
    "contents",
    "financial statements",
    "consolidated",
    "management discussion",
    "page ",
)
FINANCIAL_CONTEXT_TERMS = (
    "revenue",
    "revenues",
    "sales",
    "turnover",
    "income",
    "segment",
    "segments",
    "operating",
    "reportable",
)


def infer_document_metadata(
    pages: list[MetadataPage],
    *,
    pdf_path: str | Path | None = None,
) -> InferredDocumentMetadata:
    first_pages = pages[:5]
    first_page_lines = _meaningful_lines(first_pages[:2])
    context_text = "\n".join(page.text for page in first_pages)
    financial_context = "\n".join(_financial_context_lines(first_pages))

    return InferredDocumentMetadata(
        company_name=_infer_company_name(first_page_lines, pdf_path),
        fiscal_period=_infer_fiscal_period(context_text),
        currency=_infer_currency(financial_context or context_text),
        scale=_infer_scale(financial_context or context_text),
    )


def _infer_company_name(lines: list[str], pdf_path: str | Path | None) -> str | None:
    for line in lines[:20]:
        if _looks_like_company_name(line):
            return line

    if pdf_path is None:
        return None
    stem = Path(pdf_path).stem.replace("_", " ").replace("-", " ").strip()
    cleaned = re.sub(r"\b(annual|report|form|10k|10-k|fy|20\d{2}|19\d{2})\b", "", stem, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._-")
    if cleaned and COMPANY_SUFFIX_PATTERN.search(cleaned):
        return cleaned
    return None


def _looks_like_company_name(line: str) -> bool:
    normalized = line.strip()
    if len(normalized) < 3 or len(normalized) > 90:
        return False
    lowered = normalized.casefold()
    if any(term in lowered for term in COMPANY_SKIP_TERMS):
        return False
    if YEAR_PATTERN.fullmatch(normalized):
        return False
    return bool(COMPANY_SUFFIX_PATTERN.search(normalized))


def _infer_fiscal_period(text: str) -> str | None:
    for pattern in (YEAR_ENDED_PATTERN, ANNUAL_REPORT_PATTERN):
        match = pattern.search(text)
        if match:
            return f"FY{match.group(1)}"

    lines = _clean_lines(text.splitlines())
    for line in lines[:30]:
        lowered = line.casefold()
        if "annual report" in lowered or "form 10-k" in lowered or "integrated report" in lowered:
            match = YEAR_PATTERN.search(line)
            if match:
                return f"FY{match.group(1)}"
    return None


def _infer_currency(text: str) -> str | None:
    upper_text = text.upper()
    code_counts = {
        code: len(re.findall(rf"\b{re.escape(code)}\b", upper_text)) for code in CURRENCY_CODES
    }
    best_code, best_count = max(code_counts.items(), key=lambda item: item[1])
    if best_count > 0 and list(code_counts.values()).count(best_count) == 1:
        return best_code

    symbol_counts = {code: text.count(symbol) for symbol, code in CURRENCY_SYMBOLS.items()}
    best_symbol_code, best_symbol_count = max(symbol_counts.items(), key=lambda item: item[1])
    if best_symbol_count > 0 and list(symbol_counts.values()).count(best_symbol_count) == 1:
        return best_symbol_code
    return None


def _infer_scale(text: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    counts: dict[str, int] = {}
    for scale, terms in SCALE_TERMS:
        counts[scale] = sum(len(re.findall(rf"\b{re.escape(term)}\b", normalized)) for term in terms)
    best_scale, best_count = max(counts.items(), key=lambda item: item[1])
    if best_count > 0 and list(counts.values()).count(best_count) == 1:
        return best_scale
    return None


def _financial_context_lines(pages: list[MetadataPage]) -> list[str]:
    lines: list[str] = []
    for line in _meaningful_lines(pages):
        lowered = line.casefold()
        if any(term in lowered for term in FINANCIAL_CONTEXT_TERMS):
            lines.append(line)
    return lines


def _meaningful_lines(pages: list[MetadataPage]) -> list[str]:
    lines: list[str] = []
    for page in pages:
        lines.extend(_clean_lines(page.text.splitlines()))
    return lines


def _clean_lines(lines: list[str]) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in lines if line.strip()]
