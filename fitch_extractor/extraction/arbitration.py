from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from fitch_extractor.extraction.json_response import JsonExtractionError, extract_json_object
from fitch_extractor.extraction.normalization import NormalizedRevenueRow
from fitch_extractor.extraction.prompts import ARBITRATION_PROMPT_VERSION, build_arbitration_prompt
from fitch_extractor.extraction.providers import (
    LLMExtractionRequest,
    LLMProvider,
    LLMProviderError,
)
from fitch_extractor.extraction.schemas import RevenueArbitrationOutput, RevenueVerificationOutput
from fitch_extractor.extraction.validation import DeterministicValidationIssue
from fitch_extractor.extraction.verification import issue_payload, row_payload
from fitch_extractor.models import Document, ParsedPage


@dataclass(frozen=True)
class ArbitrationRun:
    output: RevenueArbitrationOutput | None
    issues: tuple[DeterministicValidationIssue, ...]
    provider_name: str | None
    model: str


def should_run_arbitration(
    *,
    validation_issues: list[DeterministicValidationIssue],
    verification_output: RevenueVerificationOutput | None,
    verification_failed: bool,
) -> bool:
    if any(issue.severity in {"warning", "error"} for issue in validation_issues):
        return True
    if verification_failed:
        return True
    if verification_output is None:
        return False
    return bool(
        verification_output.suspected_errors
        or verification_output.missing_rows
        or verification_output.correction_suggestions
    )


def run_arbitration(
    *,
    provider: LLMProvider,
    document: Document,
    pages: list[ParsedPage],
    rows: list[NormalizedRevenueRow],
    validation_issues: list[DeterministicValidationIssue],
    verification_output: RevenueVerificationOutput | None,
    model: str,
    max_tokens: int,
    temperature: float,
) -> ArbitrationRun:
    request = LLMExtractionRequest(
        prompt=build_arbitration_prompt(
            document=document,
            pages=pages,
            rows=[row_payload(row) for row in rows],
            validation_issues=[issue_payload(issue) for issue in validation_issues],
            verification_result=verification_output.model_dump(mode="json")
            if verification_output is not None
            else None,
        ),
        model=model,
        prompt_version=ARBITRATION_PROMPT_VERSION,
        max_tokens=max_tokens,
        temperature=temperature,
        metadata={"document_id": document.id},
    )
    try:
        response = provider.complete_json(request)
    except LLMProviderError as exc:
        return ArbitrationRun(
            output=None,
            issues=(
                DeterministicValidationIssue(
                    row_index=None,
                    severity="error",
                    issue_type="llm_arbitration_provider_error",
                    message=str(exc),
                ),
            ),
            provider_name=getattr(provider, "name", None),
            model=model,
        )

    try:
        json_content = extract_json_object(response.content)
        output = RevenueArbitrationOutput.model_validate_json(json_content)
    except (JsonExtractionError, ValidationError) as exc:
        return ArbitrationRun(
            output=None,
            issues=(
                DeterministicValidationIssue(
                    row_index=None,
                    severity="error",
                    issue_type="llm_arbitration_validation",
                    message=_arbitration_error_message(exc),
                ),
            ),
            provider_name=response.provider_name,
            model=response.model,
        )

    return ArbitrationRun(
        output=output,
        issues=(),
        provider_name=response.provider_name,
        model=response.model,
    )


def arbitration_output_issues(
    output: RevenueArbitrationOutput,
    rows: list[NormalizedRevenueRow],
    *,
    provider_name: str | None,
    model: str,
) -> tuple[DeterministicValidationIssue, ...]:
    row_indexes = {_normalize_name(row.segment_name): index for index, row in enumerate(rows)}
    model_label = _arbitration_model_label(provider_name=provider_name, model=model)
    issues: list[DeterministicValidationIssue] = [
        DeterministicValidationIssue(
            row_index=None,
            severity="warning" if output.requires_human_review else "info",
            issue_type="llm_opus_arbitration_result",
            message=(
                f"LLM arbitration ({model_label}) rationale: {output.rationale} "
                f"Accepted={len(output.accepted_rows)}, rejected={len(output.rejected_rows)}, "
                f"missing={len(output.missing_rows)}, corrections={len(output.correction_suggestions)}, "
                f"requires_human_review={output.requires_human_review}."
            ),
        )
    ]
    for rejected in output.rejected_rows:
        issues.append(
            DeterministicValidationIssue(
                row_index=row_indexes.get(_normalize_name(rejected.segment_name)),
                severity="warning",
                issue_type="llm_opus_arbitration_rejected_row",
                message=(
                    f"{rejected.segment_name}: {rejected.issue_type}; "
                    f"suggested action: {rejected.suggested_action}. {rejected.rationale}"
                ),
            )
        )
    for missing in output.missing_rows:
        issues.append(
            DeterministicValidationIssue(
                row_index=None,
                severity="warning",
                issue_type="llm_opus_arbitration_missing_row",
                message=f"Arbitration found missing row {missing.segment_name}: {missing.rationale}",
            )
        )
    for correction in output.correction_suggestions:
        issues.append(
            DeterministicValidationIssue(
                row_index=row_indexes.get(_normalize_name(correction.segment_name)),
                severity="warning",
                issue_type="llm_opus_arbitration_correction_suggestion",
                message=(
                    f"{correction.segment_name}.{correction.field_name}: "
                    f"{correction.current_value!r} -> {correction.suggested_value!r}. "
                    f"{correction.rationale}"
                ),
            )
        )
    return tuple(issues)


def _arbitration_error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            first_error = errors[0]
            location = ".".join(str(part) for part in first_error.get("loc", ())) or "response"
            return f"{location}: {first_error.get('msg', 'invalid response')}"
    return str(exc)


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _arbitration_model_label(*, provider_name: str | None, model: str) -> str:
    if provider_name:
        return f"{provider_name}:{model}"
    return model
