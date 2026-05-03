#!/usr/bin/env python3
"""Ping Anthropic Claude using the same provider stack as revenue extraction.

Requires ANTHROPIC_API_KEY. Optional env: FITCH_EXTRACTION_MODEL (see fitch_extractor.extraction.config).

Exit codes: 0 success, 1 API/runtime failure, 2 configuration error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fitch_extractor.extraction.config import ExtractionSettings
from fitch_extractor.extraction.providers import LLMExtractionRequest, LLMProviderError, create_provider


def main() -> int:
    settings = ExtractionSettings.from_env()
    try:
        provider = create_provider("anthropic")
    except LLMProviderError as exc:
        print(exc, file=sys.stderr)
        return 2

    req = LLMExtractionRequest(
        prompt=(
            'Reply with exactly one JSON object and no other text: '
            '{"ok": true, "echo": "claude-api-test"}'
        ),
        model=settings.model,
        prompt_version="claude_api_connectivity_v1",
        max_tokens=min(256, settings.max_tokens),
        temperature=0.0,
    )

    try:
        resp = provider.complete_json(req)
    except LLMProviderError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"model={resp.model!r} provider={resp.provider_name!r}")
    print("--- raw response ---")
    print(resp.content)
    print("---")

    try:
        parsed = json.loads(resp.content)
    except json.JSONDecodeError as exc:
        print(f"warning: response was not valid JSON: {exc}", file=sys.stderr)
        return 1

    if parsed.get("ok") is not True:
        print("warning: expected {\"ok\": true} in parsed JSON", file=sys.stderr)
        return 1

    print("claude_api_test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
