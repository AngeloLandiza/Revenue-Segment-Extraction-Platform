from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from time import perf_counter

from revenue_segment_extractor.extraction.providers import (
    ANTHROPIC_JSON_SYSTEM_PROMPT,
    LLMExtractionRequest,
    LLMExtractionResponse,
    LLMProvider,
    LLMProviderError,
)


APPROX_CHARS_PER_TOKEN = 4
USD_PER_MILLION_TOKENS_BY_FAMILY = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
}


@dataclass(frozen=True)
class LLMCallUsage:
    provider_name: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    token_source: str
    status: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class WorkflowUsageTracker:
    calls: list[LLMCallUsage] = field(default_factory=list)
    _started_at: float = field(default_factory=perf_counter)
    _ended_at: float | None = None

    def stop(self) -> None:
        if self._ended_at is None:
            self._ended_at = perf_counter()

    @property
    def elapsed_seconds(self) -> float:
        end = self._ended_at if self._ended_at is not None else perf_counter()
        return max(0.0, end - self._started_at)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def input_tokens(self) -> int:
        return sum(call.input_tokens for call in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(call.output_tokens for call in self.calls)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        return sum(call.estimated_cost_usd for call in self.calls)

    def record(
        self,
        *,
        request: LLMExtractionRequest,
        response: LLMExtractionResponse | None,
        provider_name: str,
        status: str,
    ) -> None:
        input_tokens, output_tokens, token_source = _tokens_for_request(request, response)
        self.calls.append(
            LLMCallUsage(
                provider_name=provider_name,
                model=response.model if response is not None else request.model,
                prompt_version=request.prompt_version,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_llm_cost_usd(
                    model=response.model if response is not None else request.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                ),
                token_source=token_source,
                status=status,
            )
        )


class TrackedLLMProvider:
    def __init__(self, provider: LLMProvider, tracker: WorkflowUsageTracker) -> None:
        self._provider = provider
        self._tracker = tracker
        self.name = provider.name

    def complete_json(self, request: LLMExtractionRequest) -> LLMExtractionResponse:
        try:
            response = self._provider.complete_json(request)
        except LLMProviderError:
            self._tracker.record(
                request=request,
                response=None,
                provider_name=self.name,
                status="failed",
            )
            raise
        self._tracker.record(
            request=request,
            response=response,
            provider_name=response.provider_name,
            status="succeeded",
        )
        return response


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, ceil(len(text) / APPROX_CHARS_PER_TOKEN))


def estimated_llm_cost_usd(*, model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _prices_for_model(model)
    if prices is None:
        return 0.0
    input_price, output_price = prices
    return ((input_tokens * input_price) + (output_tokens * output_price)) / 1_000_000


def _tokens_for_request(
    request: LLMExtractionRequest,
    response: LLMExtractionResponse | None,
) -> tuple[int, int, str]:
    if (
        response is not None
        and response.input_tokens is not None
        and response.output_tokens is not None
    ):
        return response.input_tokens, response.output_tokens, "provider_usage"
    input_tokens = estimate_tokens(f"{ANTHROPIC_JSON_SYSTEM_PROMPT}\n{request.prompt}")
    output_tokens = estimate_tokens(response.content) if response is not None else 0
    return input_tokens, output_tokens, "estimated_chars"


def _prices_for_model(model: str) -> tuple[float, float] | None:
    normalized = model.casefold()
    if "opus" in normalized:
        return USD_PER_MILLION_TOKENS_BY_FAMILY["opus"]
    if "sonnet" in normalized:
        return USD_PER_MILLION_TOKENS_BY_FAMILY["sonnet"]
    return None
