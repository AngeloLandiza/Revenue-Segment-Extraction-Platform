from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from fitch_extractor.extraction.arbitration import (
    arbitration_output_issues,
    run_arbitration,
    should_run_arbitration,
)
from fitch_extractor.extraction.candidate_selection import (
    is_non_segment_revenue_row,
    is_non_segment_page,
    is_table_of_contents_page,
    select_extraction_candidates,
)
from fitch_extractor.extraction.confidence import ConfidenceInputs, compute_row_confidence
from fitch_extractor.extraction.config import ExtractionSettings
from fitch_extractor.extraction.deduplication import (
    PreparedRevenueRow,
    deduplicate_rows,
    is_duplicate_of_existing,
)
from fitch_extractor.extraction.json_response import JsonExtractionError, extract_json_object
from fitch_extractor.extraction.normalization import (
    NormalizedRevenueRow,
    normalize_extracted_row,
)
from fitch_extractor.extraction.periods import keep_latest_year_rows
from fitch_extractor.extraction.prompts import (
    CANDIDATE_DISCOVERY_PROMPT_VERSION,
    FIRST_PASS_PROMPT_VERSION,
    build_candidate_discovery_prompt,
    build_first_pass_extraction_prompt,
)
from fitch_extractor.extraction.providers import (
    LLMExtractionRequest,
    LLMProvider,
    LLMProviderError,
)
from fitch_extractor.extraction.row_selection import keep_primary_table_rows
from fitch_extractor.extraction.schemas import (
    CandidateDiscoveryOutput,
    ExtractedRevenueRow,
    RevenueExtractionOutput,
)
from fitch_extractor.extraction.table_alignment import align_rows_to_preferred_metric
from fitch_extractor.extraction.validation import (
    DeterministicValidationIssue,
    validate_normalized_rows,
)
from fitch_extractor.extraction.verification import (
    run_second_pass_verification,
    should_run_second_pass_verification,
    verification_output_issues,
)
from fitch_extractor.enrichment import classify_segment_row
from fitch_extractor.ingestion.evidence import locate_evidence_snippet
from fitch_extractor.models import (
    DOCUMENT_STATUS_FAILED,
    DOCUMENT_STATUS_NEEDS_REVIEW,
    DOCUMENT_STATUS_READY_FOR_REVIEW,
    SEGMENT_STATUS_NEEDS_REVIEW,
    SEGMENT_STATUS_READY_FOR_REVIEW,
    Document,
    PageCandidate,
    ParsedPage,
    SegmentEvidence,
    SegmentRow,
    SerializableModel,
    ValidationIssue,
)
from fitch_extractor.persistence.repository import SQLiteRepository


DISCOVERY_PAGE_BATCH_SIZE = 40
DISCOVERY_MAX_SELECTED_PAGES = 6


@dataclass(frozen=True)
class ExtractionSummary(SerializableModel):
    document: Document
    prompt_version: str
    provider_name: str
    model: str
    candidate_page_count: int
    bundle_count: int
    extracted_row_count: int
    persisted_row_count: int
    validation_issue_count: int
    segment_rows: tuple[SegmentRow, ...]
    validation_issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True)
class _AcceptedExtraction:
    row: ExtractedRevenueRow
    output: RevenueExtractionOutput


@dataclass(frozen=True)
class _ExtractionRun:
    accepted_extractions: list[_AcceptedExtraction]
    extracted_count: int
    issues: list[ValidationIssue]


class RevenueExtractionService:
    def __init__(
        self,
        repository: SQLiteRepository,
        provider: LLMProvider,
        settings: ExtractionSettings | None = None,
        *,
        verification_provider: LLMProvider | None = None,
        arbitration_provider: LLMProvider | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.settings = settings or ExtractionSettings()
        self.verification_provider = verification_provider
        self.arbitration_provider = arbitration_provider

    def extract_document(
        self,
        document_id: str,
        *,
        candidate_limit: int | None = None,
        page_bundle_size: int | None = None,
    ) -> ExtractionSummary:
        document = self.repository.get_document(document_id)
        if document is None:
            raise KeyError(f"Document not found: {document_id}")

        parsed_pages = self.repository.list_parsed_pages(document_id)
        candidates = self.repository.list_page_candidates(document_id)
        if candidate_limit is not None:
            candidates = candidates[:candidate_limit]
        candidates = select_extraction_candidates(candidates, parsed_pages)

        if not parsed_pages:
            issue = self.repository.create_validation_issue(
                document_id=document.id,
                severity="error",
                issue_type="missing_candidate_pages",
                message="No parsed candidate pages are available; run PDF ingestion first.",
            )
            document = self.repository.update_document(document.id, status=DOCUMENT_STATUS_FAILED)
            return self._summary(
                document=document,
                candidates=candidates,
                bundle_count=0,
                extracted_count=0,
                rows=[],
                issues=[issue],
            )

        issues: list[ValidationIssue] = []
        accepted_extractions: list[_AcceptedExtraction] = []
        extracted_count = 0
        max_bundle_pages = page_bundle_size or self.settings.page_bundle_size

        if candidates:
            bundles = _build_page_bundles(
                candidates,
                parsed_pages,
                max_pages=max_bundle_pages,
            )
            initial_run = self._run_extraction_bundles(document, bundles)
            issues.extend(initial_run.issues)
            accepted_extractions.extend(initial_run.accepted_extractions)
            extracted_count += initial_run.extracted_count

        should_discover = (
            not accepted_extractions
            and not any(issue.severity == "error" for issue in issues)
            and (
                not candidates
                or any(issue.issue_type == "llm_extraction_warning" for issue in issues)
            )
        )
        if should_discover:
            discovered_candidates, discovery_issues = self._discover_candidate_pages(
                document=document,
                parsed_pages=parsed_pages,
                excluded_page_numbers={candidate.page_number for candidate in candidates},
            )
            issues.extend(discovery_issues)
            if discovered_candidates:
                candidates = _merge_candidates(candidates, discovered_candidates)
                discovery_bundles = _build_page_bundles(
                    discovered_candidates,
                    parsed_pages,
                    max_pages=max_bundle_pages,
                )
                discovery_run = self._run_extraction_bundles(document, discovery_bundles)
                issues.extend(discovery_run.issues)
                accepted_extractions.extend(discovery_run.accepted_extractions)
                extracted_count += discovery_run.extracted_count

        if not candidates:
            issue = self.repository.create_validation_issue(
                document_id=document.id,
                severity="warning",
                issue_type="no_extraction_eligible_candidate_pages",
                message="No candidate pages had explicit revenue segment extraction signals.",
            )
            issues.append(issue)

        persisted_rows: list[SegmentRow] = []

        latest_result = keep_latest_year_rows([item.row for item in accepted_extractions])
        if latest_result.skipped_count and latest_result.latest_year is not None:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="warning",
                    issue_type="prior_period_row_skipped",
                    message=(
                        f"Skipped {latest_result.skipped_count} prior-period row(s); "
                        f"kept latest detected reporting year {latest_result.latest_year}."
                    ),
                )
            )

        latest_row_ids = {id(row) for row in latest_result.rows}
        normalized_rows = _normalize_accepted_extractions(
            accepted_extractions=accepted_extractions,
            latest_row_ids=latest_row_ids,
            document=document,
            candidates=candidates,
            parsed_pages=parsed_pages,
        )
        prepared_rows, normalized_by_source_id = _prepare_normalized_rows(normalized_rows)
        primary_selection = keep_primary_table_rows(prepared_rows)
        if primary_selection.skipped_count:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="warning",
                    issue_type="secondary_segment_table_skipped",
                    message=(
                        f"Skipped {primary_selection.skipped_count} row(s) from secondary "
                        "segment tables after selecting the strongest primary table."
                    ),
                )
            )
        prepared_rows = primary_selection.rows
        deduped_rows, duplicate_count = deduplicate_rows(prepared_rows)
        deduped_normalized_rows = [
            normalized_by_source_id[id(row.source_row)] for row in deduped_rows
        ]
        if duplicate_count:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="warning",
                    issue_type="duplicate_extraction_row",
                    message=f"Skipped {duplicate_count} duplicate extraction row(s).",
                )
            )

        validation_result = validate_normalized_rows(
            document=document,
            rows=deduped_normalized_rows,
            parsed_pages=parsed_pages,
        )
        processing_issues = list(validation_result.issues)
        deterministic_issues = list(processing_issues)
        verification_output = None
        verification_failed = False

        if (
            self.settings.enable_second_pass_verification
            and self.verification_provider is not None
            and should_run_second_pass_verification(processing_issues)
        ):
            verification_run = run_second_pass_verification(
                provider=self.verification_provider,
                document=document,
                pages=_pages_for_rows(parsed_pages, deduped_normalized_rows),
                rows=deduped_normalized_rows,
                validation_issues=deterministic_issues,
                model=self.settings.verification_model,
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
            )
            verification_output = verification_run.output
            verification_failed = verification_run.failed_or_uncertain
            processing_issues.extend(verification_run.issues)
            if verification_run.output is not None:
                processing_issues.extend(
                    verification_output_issues(verification_run.output, deduped_normalized_rows)
                )

        if (
            self.settings.enable_arbitration
            and self.arbitration_provider is not None
            and should_run_arbitration(
                validation_issues=deterministic_issues,
                verification_output=verification_output,
                verification_failed=verification_failed,
            )
        ):
            arbitration_run = run_arbitration(
                provider=self.arbitration_provider,
                document=document,
                pages=_pages_for_rows(parsed_pages, deduped_normalized_rows),
                rows=deduped_normalized_rows,
                validation_issues=deterministic_issues,
                verification_output=verification_output,
                model=self.settings.arbitration_model,
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
            )
            processing_issues.extend(arbitration_run.issues)
            if arbitration_run.output is not None:
                processing_issues.extend(
                    arbitration_output_issues(
                        arbitration_run.output,
                        deduped_normalized_rows,
                        provider_name=arbitration_run.provider_name,
                        model=arbitration_run.model,
                    )
                )

        blocking_row_indexes = {
            issue.row_index
            for issue in processing_issues
            if issue.row_index is not None and issue.blocking
        }
        issues.extend(
            self._persist_document_processing_issues(
                document=document,
                processing_issues=processing_issues,
                blocking_row_indexes=blocking_row_indexes,
            )
        )
        has_document_review_issue = any(
            issue.row_index is None and issue.severity in {"warning", "error"}
            for issue in processing_issues
        )

        for row_index, row in enumerate(deduped_normalized_rows):
            prepared_row = deduped_rows[row_index]
            if row_index in blocking_row_indexes:
                continue
            if self._already_persisted(prepared_row, document.id):
                issues.append(
                    self.repository.create_validation_issue(
                        document_id=document.id,
                        severity="warning",
                        issue_type="duplicate_existing_segment_row",
                        message=(
                            "Skipped duplicate row already stored for "
                            f"{row.source_row.segment_name}."
                        ),
                    )
                )
                continue
            row_processing_issues = [
                issue for issue in processing_issues if issue.row_index == row_index
            ]
            status = _status_for_row(
                row_processing_issues,
                reconciliation_status=validation_result.reconciliation.status,
                has_document_review_issue=has_document_review_issue,
            )
            confidence = compute_row_confidence(
                ConfidenceInputs(
                    row=row,
                    row_issues=tuple(row_processing_issues),
                    reconciliation=validation_result.reconciliation,
                )
            )
            persisted_row, row_issues = self._persist_row(
                document,
                row,
                parsed_pages,
                status=status,
                confidence=confidence,
            )
            persisted_rows.append(persisted_row)
            issues.extend(row_issues)
            issues.extend(
                self._persist_row_processing_issues(
                    document=document,
                    segment_id=persisted_row.id,
                    processing_issues=row_processing_issues,
                )
            )

        document = self._update_document_status(document, persisted_rows, issues)

        return self._summary(
            document=document,
            candidates=candidates,
            bundle_count=_bundle_count_for_candidates(
                candidates,
                parsed_pages,
                max_pages=max_bundle_pages,
            ),
            extracted_count=extracted_count,
            rows=persisted_rows,
            issues=issues,
        )

    def _run_extraction_bundles(
        self,
        document: Document,
        bundles: list[list[ParsedPage]],
    ) -> _ExtractionRun:
        issues: list[ValidationIssue] = []
        accepted_extractions: list[_AcceptedExtraction] = []
        extracted_count = 0

        for bundle in bundles:
            prompt = build_first_pass_extraction_prompt(document=document, pages=bundle)
            request = LLMExtractionRequest(
                prompt=prompt,
                model=self.settings.model,
                prompt_version=FIRST_PASS_PROMPT_VERSION,
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
                metadata={"document_id": document.id},
            )
            output = self._complete_and_validate(document, request, issues)
            if output is None:
                continue

            extracted_count += len(output.rows)
            issues.extend(self._persist_warnings(document, output.extraction_warnings))
            accepted_rows, row_validation_issues = self._accepted_rows_for_bundle(
                document=document,
                output=output,
                bundle=bundle,
            )
            issues.extend(row_validation_issues)
            accepted_rows = _align_rows_to_page_tables(accepted_rows, bundle)
            accepted_extractions.extend(
                _AcceptedExtraction(row=row, output=output) for row in accepted_rows
            )

        return _ExtractionRun(
            accepted_extractions=accepted_extractions,
            extracted_count=extracted_count,
            issues=issues,
        )

    def _discover_candidate_pages(
        self,
        *,
        document: Document,
        parsed_pages: list[ParsedPage],
        excluded_page_numbers: set[int],
    ) -> tuple[list[PageCandidate], list[ValidationIssue]]:
        discovered: dict[int, PageCandidate] = {}
        issues: list[ValidationIssue] = []

        for batch in _page_batches(parsed_pages, DISCOVERY_PAGE_BATCH_SIZE):
            prompt = build_candidate_discovery_prompt(
                document=document,
                pages=batch,
                max_pages=DISCOVERY_MAX_SELECTED_PAGES,
            )
            request = LLMExtractionRequest(
                prompt=prompt,
                model=self.settings.model,
                prompt_version=CANDIDATE_DISCOVERY_PROMPT_VERSION,
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
                metadata={"document_id": document.id},
            )
            output = self._complete_and_validate_discovery(document, request, issues)
            if output is None:
                continue
            issues.extend(self._persist_warnings(document, output.extraction_warnings))
            for selected_page in output.selected_pages:
                if selected_page.page_number in excluded_page_numbers:
                    continue
                page = next(
                    (item for item in batch if item.page_number == selected_page.page_number),
                    None,
                )
                if page is None:
                    continue
                confidence = selected_page.confidence if selected_page.confidence is not None else 0.5
                discovered[selected_page.page_number] = PageCandidate(
                    id=f"discovered_{document.id}_{selected_page.page_number}",
                    document_id=document.id,
                    page_number=selected_page.page_number,
                    relevance_score=round(confidence * 100, 3),
                    matched_signals_json={
                        "llm_candidate_discovery": {
                            "prompt_version": CANDIDATE_DISCOVERY_PROMPT_VERSION,
                            "confidence": selected_page.confidence,
                            "reason": selected_page.reason,
                        }
                    },
                    reason=f"LLM candidate discovery: {selected_page.reason}",
                )

        selected = sorted(
            discovered.values(),
            key=lambda candidate: (-candidate.relevance_score, candidate.page_number),
        )[:DISCOVERY_MAX_SELECTED_PAGES]
        return selected, issues

    def _complete_and_validate(
        self,
        document: Document,
        request: LLMExtractionRequest,
        issues: list[ValidationIssue],
    ) -> RevenueExtractionOutput | None:
        try:
            response = self.provider.complete_json(request)
        except LLMProviderError as exc:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="error",
                    issue_type="llm_provider_error",
                    message=str(exc),
                )
            )
            return None

        try:
            json_content = extract_json_object(response.content)
            return RevenueExtractionOutput.model_validate_json(json_content)
        except JsonExtractionError as exc:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="error",
                    issue_type="llm_output_validation",
                    message=str(exc),
                )
            )
            return None
        except ValidationError as exc:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="error",
                    issue_type="llm_output_validation",
                    message=_format_validation_error(exc),
                )
            )
            return None

    def _complete_and_validate_discovery(
        self,
        document: Document,
        request: LLMExtractionRequest,
        issues: list[ValidationIssue],
    ) -> CandidateDiscoveryOutput | None:
        try:
            response = self.provider.complete_json(request)
        except LLMProviderError as exc:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="error",
                    issue_type="llm_provider_error",
                    message=str(exc),
                )
            )
            return None

        try:
            json_content = extract_json_object(response.content)
            return CandidateDiscoveryOutput.model_validate_json(json_content)
        except JsonExtractionError as exc:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="error",
                    issue_type="llm_candidate_discovery_validation",
                    message=str(exc),
                )
            )
            return None
        except ValidationError as exc:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    severity="error",
                    issue_type="llm_candidate_discovery_validation",
                    message=_format_validation_error(exc),
                )
            )
            return None

    def _persist_warnings(
        self,
        document: Document,
        warnings: list[str],
    ) -> list[ValidationIssue]:
        return [
            self.repository.create_validation_issue(
                document_id=document.id,
                severity="warning",
                issue_type="llm_extraction_warning",
                message=warning,
            )
            for warning in warnings
            if warning.strip()
        ]

    def _already_persisted(self, row: PreparedRevenueRow, document_id: str) -> bool:
        for segment in self.repository.list_segment_rows(document_id):
            evidence_text = _first_evidence_text(self.repository.list_segment_evidence(segment.id))
            if is_duplicate_of_existing(
                row,
                existing_segment_name=segment.segment_name,
                existing_normalized_value=segment.normalized_value,
                existing_period_label=segment.period_label,
                existing_page_number=_parse_page_number(segment.page_ref),
                existing_evidence_text=evidence_text,
            ):
                return True
        return False

    def _persist_row(
        self,
        document: Document,
        row: NormalizedRevenueRow,
        parsed_pages: list[ParsedPage],
        *,
        status: str,
        confidence: float,
    ) -> tuple[SegmentRow, list[ValidationIssue]]:
        classification = classify_segment_row(
            row.segment_name,
            evidence_text=row.evidence_text,
            normalized_value=row.normalized_value,
        )
        segment = self.repository.create_segment_row(
            document_id=document.id,
            segment_name=row.segment_name,
            revenue_raw=row.revenue_raw,
            revenue_value=row.revenue_value,
            currency=row.currency,
            scale=row.scale,
            period_label=row.period_label,
            normalized_value=row.normalized_value,
            page_ref=row.page_ref,
            section_ref=row.section_ref,
            metric_basis=row.metric_basis,
            confidence=confidence,
            status=status,
            extraction_method=f"{self.provider.name}:{FIRST_PASS_PROMPT_VERSION}",
            row_type=classification.row_type,
            segment_type=classification.segment_type,
            segment_name_original=classification.segment_name_original,
            segment_name_normalized=classification.segment_name_normalized,
            language=classification.language,
            needs_review=classification.needs_review,
            classification_rationale=classification.rationale,
        )
        evidence = _locate_evidence(row, parsed_pages)
        self.repository.create_segment_evidence(
            segment_id=segment.id,
            document_id=document.id,
            page_number=evidence.page_number,
            snippet_text=evidence.snippet_text,
            bbox_json=evidence.bbox_json,
            parser_source=evidence.parser_source,
            evidence_kind=evidence.evidence_kind,
            evidence_original=evidence.snippet_text,
            evidence_translation=None if classification.language == "en" else row.evidence_text,
            language=classification.language,
        )
        issues: list[ValidationIssue] = []
        if row.revenue_value is None:
            issues.append(
                self.repository.create_validation_issue(
                    document_id=document.id,
                    segment_id=segment.id,
                    severity="warning",
                    issue_type="missing_revenue_value",
                    message=f"Revenue value is null for extracted segment {row.segment_name}.",
                )
            )
        return segment, issues

    def _persist_document_processing_issues(
        self,
        *,
        document: Document,
        processing_issues: list[DeterministicValidationIssue],
        blocking_row_indexes: set[int],
    ) -> list[ValidationIssue]:
        persisted_issues: list[ValidationIssue] = []
        for issue in processing_issues:
            if issue.row_index is None or issue.row_index in blocking_row_indexes:
                persisted_issues.append(
                    self.repository.create_validation_issue(
                        document_id=document.id,
                        severity=issue.severity,
                        issue_type=issue.issue_type,
                        message=issue.message,
                    )
                )
        return persisted_issues

    def _persist_row_processing_issues(
        self,
        *,
        document: Document,
        segment_id: str,
        processing_issues: list[DeterministicValidationIssue],
    ) -> list[ValidationIssue]:
        return [
            self.repository.create_validation_issue(
                document_id=document.id,
                segment_id=segment_id,
                severity=issue.severity,
                issue_type=issue.issue_type,
                message=issue.message,
            )
            for issue in processing_issues
            if not issue.blocking
        ]

    def _update_document_status(
        self,
        document: Document,
        rows: list[SegmentRow],
        issues: list[ValidationIssue],
    ) -> Document:
        if any(issue.severity == "error" for issue in issues) and not rows:
            status = DOCUMENT_STATUS_FAILED
        elif any(row.status == SEGMENT_STATUS_NEEDS_REVIEW for row in rows):
            status = DOCUMENT_STATUS_NEEDS_REVIEW
        elif rows:
            status = DOCUMENT_STATUS_READY_FOR_REVIEW
        elif any(issue.severity in {"warning", "error"} for issue in issues):
            status = DOCUMENT_STATUS_NEEDS_REVIEW
        else:
            status = DOCUMENT_STATUS_READY_FOR_REVIEW
        return self.repository.update_document(document.id, status=status)

    def _summary(
        self,
        *,
        document: Document,
        candidates: list[PageCandidate],
        bundle_count: int,
        extracted_count: int,
        rows: list[SegmentRow],
        issues: list[ValidationIssue],
    ) -> ExtractionSummary:
        return ExtractionSummary(
            document=document,
            prompt_version=FIRST_PASS_PROMPT_VERSION,
            provider_name=self.provider.name,
            model=self.settings.model,
            candidate_page_count=len(candidates),
            bundle_count=bundle_count,
            extracted_row_count=extracted_count,
            persisted_row_count=len(rows),
            validation_issue_count=len(issues),
            segment_rows=tuple(rows),
            validation_issues=tuple(issues),
        )

    def _accepted_rows_for_bundle(
        self,
        *,
        document: Document,
        output: RevenueExtractionOutput,
        bundle: list[ParsedPage],
    ) -> tuple[list[ExtractedRevenueRow], list[ValidationIssue]]:
        pages_by_number = {page.page_number: page for page in bundle}
        accepted_rows: list[ExtractedRevenueRow] = []
        issues: list[ValidationIssue] = []
        for row in output.rows:
            page_number = _parse_page_number(row.page_ref)
            if page_number not in pages_by_number:
                issues.append(
                    self.repository.create_validation_issue(
                        document_id=document.id,
                        severity="warning",
                        issue_type="row_page_outside_prompt_bundle",
                        message=(
                            f"Skipped row for {row.segment_name} because page_ref "
                            f"{row.page_ref or 'null'} was not in the prompt bundle."
                        ),
                    )
                )
                continue

            page = pages_by_number[page_number]
            if is_non_segment_revenue_row(
                segment_name=row.segment_name,
                evidence_text=row.evidence_text,
                metric_basis=row.metric_basis,
                section_ref=row.section_ref,
                page_text=page.text,
            ):
                issues.append(
                    self.repository.create_validation_issue(
                        document_id=document.id,
                        severity="warning",
                        issue_type="non_segment_revenue_disclosure",
                        message=(
                            f"Skipped row for {row.segment_name} because the evidence "
                            "appears to come from a non-segment revenue disclosure."
                        ),
                    )
                )
                continue

            accepted_rows.append(row)
        return accepted_rows, issues

@dataclass(frozen=True)
class _EvidenceToStore:
    page_number: int
    snippet_text: str
    bbox_json: dict | None
    parser_source: str
    evidence_kind: str


def _build_page_bundles(
    candidates: list[PageCandidate],
    parsed_pages: list[ParsedPage],
    *,
    max_pages: int,
) -> list[list[ParsedPage]]:
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    pages_by_number = {page.page_number: page for page in parsed_pages}
    selected_numbers = _expand_context_page_numbers(
        sorted({candidate.page_number for candidate in candidates}),
        pages_by_number,
        max_pages=max_pages,
    )
    bundles: list[list[ParsedPage]] = []
    current: list[ParsedPage] = []
    previous_page_number: int | None = None

    for page_number in selected_numbers:
        page = pages_by_number.get(page_number)
        if page is None:
            continue
        starts_new_bundle = (
            not current
            or previous_page_number is None
            or page_number - previous_page_number > 1
            or len(current) >= max_pages
        )
        if starts_new_bundle and current:
            bundles.append(current)
            current = []
        current.append(page)
        previous_page_number = page_number

    if current:
        bundles.append(current)
    return bundles


def _bundle_count_for_candidates(
    candidates: list[PageCandidate],
    parsed_pages: list[ParsedPage],
    *,
    max_pages: int,
) -> int:
    if not candidates:
        return 0
    return len(_build_page_bundles(candidates, parsed_pages, max_pages=max_pages))


def _merge_candidates(
    existing: list[PageCandidate],
    discovered: list[PageCandidate],
) -> list[PageCandidate]:
    by_page_number = {candidate.page_number: candidate for candidate in existing}
    for candidate in discovered:
        by_page_number.setdefault(candidate.page_number, candidate)
    return sorted(
        by_page_number.values(),
        key=lambda candidate: (-candidate.relevance_score, candidate.page_number),
    )


def _page_batches(
    pages: list[ParsedPage],
    batch_size: int,
) -> list[list[ParsedPage]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    return [pages[index : index + batch_size] for index in range(0, len(pages), batch_size)]


def _align_rows_to_page_tables(
    rows: list[ExtractedRevenueRow],
    bundle: list[ParsedPage],
) -> list[ExtractedRevenueRow]:
    pages_by_number = {page.page_number: page for page in bundle}
    grouped: dict[int | None, list[ExtractedRevenueRow]] = {}
    for row in rows:
        grouped.setdefault(_parse_page_number(row.page_ref), []).append(row)

    aligned_rows: list[ExtractedRevenueRow] = []
    for page_number, page_rows in grouped.items():
        page = pages_by_number.get(page_number)
        if page is None:
            aligned_rows.extend(page_rows)
            continue
        aligned_rows.extend(align_rows_to_preferred_metric(page_rows, page))
    return aligned_rows


def _normalize_accepted_extractions(
    *,
    accepted_extractions: list[_AcceptedExtraction],
    latest_row_ids: set[int],
    document: Document,
    candidates: list[PageCandidate],
    parsed_pages: list[ParsedPage],
) -> list[NormalizedRevenueRow]:
    page_scores = {candidate.page_number: candidate.relevance_score for candidate in candidates}
    pages_by_number = {page.page_number: page for page in parsed_pages}
    return [
        normalize_extracted_row(
            item.row,
            item.output,
            document,
            page_relevance_score=page_scores.get(_parse_page_number(item.row.page_ref)),
            page_context_text=_page_context_text(item.row, pages_by_number),
        )
        for item in accepted_extractions
        if id(item.row) in latest_row_ids
    ]


def _prepare_normalized_rows(
    rows: list[NormalizedRevenueRow],
) -> tuple[list[PreparedRevenueRow], dict[int, NormalizedRevenueRow]]:
    prepared_rows = [_prepared_from_normalized(row) for row in rows]
    return prepared_rows, {id(row.source_row): row for row in rows}


def _prepared_from_normalized(row: NormalizedRevenueRow) -> PreparedRevenueRow:
    return PreparedRevenueRow(
        source_row=row.source_row,
        normalized_value=row.normalized_value,
        page_number=row.page_number,
        fiscal_period=row.fiscal_period,
    )


def _pages_for_rows(
    parsed_pages: list[ParsedPage],
    rows: list[NormalizedRevenueRow],
) -> list[ParsedPage]:
    page_numbers = {row.page_number for row in rows if row.page_number is not None}
    selected = [page for page in parsed_pages if page.page_number in page_numbers]
    return selected or parsed_pages[:3]


def _status_for_row(
    row_issues: list[DeterministicValidationIssue],
    *,
    reconciliation_status: str,
    has_document_review_issue: bool,
) -> str:
    if (
        row_issues
        or reconciliation_status == "failed"
        or has_document_review_issue
    ):
        return SEGMENT_STATUS_NEEDS_REVIEW
    return SEGMENT_STATUS_READY_FOR_REVIEW


def _expand_context_page_numbers(
    selected_numbers: list[int],
    pages_by_number: dict[int, ParsedPage],
    *,
    max_pages: int,
) -> list[int]:
    expanded_numbers: set[int] = set(selected_numbers)
    if max_pages < 2:
        return selected_numbers

    for page_number in selected_numbers:
        page = pages_by_number.get(page_number)
        next_page = pages_by_number.get(page_number + 1)
        if page is None or next_page is None:
            continue
        if page_number + 1 in expanded_numbers:
            continue
        if _should_include_next_context_page(page, next_page):
            expanded_numbers.add(page_number + 1)

    return sorted(expanded_numbers)


def _should_include_next_context_page(page: ParsedPage, next_page: ParsedPage) -> bool:
    if is_table_of_contents_page(next_page.text) or is_non_segment_page(next_page.text):
        return False

    normalized_text = _normalize_for_context(page.text)
    next_normalized_text = _normalize_for_context(next_page.text)
    is_segment_note_intro = bool(
        re.search(
            r"\b(note\s+\d+\s+segment|segment reporting|operating segments|"
            r"reportable segments|business segments)\b",
            normalized_text,
        )
    )
    if not is_segment_note_intro:
        return False

    page_has_revenue_table = _has_table_rows(page) and _has_revenue_metric(normalized_text)
    next_has_revenue_context = _has_table_rows(next_page) or _has_revenue_metric(next_normalized_text)
    return not page_has_revenue_table and next_has_revenue_context


def _has_table_rows(page: ParsedPage) -> bool:
    tables = page.tables_json.get("tables", []) if isinstance(page.tables_json, dict) else []
    return any(table.get("rows") for table in tables if isinstance(table, dict))


def _has_revenue_metric(normalized_text: str) -> bool:
    return bool(
        re.search(
            r"\b(external income|external revenue|net revenue|revenue|sales|turnover|"
            r"net interest income|net commission income|total operating income)\b",
            normalized_text,
        )
    )


def _normalize_for_context(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _parse_page_number(page_ref: str | None) -> int | None:
    if not page_ref:
        return None
    match = re.search(r"\d+", page_ref)
    return int(match.group(0)) if match else None


def _page_context_text(
    row: ExtractedRevenueRow,
    pages_by_number: dict[int, ParsedPage],
) -> str | None:
    page_number = _parse_page_number(row.page_ref)
    page = pages_by_number.get(page_number) if page_number is not None else None
    if page is None:
        return None
    return "\n".join(page.text.splitlines()[:80])


def _locate_evidence(row: NormalizedRevenueRow, parsed_pages: list[ParsedPage]) -> _EvidenceToStore:
    fallback_page_number = row.page_number or parsed_pages[0].page_number
    page = next(
        (parsed_page for parsed_page in parsed_pages if parsed_page.page_number == fallback_page_number),
        parsed_pages[0],
    )
    matches = locate_evidence_snippet(page, row.source_row.evidence_text, max_matches=1)
    if matches:
        match = matches[0]
        return _EvidenceToStore(
            page_number=int(match["page_number"]),
            snippet_text=str(match["snippet_text"]),
            bbox_json=match.get("bbox"),
            parser_source=str(match["parser_source"]),
            evidence_kind="page_text_block",
        )
    return _EvidenceToStore(
        page_number=page.page_number,
        snippet_text=row.source_row.evidence_text,
        bbox_json=None,
        parser_source="llm",
        evidence_kind="llm_evidence_text",
    )


def _first_evidence_text(evidence: list[SegmentEvidence]) -> str | None:
    if not evidence:
        return None
    return evidence[0].snippet_text


def _format_validation_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return str(exc)
    first_error = errors[0]
    location = ".".join(str(part) for part in first_error.get("loc", ())) or "response"
    message = first_error.get("msg", "invalid response")
    return f"{location}: {message}"
