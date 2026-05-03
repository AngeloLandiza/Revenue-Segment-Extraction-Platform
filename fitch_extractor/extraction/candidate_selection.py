from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from fitch_extractor.models import PageCandidate, ParsedPage


REVENUE_SEGMENT_TERMS = {
    "business segments",
    "disaggregation of revenue",
    "external revenue",
    "net sales by segment",
    "operating segments",
    "reportable segments",
    "revenue by segment",
    "sales by segment",
    "segment reporting",
    "segment revenue",
    "segment revenues",
}

WEAK_TERMS = {
    "management discussion and analysis",
    "md&a",
    "note",
}

NON_SEGMENT_DISCLOSURE_TERMS = {
    "accounting and reporting developments",
    "accounting changes",
    "accounting standard",
    "allocation table",
    "climate impact",
    "emissions",
    "collateral",
    "coverage ratio",
    "credit risk",
    "default portfolio",
    "energy consumption",
    "energy intensity",
    "energy usage",
    "energy mix",
    "eu taxonomy",
    "fasb",
    "fuel consumption",
    "green asset portfolio",
    "green financing",
    "taxonomy",
}

NON_OPERATING_SEGMENT_SECTION_TERMS = {
    "geographical segmentation",
    "geographic segmentation",
    "geographic segment",
    "product segmentation",
    "product segment",
}

GENERIC_REVENUE_LINE_ITEM_LABELS = {
    "external revenue",
    "net revenue",
    "net revenues",
    "net sales",
    "revenue",
    "revenues",
    "sales",
    "total consolidated revenue",
    "total consolidated revenues",
    "turnover",
}


def select_extraction_candidates(
    candidates: Sequence[PageCandidate],
    parsed_pages: Sequence[ParsedPage],
) -> list[PageCandidate]:
    pages_by_number = {page.page_number: page for page in parsed_pages}
    anchored_candidates = [
        candidate
        for candidate in candidates
        if _is_revenue_segment_anchor(candidate, pages_by_number.get(candidate.page_number))
    ]
    if not anchored_candidates:
        return []

    canonical_candidates = [
        candidate
        for candidate in anchored_candidates
        if _is_financial_statement_page(pages_by_number.get(candidate.page_number))
    ]
    segment_note_candidates = [
        candidate
        for candidate in canonical_candidates
        if not _is_product_or_geography_section(_normalize(pages_by_number[candidate.page_number].text))
    ]
    if segment_note_candidates:
        return segment_note_candidates
    return canonical_candidates or anchored_candidates


def is_non_segment_revenue_row(
    *,
    segment_name: str,
    evidence_text: str,
    metric_basis: str | None,
    section_ref: str | None,
    page_text: str | None,
) -> bool:
    if _is_generic_revenue_line_item(segment_name, metric_basis=metric_basis):
        return True

    combined_text = _normalize(
        " ".join([segment_name, evidence_text, section_ref or "", page_text or ""])
    )
    if _is_generic_revenue_line_item(segment_name, metric_basis=combined_text):
        return True
    if not any(term in combined_text for term in NON_SEGMENT_DISCLOSURE_TERMS):
        return False
    return not _has_segment_note_context(combined_text)


def _is_generic_revenue_line_item(segment_name: str, *, metric_basis: str | None) -> bool:
    normalized_segment = _normalize(segment_name)
    if normalized_segment not in GENERIC_REVENUE_LINE_ITEM_LABELS:
        return False

    normalized_metric = _normalize(metric_basis or "")
    return (
        not normalized_metric
        or "not a segment" in normalized_metric
        or "not segment" in normalized_metric
        or "consolidated revenue" in normalized_metric
        or "consolidated revenues" in normalized_metric
        or normalized_segment == normalized_metric
    )


def _is_revenue_segment_anchor(candidate: PageCandidate, page: ParsedPage | None) -> bool:
    if page is None or is_table_of_contents_page(page.text) or is_non_segment_page(page.text):
        return False

    normalized_text = _normalize(page.text)
    if _is_accounting_update_page(normalized_text):
        return False
    if _is_non_operating_segment_section(normalized_text):
        return False

    matched_terms = _matched_terms(candidate)
    strong_matched_terms = matched_terms & REVENUE_SEGMENT_TERMS
    if strong_matched_terms:
        return True

    if _has_segment_note_context(normalized_text) and _has_revenue_context(normalized_text):
        return True

    return _has_structural_revenue_table_signal(candidate)


def is_non_segment_page(text: str) -> bool:
    normalized_text = _normalize(text)
    if not any(term in normalized_text for term in NON_SEGMENT_DISCLOSURE_TERMS):
        return False
    return not _has_segment_note_context(normalized_text)


def is_table_of_contents_page(text: str) -> bool:
    normalized_text = _normalize(text)
    if _has_note_segment_heading(normalized_text):
        return False
    lines = [_normalize(line) for line in text.splitlines()[:100]]
    non_empty_lines = [line for line in lines if line]
    if not non_empty_lines:
        return False

    has_contents_heading = any(
        line in {"content", "contents", "table of contents"}
        or line.replace(" ", "") in {"content", "contents", "tableofcontents"}
        for line in non_empty_lines[:8]
    )
    standalone_page_refs = sum(1 for line in non_empty_lines if re.fullmatch(r"\d{1,4}", line))
    section_terms = {
        "assets",
        "capital",
        "financial statements",
        "income",
        "management discussion",
        "notes",
        "overview",
        "risk",
        "segment",
    }
    section_term_count = sum(
        1 for line in non_empty_lines if any(term in line for term in section_terms)
    )
    has_unit_marker = bool(
        re.search(r"\b(chf|dkk|eur|gbp|sek|usd)\b|[$€£]", text.lower())
    )

    return not has_unit_marker and (
        ("table of contents" in normalized_text and has_contents_heading)
        or (has_contents_heading and standalone_page_refs >= 5)
        or (standalone_page_refs >= 8 and section_term_count >= 4)
    )


def _is_financial_statement_page(page: ParsedPage | None) -> bool:
    if page is None:
        return False
    header_lines = [_normalize(line) for line in page.text.splitlines()[:5]]
    return any(
        line == "financial statements"
        or line.startswith("financial statements ")
        or line.endswith("| financial statements")
        for line in header_lines
    )


def _is_non_operating_segment_section(normalized_text: str) -> bool:
    has_non_operating_section = any(
        term in normalized_text for term in NON_OPERATING_SEGMENT_SECTION_TERMS
    )
    return (
        has_non_operating_section
        and not _has_segment_note_context(normalized_text)
        and not _has_revenue_context(normalized_text)
    )


def _is_product_or_geography_section(normalized_text: str) -> bool:
    return any(term in normalized_text for term in NON_OPERATING_SEGMENT_SECTION_TERMS) and not _has_segment_note_context(normalized_text)


def _is_accounting_update_page(normalized_text: str) -> bool:
    has_accounting_update = any(
        term in normalized_text
        for term in {
            "accounting and reporting developments",
            "accounting changes",
            "standards adopted",
            "standards issued",
            "fasb",
        }
    )
    has_actual_segment_note = any(
        term in normalized_text
        for term in {
            "business segments & corporate",
            "business segments and corporate",
            "description of business segment reporting",
        }
    )
    return has_accounting_update and not has_actual_segment_note


def _matched_terms(candidate: PageCandidate) -> set[str]:
    terms = candidate.matched_signals_json.get("terms", {})
    if not isinstance(terms, list):
        return set()

    matched_terms: set[str] = set()
    for term in terms:
        if isinstance(term, dict):
            raw_term = term.get("term")
        else:
            raw_term = term
        if raw_term is None:
            continue
        normalized_term = _normalize(str(raw_term))
        if normalized_term not in WEAK_TERMS:
            matched_terms.add(normalized_term)
    return matched_terms


def _has_structural_revenue_table_signal(candidate: PageCandidate) -> bool:
    signals = candidate.matched_signals_json
    table_signal = _signal_dict(signals.get("table_density"))
    numeric_signal = _signal_dict(signals.get("numeric_density"))

    return (
        _signal_count(table_signal, "table_count") > 0
        and _signal_count(table_signal, "cell_count") >= 12
        and _signal_count(numeric_signal, "numeric_count") >= 6
    )


def _signal_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _signal_count(signal: dict, key: str) -> int:
    value = signal.get(key, 0)
    return value if isinstance(value, int) else 0


def _has_segment_note_context(normalized_text: str) -> bool:
    return bool(
        re.search(
            r"\b(note\s+\d+\s+segment information|primary segmentation|reporting segments|"
            r"operating segments|reportable segments|segment reporting)\b",
            normalized_text,
        )
    )


def _has_note_segment_heading(normalized_text: str) -> bool:
    return bool(
        re.search(
            r"(\(\d+\)\s+segment reporting|\bnote\s+\d+\s+segment information)",
            normalized_text,
        )
    )


def _has_revenue_context(normalized_text: str) -> bool:
    return bool(
        re.search(
            r"\b(external income|external revenue|net revenue|revenue|sales|turnover)\b",
            normalized_text,
        )
    )


def _normalize(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()
