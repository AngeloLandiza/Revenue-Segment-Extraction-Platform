from __future__ import annotations

import re
import unicodedata
from typing import Any, Protocol


class PageWithBlocks(Protocol):
    page_number: int
    blocks_json: dict[str, Any]


def locate_evidence_snippet(
    parsed_page: PageWithBlocks,
    snippet: str,
    *,
    max_matches: int = 3,
) -> list[dict[str, Any]]:
    if not snippet.strip():
        raise ValueError("snippet must not be empty")
    if max_matches < 1:
        raise ValueError("max_matches must be at least 1")

    normalized_snippet = _normalize(snippet)
    snippet_tokens = set(normalized_snippet.split())
    matches: list[dict[str, Any]] = []

    for block in _iter_text_blocks(parsed_page):
        block_text = str(block.get("text", ""))
        normalized_block = _normalize(block_text)
        if not normalized_block:
            continue

        exact_match = normalized_snippet in normalized_block
        token_overlap = len(snippet_tokens & set(normalized_block.split()))
        if not exact_match and token_overlap < max(1, min(3, len(snippet_tokens))):
            continue

        matches.append(
            {
                "page_number": int(getattr(parsed_page, "page_number")),
                "snippet_text": _trim_snippet(block_text, normalized_snippet),
                "bbox": block.get("bbox"),
                "parser_source": str(block.get("parser_source") or "pymupdf"),
                "match_type": "exact_block" if exact_match else "token_overlap_block",
                "block_index": block.get("block_index"),
            }
        )
        if len(matches) >= max_matches:
            break

    return matches


def _iter_text_blocks(parsed_page: PageWithBlocks) -> list[dict[str, Any]]:
    blocks_json = parsed_page.blocks_json
    if not isinstance(blocks_json, dict):
        return []
    blocks = blocks_json.get("blocks", [])
    if not isinstance(blocks, list):
        return []
    return [block for block in blocks if isinstance(block, dict)]


def _trim_snippet(block_text: str, normalized_snippet: str) -> str:
    normalized_block = _normalize(block_text)
    match_index = normalized_block.find(normalized_snippet)
    if match_index < 0:
        return block_text[:300].strip()

    start = max(0, match_index - 80)
    end = min(len(block_text), match_index + len(normalized_snippet) + 160)
    return block_text[start:end].strip()


def _normalize(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()
