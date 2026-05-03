from __future__ import annotations

import unittest

from revenue_segment_extractor.extraction.providers import (
    LLMExtractionRequest,
    LLMExtractionResponse,
    LLMProviderError,
)
from revenue_segment_extractor.extraction.usage import (
    TrackedLLMProvider,
    WorkflowUsageTracker,
    estimate_tokens,
    estimated_llm_cost_usd,
)


class LLMUsageTrackingTest(unittest.TestCase):
    def test_estimates_cost_for_sonnet_and_opus_families(self) -> None:
        sonnet_cost = estimated_llm_cost_usd(
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        opus_cost = estimated_llm_cost_usd(
            model="claude-opus-4-7",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        self.assertEqual(18.0, sonnet_cost)
        self.assertEqual(90.0, opus_cost)

    def test_tracked_provider_records_provider_usage_tokens(self) -> None:
        tracker = WorkflowUsageTracker()
        provider = TrackedLLMProvider(_UsageProvider(), tracker)

        response = provider.complete_json(_request())

        self.assertEqual("{}", response.content)
        self.assertEqual(1, tracker.call_count)
        self.assertEqual(10, tracker.input_tokens)
        self.assertEqual(4, tracker.output_tokens)
        self.assertEqual("provider_usage", tracker.calls[0].token_source)
        self.assertEqual("succeeded", tracker.calls[0].status)

    def test_tracked_provider_estimates_tokens_when_usage_is_unavailable(self) -> None:
        tracker = WorkflowUsageTracker()
        provider = TrackedLLMProvider(_NoUsageProvider(), tracker)

        provider.complete_json(_request(prompt="abcd" * 8))

        self.assertEqual(1, tracker.call_count)
        self.assertGreaterEqual(tracker.input_tokens, estimate_tokens("abcd" * 8))
        self.assertGreater(tracker.output_tokens, 0)
        self.assertEqual("estimated_chars", tracker.calls[0].token_source)

    def test_tracked_provider_records_failed_requests(self) -> None:
        tracker = WorkflowUsageTracker()
        provider = TrackedLLMProvider(_FailingProvider(), tracker)

        with self.assertRaises(LLMProviderError):
            provider.complete_json(_request())

        self.assertEqual(1, tracker.call_count)
        self.assertEqual("failed", tracker.calls[0].status)
        self.assertEqual(0, tracker.output_tokens)


class _UsageProvider:
    name = "usage-test"

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        return LLMExtractionResponse(
            content="{}",
            model=request.model,
            provider_name=self.name,
            input_tokens=10,
            output_tokens=4,
        )


class _NoUsageProvider:
    name = "no-usage-test"

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        return LLMExtractionResponse(
            content='{"ok": true}',
            model=request.model,
            provider_name=self.name,
        )


class _FailingProvider:
    name = "failing-test"

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        raise LLMProviderError("provider failed")


def _request(prompt: str = "prompt") -> LLMExtractionRequest:
    return LLMExtractionRequest(
        prompt=prompt,
        model="claude-sonnet-4-6",
        prompt_version="usage_test_v1",
        max_tokens=100,
    )


if __name__ == "__main__":
    unittest.main()
