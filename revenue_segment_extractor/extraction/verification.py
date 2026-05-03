from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from revenue_segment_extractor.extraction.json_response import JsonExtractionError, extract_json_object
from revenue_segment_extractor.extraction.normalization import NormalizedRevenueRow
from revenue_segment_extractor.extraction.prompts import (
    SECOND_PASS_VERIFICATION_PROMPT_VERSION,
    build_second_pass_verification_prompt,
)
from revenue_segment_extractor.extraction.providers import (
    LLMExtractionRequest,
    LLMProvider,
    LLMProviderError,
)
from revenue_segment_extractor.extraction.schemas import RevenueVerificationOutput
from revenue_segment_extractor.extraction.validation import DeterministicValidationIssue
from revenue_segment_extractor.models import Document, ParsedPage


@dataclass(frozen=True)
class VerificationRun:
    output: RevenueVerificationOutput | None
    issues: tuple[DeterministicValidationIssue, ...]
    provider_name: str | None
    model: str

    @property
    def failed_or_uncertain(self) -> bool:
        if self.output is None:
            return bool(self.issues)
        return bool(
            self.output.suspected_errors
            or self.output.missing_rows
            or self.output.correction_suggestions
        )


def run_second_pass_verification(
    *,
    provider: LLMProvider,
    document: Document,
    pages: list[ParsedPage],
    rows: list[NormalizedRevenueRow],
    validation_issues: list[DeterministicValidationIssue],
    model: str,
    max_tokens: int,
    temperature: float,
) -> VerificationRun:
    request = LLMExtractionRequest(
        prompt=build_second_pass_verification_prompt(
            document=document,
            pages=pages,
            rows=[row_payload(row) for row in rows],
            validation_issues=[issue_payload(issue) for issue in validation_issues],
        ),
        model=model,
        prompt_version=SECOND_PASS_VERIFICATION_PROMPT_VERSION,
        max_tokens=max_tokens,
        temperature=temperature,
        metadata={"document_id": document.id},
    )
    try:
        response = provider.complete_json(request)
    except LLMProviderError as exc:
        return VerificationRun(
            output=None,
            issues=(
                DeterministicValidationIssue(
                    row_index=None,
                    severity="error",
                    issue_type="llm_verification_provider_error",
                    message=str(exc),
                ),
            ),
            provider_name=getattr(provider, "name", None),
            model=model,
        )

    try:
        json_content = extract_json_object(response.content)
        output = RevenueVerificationOutput.model_validate_json(json_content)
    except (JsonExtractionError, ValidationError) as exc:
        return VerificationRun(
            output=None,
            issues=(
                DeterministicValidationIssue(
                    row_index=None,
                    severity="error",
                    issue_type="llm_verification_validation",
                    message=_verification_error_message(exc),
                ),
            ),
            provider_name=response.provider_name,
            model=response.model,
        )

    return VerificationRun(
        output=output,
        issues=(),
        provider_name=response.provider_name,
        model=response.model,
    )


def verification_output_issues(
    output: RevenueVerificationOutput,
    rows: list[NormalizedRevenueRow],
) -> tuple[DeterministicValidationIssue, ...]:
    row_indexes = {_normalize_name(row.segment_name): index for index, row in enumerate(rows)}
    issues: list[DeterministicValidationIssue] = [
        DeterministicValidationIssue(
            row_index=None,
            severity="info",
            issue_type="llm_verification_result",
            message=f"Second-pass verification rationale: {output.rationale}",
        )
    ]
    for suspected in output.suspected_errors:
        issues.append(
            DeterministicValidationIssue(
                row_index=row_indexes.get(_normalize_name(suspected.segment_name)),
                severity="warning",
                issue_type="llm_verification_suspected_error",
                message=(
                    f"{suspected.segment_name}: {suspected.issue_type}; "
                    f"suggested action: {suspected.suggested_action}. {suspected.rationale}"
                ),
            )
        )
    for missing in output.missing_rows:
        issues.append(
            DeterministicValidationIssue(
                row_index=None,
                severity="warning",
                issue_type="llm_verification_missing_row",
                message=f"Missing row suspected for {missing.segment_name}: {missing.rationale}",
            )
        )
    for correction in output.correction_suggestions:
        issues.append(
            DeterministicValidationIssue(
                row_index=row_indexes.get(_normalize_name(correction.segment_name)),
                severity="warning",
                issue_type="llm_verification_correction_suggestion",
                message=(
                    f"{correction.segment_name}.{correction.field_name}: "
                    f"{correction.current_value!r} -> {correction.suggested_value!r}. "
                    f"{correction.rationale}"
                ),
            )
        )
    return tuple(issues)


def should_run_second_pass_verification(
    issues: list[DeterministicValidationIssue],
) -> bool:
    return any(issue.severity in {"warning", "error"} for issue in issues)


def row_payload(row: NormalizedRevenueRow) -> dict:
    return {
        "segment_name": row.segment_name,
        "revenue_raw": row.revenue_raw,
        "revenue_value": row.revenue_value,
        "currency": row.currency,
        "scale": row.scale,
        "period_label": row.period_label,
        "normalized_value": row.normalized_value,
        "page_ref": row.page_ref,
        "section_ref": row.section_ref,
        "metric_basis": row.metric_basis,
        "evidence_text": row.evidence_text,
        "confidence": row.extraction_confidence,
    }


def issue_payload(issue: DeterministicValidationIssue) -> dict:
    return {
        "row_index": issue.row_index,
        "severity": issue.severity,
        "issue_type": issue.issue_type,
        "message": issue.message,
        "blocking": issue.blocking,
    }


def _verification_error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            first_error = errors[0]
            location = ".".join(str(part) for part in first_error.get("loc", ())) or "response"
            return f"{location}: {first_error.get('msg', 'invalid response')}"
    return str(exc)


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())
