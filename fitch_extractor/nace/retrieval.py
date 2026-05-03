from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from fitch_extractor.nace.reference import NaceNode


_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "business",
    "businesses",
    "by",
    "company",
    "division",
    "for",
    "from",
    "group",
    "in",
    "of",
    "on",
    "operations",
    "other",
    "products",
    "revenue",
    "segment",
    "segments",
    "service",
    "services",
    "solutions",
    "the",
    "to",
    "with",
}

_LEVEL_BONUS = {
    "section": 0.00,
    "division": 0.03,
    "group": 0.05,
    "class": 0.07,
}


@dataclass(frozen=True)
class NaceMatch:
    node: NaceNode
    score: float
    rationale: str


def retrieve_nace_candidates(
    nodes: tuple[NaceNode, ...],
    *,
    segment_name: str,
    evidence_text: str = "",
    context: str = "",
    limit: int = 8,
) -> tuple[NaceMatch, ...]:
    query_text = " ".join(part for part in (segment_name, evidence_text, context) if part)
    query_tokens = set(_tokenize(query_text))
    segment_tokens = set(_tokenize(segment_name))
    if not query_tokens and not segment_name.strip():
        return ()

    matches = [
        _score_node(node, segment_name=segment_name, query_tokens=query_tokens, segment_tokens=segment_tokens)
        for node in nodes
    ]
    positive_matches = [match for match in matches if match.score > 0]
    positive_matches.sort(
        key=lambda match: (
            match.score,
            match.node.level_depth,
            -match.node.source_row_number,
        ),
        reverse=True,
    )
    return tuple(_ranked(match) for match in positive_matches[:limit])


def _score_node(
    node: NaceNode,
    *,
    segment_name: str,
    query_tokens: set[str],
    segment_tokens: set[str],
) -> NaceMatch:
    node_text = " ".join((*node.hierarchy_path_names, node.label))
    node_tokens = set(_tokenize(node_text))
    label_tokens = set(_tokenize(node.label))
    if not node_tokens:
        return NaceMatch(node=node, score=0.0, rationale="No comparable NACE tokens.")

    overlap = query_tokens & node_tokens
    label_overlap = segment_tokens & label_tokens
    fuzzy = SequenceMatcher(None, _normalize(segment_name), _normalize(node.label)).ratio()
    exact_label = _normalize(segment_name) == _normalize(node.label)
    contains_label = bool(_normalize(segment_name)) and _normalize(segment_name) in _normalize(node.label)

    overlap_score = len(overlap) / max(len(query_tokens), 1)
    label_score = len(label_overlap) / max(len(label_tokens), 1)
    score = (0.52 * overlap_score) + (0.28 * label_score) + (0.20 * fuzzy)
    if exact_label:
        score += 0.30
    elif contains_label:
        score += 0.12
    score += _LEVEL_BONUS[node.level]
    score = min(score, 1.0)

    if not overlap and fuzzy < 0.45 and not exact_label and not contains_label:
        score = 0.0

    rationale_parts: list[str] = []
    if overlap:
        rationale_parts.append("keyword overlap: " + ", ".join(sorted(overlap)[:8]))
    if fuzzy >= 0.55:
        rationale_parts.append(f"fuzzy label similarity {fuzzy:.2f}")
    if exact_label or contains_label:
        rationale_parts.append("segment label directly matches NACE label")
    if not rationale_parts:
        rationale_parts.append("weak deterministic text similarity")

    return NaceMatch(node=node, score=round(score, 4), rationale="; ".join(rationale_parts))


def _ranked(match: NaceMatch) -> NaceMatch:
    if match.score >= 0.55 or match.node.level in {"section", "division"}:
        return match
    fallback = _fallback_notice(match.node.level)
    if not fallback:
        return match
    return replace(match, rationale=f"{match.rationale}; ambiguous below {fallback} level")


def _fallback_notice(level: str) -> str | None:
    if level == "class":
        return "class"
    if level == "group":
        return "group"
    return None


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
        if len(token) > 2 and token not in _STOPWORDS
    ]


def _normalize(text: str) -> str:
    return " ".join(_tokenize(text))
