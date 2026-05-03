from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol


DEFAULT_CANDIDATE_LIMIT = 15
ADJACENT_SOURCE_THRESHOLD = 4.0
ADJACENT_SCORE_FACTOR = 0.35

TERM_WEIGHTS = {
    "revenue by segment": 9.0,
    "net sales by segment": 9.0,
    "operating segments": 8.5,
    "reportable segments": 8.5,
    "segment reporting": 7.5,
    "business segments": 7.0,
    "external revenue": 7.0,
    "disaggregation of revenue": 7.0,
    "segment revenue": 6.5,
    "segment revenues": 6.5,
    "sales by segment": 6.5,
    "management discussion and analysis": 2.5,
    "md&a": 2.5,
    "note": 0.8,
    "segmentos operativos": 6.5,
    "ingresos por segmento": 6.5,
    "ventas por segmento": 6.0,
    "cifra de negocios": 5.5,
    "segmentos de negocio": 5.5,
    "segmentos reportables": 5.5,
    "secteurs operationnels": 6.5,
    "segments operationnels": 6.5,
    "chiffre d affaires": 6.0,
    "chiffre d affaires par secteur": 6.5,
    "secteurs d activite": 5.5,
    "segments a presenter": 5.5,
    "umsatz nach segment": 6.5,
    "umsatzerloese": 6.0,
    "umsatzerlose": 6.0,
    "operative segmente": 6.0,
    "berichtspflichtige segmente": 6.0,
    "geschaeftssegmente": 6.0,
    "geschaftssegmente": 6.0,
    "segmentberichterstattung": 6.0,
    "ricavi per settore": 6.5,
    "settori operativi": 6.0,
    "segmenti operativi": 6.0,
    "fatturato": 5.5,
    "receita por segmento": 6.5,
    "receitas por segmento": 6.5,
    "segmentos operacionais": 6.0,
    "volume de negocios": 5.5,
    "omzet per segment": 6.5,
    "operationele segmenten": 6.0,
}

BUSINESS_LINE_TERMS = {
    "automotive",
    "banking",
    "commercial",
    "consumer",
    "corporate",
    "energy",
    "industrial",
    "insurance",
    "investment",
    "retail",
    "technology",
    "wholesale",
}

CURRENCY_UNIT_TERMS = {
    "$",
    "usd",
    "eur",
    "euro",
    "gbp",
    "dollar",
    "dollars",
    "million",
    "millions",
    "millones",
    "millionen",
    "milhoes",
    "billion",
    "billions",
    "thousand",
    "thousands",
}

TOTAL_TERMS = {
    "total revenue",
    "total revenues",
    "total sales",
    "total net sales",
    "consolidated revenue",
    "consolidated revenues",
    "intersegment",
    "total",
    "total ingresos",
    "ingresos totales",
    "ventas totales",
    "total chiffre d affaires",
    "chiffre d affaires total",
    "total produits",
    "gesamtumsatz",
    "umsatz gesamt",
    "ricavi totali",
    "totale ricavi",
    "receita total",
    "receitas totais",
    "totaal omzet",
}


@dataclass(frozen=True)
class PageRelevance:
    page_number: int
    relevance_score: float
    matched_signals: dict[str, Any]
    reason: str


class ScorablePage(Protocol):
    page_number: int
    text: str
    tables_json: dict[str, Any]


def score_pages(pages: Sequence[ScorablePage]) -> list[PageRelevance]:
    return [_score_page(page) for page in pages]


def select_candidate_pages(
    scores: list[PageRelevance],
    *,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    include_adjacent: bool = True,
) -> list[PageRelevance]:
    if limit < 1:
        raise ValueError("limit must be at least 1")

    by_page_number = {score.page_number: score for score in scores}
    selected: dict[int, PageRelevance] = {}
    sorted_scores = sorted(scores, key=lambda item: (-item.relevance_score, item.page_number))

    for score in sorted_scores:
        if score.relevance_score <= 0 or len(selected) >= limit:
            break
        if score.page_number in selected:
            continue
        _select_candidate(selected, score)

        if include_adjacent and score.relevance_score >= ADJACENT_SOURCE_THRESHOLD:
            for adjacent_page_number in (score.page_number - 1, score.page_number + 1):
                if len(selected) >= limit:
                    break
                adjacent_score = by_page_number.get(adjacent_page_number)
                if (
                    adjacent_score
                    and adjacent_score.relevance_score > 0
                    and adjacent_page_number not in selected
                ):
                    _select_candidate(
                        selected,
                        _with_proximity_signal(adjacent_score, source_score=score),
                    )

    return sorted(selected.values(), key=lambda item: (-item.relevance_score, item.page_number))


def _score_page(page: ScorablePage) -> PageRelevance:
    text = page.text or ""
    normalized_text = _normalize_for_matching(text)
    matched_signals: dict[str, Any] = {}
    score = 0.0

    term_matches = _match_weighted_terms(normalized_text)
    if term_matches:
        score += sum(match["weight"] for match in term_matches)
        matched_signals["terms"] = term_matches

    table_signal = _table_density_signal(page.tables_json)
    if table_signal["score"] > 0:
        score += table_signal["score"]
        matched_signals["table_density"] = table_signal

    numeric_signal = _numeric_density_signal(text)
    if numeric_signal["score"] > 0:
        score += numeric_signal["score"]
        matched_signals["numeric_density"] = numeric_signal

    currency_terms = _match_terms(normalized_text, CURRENCY_UNIT_TERMS)
    if currency_terms:
        currency_score = min(3.0, len(currency_terms) * 0.75)
        score += currency_score
        matched_signals["currency_unit_terms"] = {
            "terms": currency_terms,
            "score": currency_score,
        }

    business_signal = _business_line_signal(normalized_text)
    if business_signal["score"] > 0:
        score += business_signal["score"]
        matched_signals["business_line_terms"] = business_signal

    total_terms = _match_terms(normalized_text, TOTAL_TERMS)
    if total_terms:
        total_score = min(2.0, len(total_terms) * 0.5)
        score += total_score
        matched_signals["total_terms"] = {
            "terms": total_terms,
            "score": total_score,
        }

    return PageRelevance(
        page_number=page.page_number,
        relevance_score=round(score, 3),
        matched_signals=matched_signals,
        reason=_build_reason(matched_signals),
    )


def _select_candidate(
    selected: dict[int, PageRelevance],
    score: PageRelevance,
) -> None:
    selected[score.page_number] = score


def _with_proximity_signal(
    score: PageRelevance,
    *,
    source_score: PageRelevance,
) -> PageRelevance:
    matched_signals = copy.deepcopy(score.matched_signals)
    proximity_signal = {
        "adjacent_to_page": source_score.page_number,
        "source_score": source_score.relevance_score,
        "score": round(source_score.relevance_score * ADJACENT_SCORE_FACTOR, 3),
    }
    matched_signals["proximity"] = proximity_signal

    proximity_score = max(score.relevance_score, proximity_signal["score"])
    if score.relevance_score > 0:
        reason = (
            f"{score.reason} Included because page {source_score.page_number} "
            "has strong segment disclosure signals."
        )
    else:
        reason = (
            f"Adjacent to page {source_score.page_number}, which has strong "
            "segment disclosure signals."
        )
    return replace(
        score,
        relevance_score=round(proximity_score, 3),
        matched_signals=matched_signals,
        reason=reason,
    )


def _match_weighted_terms(normalized_text: str) -> list[dict[str, float | str]]:
    matches: list[dict[str, float | str]] = []
    for term, weight in TERM_WEIGHTS.items():
        if _term_matches(normalized_text, term):
            matches.append({"term": term, "weight": weight})
    return matches


def _match_terms(normalized_text: str, terms: set[str]) -> list[str]:
    return sorted(term for term in terms if _term_matches(normalized_text, term))


def _term_matches(normalized_text: str, term: str) -> bool:
    if term == "$":
        return "$" in normalized_text
    normalized_term = _normalize_for_matching(term)
    if " " in normalized_term or "&" in normalized_term:
        return normalized_term in normalized_text
    return bool(re.search(rf"\b{re.escape(normalized_term)}\b", normalized_text))


def _table_density_signal(tables_json: dict[str, Any]) -> dict[str, Any]:
    tables = tables_json.get("tables", []) if isinstance(tables_json, dict) else []
    table_count = len(tables)
    cell_count = 0
    row_count = 0
    for table in tables:
        rows = table.get("rows", []) if isinstance(table, dict) else []
        row_count += len(rows)
        cell_count += sum(len(row) for row in rows)

    score = 0.0
    if table_count:
        score = min(4.0, table_count * 2.0) + min(2.0, cell_count / 20)

    return {
        "table_count": table_count,
        "row_count": row_count,
        "cell_count": cell_count,
        "score": round(score, 3),
    }


def _numeric_density_signal(text: str) -> dict[str, Any]:
    matches = re.findall(r"\(?[$]?\d[\d,]*(?:\.\d+)?%?\)?", text)
    score = min(3.0, len(matches) / 8)
    return {
        "numeric_count": len(matches),
        "score": round(score, 3),
    }


def _business_line_signal(normalized_text: str) -> dict[str, Any]:
    occurrences = {
        term: len(re.findall(rf"\b{re.escape(term)}\b", normalized_text))
        for term in sorted(BUSINESS_LINE_TERMS)
    }
    matched_occurrences = {
        term: count for term, count in occurrences.items() if count > 0
    }
    repeated_terms = [
        term for term, count in matched_occurrences.items() if count > 1
    ]
    score = min(
        3.0,
        len(matched_occurrences) * 0.5 + len(repeated_terms) * 0.5,
    )
    return {
        "terms": sorted(matched_occurrences),
        "occurrences": matched_occurrences,
        "repeated_terms": repeated_terms,
        "score": round(score, 3),
    }


def _normalize_for_matching(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def _build_reason(matched_signals: dict[str, Any]) -> str:
    if not matched_signals:
        return "No deterministic revenue-segment signals matched."

    reason_parts: list[str] = []
    if "terms" in matched_signals:
        top_terms = [str(match["term"]) for match in matched_signals["terms"][:3]]
        reason_parts.append(f"matched terms: {', '.join(top_terms)}")
    if "table_density" in matched_signals:
        table_count = matched_signals["table_density"]["table_count"]
        reason_parts.append(f"{table_count} detected table(s)")
    if "numeric_density" in matched_signals:
        numeric_count = matched_signals["numeric_density"]["numeric_count"]
        reason_parts.append(f"{numeric_count} numeric token(s)")
    if "currency_unit_terms" in matched_signals:
        reason_parts.append("currency or unit terms present")
    if "business_line_terms" in matched_signals:
        reason_parts.append("business-line labels present")
    if "total_terms" in matched_signals:
        reason_parts.append("total/consolidation terms present")

    return "; ".join(reason_parts) + "."
