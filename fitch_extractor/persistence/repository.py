from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fitch_extractor.enrichment import (
    classify_segment_row,
    default_esg_link_type,
    esg_category_for_factor,
    esg_cluster_key,
    normalize_english_meaning,
    score_relevant_esg_link,
)
from fitch_extractor.models import (
    DOCUMENT_STATUS_NEW,
    DOCUMENT_STATUS_APPROVED,
    EXPORT_READY_SEGMENT_STATUSES,
    QUEUE_STATUS_COMPLETED,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_RUNNING,
    SEGMENT_STATUS_PENDING,
    CompanyScore,
    Document,
    DocumentQueueJob,
    EsgFactor,
    ExportRecord,
    NaceCandidate,
    NaceSelection,
    PageCandidate,
    ParsedPage,
    ReviewEvent,
    SegmentEvidence,
    SegmentRow,
    SegmentScore,
    ValidationIssue,
    ValidationIssueReview,
)

IdFactory = Callable[[str], str]
Clock = Callable[[], datetime]


class SQLiteRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        id_factory: IdFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.connection = connection
        self._id_factory = id_factory or _default_id
        self._clock = clock or _default_clock

    def create_document(
        self,
        *,
        company_name: str,
        document_name: str,
        source_path: str,
        fiscal_period: str | None = None,
        status: str = DOCUMENT_STATUS_NEW,
        reported_total: Decimal | None = None,
        currency: str | None = None,
        scale: str | None = None,
        analysis_notes: str | None = None,
    ) -> Document:
        created_at = self._now()
        document = Document(
            id=self._new_id("doc"),
            company_name=company_name,
            document_name=document_name,
            source_path=source_path,
            fiscal_period=fiscal_period,
            status=status,
            reported_total=reported_total,
            currency=currency,
            scale=scale,
            created_at=created_at,
            updated_at=created_at,
            analysis_notes=analysis_notes,
        )
        return self.save_document(document)

    def save_document(self, document: Document) -> Document:
        self.connection.execute(
            """
            INSERT INTO documents (
                id, company_name, document_name, source_path, fiscal_period, status,
                reported_total, currency, scale, created_at, updated_at, analysis_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.id,
                document.company_name,
                document.document_name,
                document.source_path,
                document.fiscal_period,
                document.status,
                _decimal_to_text(document.reported_total),
                document.currency,
                document.scale,
                _datetime_to_text(document.created_at),
                _datetime_to_text(document.updated_at),
                document.analysis_notes,
            ),
        )
        self.connection.commit()
        return document

    def get_document(self, document_id: str) -> Document | None:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        return _document_from_row(row) if row else None

    def list_documents(self) -> list[Document]:
        rows = self.connection.execute(
            "SELECT * FROM documents ORDER BY created_at, id",
        ).fetchall()
        return [_document_from_row(row) for row in rows]

    def create_document_queue_job(
        self,
        *,
        document_id: str,
        requested_by: str,
        provider_name: str,
        model: str | None,
    ) -> DocumentQueueJob:
        self._require_document(document_id)
        job = DocumentQueueJob(
            id=self._new_id("job"),
            document_id=document_id,
            status=QUEUE_STATUS_PENDING,
            requested_by=requested_by,
            provider_name=provider_name,
            model=model,
            worker_id=None,
            error_message=None,
            created_at=self._now(),
            started_at=None,
            finished_at=None,
        )
        return self.save_document_queue_job(job)

    def save_document_queue_job(self, job: DocumentQueueJob) -> DocumentQueueJob:
        self.connection.execute(
            """
            INSERT INTO document_queue_jobs (
                id, document_id, status, requested_by, provider_name, model, worker_id,
                error_message, created_at, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.document_id,
                job.status,
                job.requested_by,
                job.provider_name,
                job.model,
                job.worker_id,
                job.error_message,
                _datetime_to_text(job.created_at),
                _optional_datetime_to_text(job.started_at),
                _optional_datetime_to_text(job.finished_at),
            ),
        )
        self.connection.commit()
        return job

    def get_document_queue_job(self, job_id: str) -> DocumentQueueJob | None:
        row = self.connection.execute(
            "SELECT * FROM document_queue_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return _document_queue_job_from_row(row) if row else None

    def list_document_queue_jobs(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[DocumentQueueJob]:
        query = "SELECT * FROM document_queue_jobs"
        values: list[object] = []
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            values.extend(statuses)
        query += " ORDER BY created_at, id"
        if limit is not None:
            query += " LIMIT ?"
            values.append(limit)

        rows = self.connection.execute(query, values).fetchall()
        return [_document_queue_job_from_row(row) for row in rows]

    def claim_next_document_queue_job(self, *, worker_id: str) -> DocumentQueueJob | None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                """
                SELECT * FROM document_queue_jobs
                WHERE status = ?
                ORDER BY created_at, id
                LIMIT 1
                """,
                (QUEUE_STATUS_PENDING,),
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None

            started_at = self._now()
            self.connection.execute(
                """
                UPDATE document_queue_jobs
                SET status = ?,
                    worker_id = ?,
                    error_message = NULL,
                    started_at = ?,
                    finished_at = NULL
                WHERE id = ?
                """,
                (QUEUE_STATUS_RUNNING, worker_id, _datetime_to_text(started_at), row["id"]),
            )
            self.connection.commit()
            return self.get_document_queue_job(row["id"])
        except Exception:
            self.connection.rollback()
            raise

    def complete_document_queue_job(self, job_id: str) -> DocumentQueueJob:
        return self._finish_document_queue_job(
            job_id,
            status=QUEUE_STATUS_COMPLETED,
            error_message=None,
        )

    def fail_document_queue_job(self, job_id: str, error_message: str) -> DocumentQueueJob:
        return self._finish_document_queue_job(
            job_id,
            status=QUEUE_STATUS_FAILED,
            error_message=error_message,
        )

    def _finish_document_queue_job(
        self,
        job_id: str,
        *,
        status: str,
        error_message: str | None,
    ) -> DocumentQueueJob:
        finished_at = self._now()
        cursor = self.connection.execute(
            """
            UPDATE document_queue_jobs
            SET status = ?,
                error_message = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (status, error_message, _datetime_to_text(finished_at), job_id),
        )
        self.connection.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Document queue job not found: {job_id}")
        job = self.get_document_queue_job(job_id)
        if job is None:
            raise KeyError(f"Document queue job not found: {job_id}")
        return job

    def update_document(self, document_id: str, **changes: Any) -> Document:
        allowed_fields = {
            "company_name",
            "document_name",
            "source_path",
            "fiscal_period",
            "status",
            "reported_total",
            "currency",
            "scale",
            "updated_at",
            "analysis_notes",
        }
        if not changes:
            return self._require_document(document_id)

        unknown_fields = set(changes) - allowed_fields
        if unknown_fields:
            raise ValueError(f"Unsupported document fields: {sorted(unknown_fields)}")

        changes.setdefault("updated_at", self._now())
        assignments = ", ".join(f"{field} = ?" for field in changes)
        values = [_document_db_value(field, value) for field, value in changes.items()]
        values.append(document_id)

        cursor = self.connection.execute(
            f"UPDATE documents SET {assignments} WHERE id = ?",
            values,
        )
        self.connection.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Document not found: {document_id}")
        return self._require_document(document_id)

    def delete_document(self, document_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def clear_parsed_outputs(self, document_id: str) -> None:
        self._require_document(document_id)
        self.connection.execute("DELETE FROM page_candidates WHERE document_id = ?", (document_id,))
        self.connection.execute("DELETE FROM parsed_pages WHERE document_id = ?", (document_id,))
        self.connection.commit()

    def create_parsed_page(
        self,
        *,
        document_id: str,
        page_number: int,
        text: str,
        blocks_json: JsonDict,
        tables_json: JsonDict,
        language: str | None,
        parser_sources: tuple[str, ...],
        has_text: bool,
    ) -> ParsedPage:
        page = ParsedPage(
            id=self._new_id("page"),
            document_id=document_id,
            page_number=page_number,
            text=text,
            blocks_json=blocks_json,
            tables_json=tables_json,
            language=language,
            parser_sources=parser_sources,
            has_text=has_text,
            created_at=self._now(),
        )
        return self.save_parsed_page(page)

    def save_parsed_page(self, page: ParsedPage) -> ParsedPage:
        self.connection.execute(
            """
            INSERT INTO parsed_pages (
                id, document_id, page_number, text, blocks_json, tables_json,
                language, parser_sources, has_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page.id,
                page.document_id,
                page.page_number,
                page.text,
                _json_to_text(page.blocks_json),
                _json_to_text(page.tables_json),
                page.language,
                _json_to_text(list(page.parser_sources)),
                int(page.has_text),
                _datetime_to_text(page.created_at),
            ),
        )
        self.connection.commit()
        return page

    def list_parsed_pages(self, document_id: str) -> list[ParsedPage]:
        rows = self.connection.execute(
            """
            SELECT * FROM parsed_pages
            WHERE document_id = ?
            ORDER BY page_number, id
            """,
            (document_id,),
        ).fetchall()
        return [_parsed_page_from_row(row) for row in rows]

    def create_page_candidate(
        self,
        *,
        document_id: str,
        page_number: int,
        relevance_score: float,
        matched_signals_json: JsonDict,
        reason: str,
    ) -> PageCandidate:
        candidate = PageCandidate(
            id=self._new_id("candidate"),
            document_id=document_id,
            page_number=page_number,
            relevance_score=relevance_score,
            matched_signals_json=matched_signals_json,
            reason=reason,
        )
        return self.save_page_candidate(candidate)

    def save_page_candidate(self, candidate: PageCandidate) -> PageCandidate:
        self.connection.execute(
            """
            INSERT INTO page_candidates (
                id, document_id, page_number, relevance_score, matched_signals_json, reason
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.id,
                candidate.document_id,
                candidate.page_number,
                candidate.relevance_score,
                _json_to_text(candidate.matched_signals_json),
                candidate.reason,
            ),
        )
        self.connection.commit()
        return candidate

    def list_page_candidates(self, document_id: str) -> list[PageCandidate]:
        rows = self.connection.execute(
            """
            SELECT * FROM page_candidates
            WHERE document_id = ?
            ORDER BY relevance_score DESC, page_number, id
            """,
            (document_id,),
        ).fetchall()
        return [_page_candidate_from_row(row) for row in rows]

    def create_segment_row(
        self,
        *,
        document_id: str,
        segment_name: str,
        revenue_raw: str | None = None,
        revenue_value: Decimal | None = None,
        currency: str | None = None,
        scale: str | None = None,
        period_label: str | None = None,
        normalized_value: Decimal | None = None,
        page_ref: str | None = None,
        section_ref: str | None = None,
        metric_basis: str | None = None,
        confidence: float | None = None,
        status: str = SEGMENT_STATUS_PENDING,
        extraction_method: str | None = None,
        row_type: str | None = None,
        segment_type: str | None = None,
        segment_name_original: str | None = None,
        segment_name_normalized: str | None = None,
        language: str | None = None,
        needs_review: bool | None = None,
        classification_rationale: str | None = None,
    ) -> SegmentRow:
        created_at = self._now()
        classification = classify_segment_row(
            segment_name,
            language=language,
            normalized_value=normalized_value,
        )
        segment = SegmentRow(
            id=self._new_id("seg"),
            document_id=document_id,
            segment_name=segment_name,
            revenue_raw=revenue_raw,
            revenue_value=revenue_value,
            currency=currency,
            scale=scale,
            period_label=period_label,
            normalized_value=normalized_value,
            page_ref=page_ref,
            section_ref=section_ref,
            metric_basis=metric_basis,
            confidence=confidence,
            status=status,
            extraction_method=extraction_method,
            created_at=created_at,
            updated_at=created_at,
            row_type=row_type or classification.row_type,
            segment_type=segment_type or classification.segment_type,
            segment_name_original=segment_name_original or classification.segment_name_original,
            segment_name_normalized=(
                segment_name_normalized or classification.segment_name_normalized
            ),
            language=language or classification.language,
            needs_review=classification.needs_review if needs_review is None else needs_review,
            classification_rationale=(
                classification_rationale or classification.rationale
            ),
        )
        return self.save_segment_row(segment)

    def save_segment_row(self, segment: SegmentRow) -> SegmentRow:
        self.connection.execute(
            """
            INSERT INTO segment_rows (
                id, document_id, segment_name, revenue_raw, revenue_value, currency,
                scale, period_label, normalized_value, page_ref, section_ref,
                metric_basis, confidence, status, extraction_method, created_at, updated_at,
                row_type, segment_type, segment_name_original, segment_name_normalized,
                language, needs_review, classification_rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment.id,
                segment.document_id,
                segment.segment_name,
                segment.revenue_raw,
                _decimal_to_text(segment.revenue_value),
                segment.currency,
                segment.scale,
                segment.period_label,
                _decimal_to_text(segment.normalized_value),
                segment.page_ref,
                segment.section_ref,
                segment.metric_basis,
                segment.confidence,
                segment.status,
                segment.extraction_method,
                _datetime_to_text(segment.created_at),
                _datetime_to_text(segment.updated_at),
                segment.row_type,
                segment.segment_type,
                segment.segment_name_original,
                segment.segment_name_normalized,
                segment.language,
                None if segment.needs_review is None else int(segment.needs_review),
                segment.classification_rationale,
            ),
        )
        self.connection.commit()
        return segment

    def get_segment_row(self, segment_id: str) -> SegmentRow | None:
        row = self.connection.execute(
            "SELECT * FROM segment_rows WHERE id = ?",
            (segment_id,),
        ).fetchone()
        return _segment_row_from_row(row) if row else None

    def list_segment_rows(self, document_id: str | None = None) -> list[SegmentRow]:
        if document_id:
            rows = self.connection.execute(
                """
                SELECT * FROM segment_rows
                WHERE document_id = ?
                ORDER BY created_at, id
                """,
                (document_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM segment_rows ORDER BY created_at, id",
            ).fetchall()
        return [_segment_row_from_row(row) for row in rows]

    def update_segment_row(self, segment_id: str, **changes: Any) -> SegmentRow:
        allowed_fields = {
            "segment_name",
            "revenue_raw",
            "revenue_value",
            "currency",
            "scale",
            "period_label",
            "normalized_value",
            "page_ref",
            "section_ref",
            "metric_basis",
            "confidence",
            "status",
            "extraction_method",
            "updated_at",
            "row_type",
            "segment_type",
            "segment_name_original",
            "segment_name_normalized",
            "language",
            "needs_review",
            "classification_rationale",
        }
        if not changes:
            return self._require_segment_row(segment_id)

        unknown_fields = set(changes) - allowed_fields
        if unknown_fields:
            raise ValueError(f"Unsupported segment fields: {sorted(unknown_fields)}")

        changes.setdefault("updated_at", self._now())
        assignments = ", ".join(f"{field} = ?" for field in changes)
        values = [_segment_db_value(field, value) for field, value in changes.items()]
        values.append(segment_id)

        cursor = self.connection.execute(
            f"UPDATE segment_rows SET {assignments} WHERE id = ?",
            values,
        )
        self.connection.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Segment row not found: {segment_id}")
        return self._require_segment_row(segment_id)

    def save_segment_evidence(self, evidence: SegmentEvidence) -> SegmentEvidence:
        self.connection.execute(
            """
            INSERT INTO segment_evidence (
                id, segment_id, document_id, page_number, snippet_text,
                bbox_json, parser_source, evidence_kind, evidence_original,
                evidence_translation, language
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence.id,
                evidence.segment_id,
                evidence.document_id,
                evidence.page_number,
                evidence.snippet_text,
                _json_to_text(evidence.bbox_json) if evidence.bbox_json is not None else None,
                evidence.parser_source,
                evidence.evidence_kind,
                evidence.evidence_original,
                evidence.evidence_translation,
                evidence.language,
            ),
        )
        self.connection.commit()
        return evidence

    def create_segment_evidence(
        self,
        *,
        segment_id: str,
        document_id: str,
        page_number: int,
        snippet_text: str,
        bbox_json: JsonDict | None = None,
        parser_source: str,
        evidence_kind: str,
        evidence_original: str | None = None,
        evidence_translation: str | None = None,
        language: str | None = None,
    ) -> SegmentEvidence:
        evidence = SegmentEvidence(
            id=self._new_id("evidence"),
            segment_id=segment_id,
            document_id=document_id,
            page_number=page_number,
            snippet_text=snippet_text,
            bbox_json=bbox_json,
            parser_source=parser_source,
            evidence_kind=evidence_kind,
            evidence_original=evidence_original or snippet_text,
            evidence_translation=evidence_translation,
            language=language,
        )
        return self.save_segment_evidence(evidence)

    def list_segment_evidence(self, segment_id: str) -> list[SegmentEvidence]:
        rows = self.connection.execute(
            """
            SELECT * FROM segment_evidence
            WHERE segment_id = ?
            ORDER BY page_number, id
            """,
            (segment_id,),
        ).fetchall()
        return [_segment_evidence_from_row(row) for row in rows]

    def save_validation_issue(self, issue: ValidationIssue) -> ValidationIssue:
        self.connection.execute(
            """
            INSERT INTO validation_issues (
                id, document_id, segment_id, severity, issue_type, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue.id,
                issue.document_id,
                issue.segment_id,
                issue.severity,
                issue.issue_type,
                issue.message,
                _datetime_to_text(issue.created_at),
            ),
        )
        self.connection.commit()
        return issue

    def create_validation_issue(
        self,
        *,
        document_id: str,
        severity: str,
        issue_type: str,
        message: str,
        segment_id: str | None = None,
    ) -> ValidationIssue:
        issue = ValidationIssue(
            id=self._new_id("issue"),
            document_id=document_id,
            segment_id=segment_id,
            severity=severity,
            issue_type=issue_type,
            message=message,
            created_at=self._now(),
        )
        return self.save_validation_issue(issue)

    def list_validation_issues(self, document_id: str) -> list[ValidationIssue]:
        rows = self.connection.execute(
            """
            SELECT * FROM validation_issues
            WHERE document_id = ?
            ORDER BY created_at, id
            """,
            (document_id,),
        ).fetchall()
        return [_validation_issue_from_row(row) for row in rows]

    def get_validation_issue(self, issue_id: str) -> ValidationIssue | None:
        row = self.connection.execute(
            "SELECT * FROM validation_issues WHERE id = ?",
            (issue_id,),
        ).fetchone()
        return _validation_issue_from_row(row) if row else None

    def get_validation_issue_review(self, issue_id: str) -> ValidationIssueReview | None:
        row = self.connection.execute(
            "SELECT * FROM validation_issue_reviews WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()
        return _validation_issue_review_from_row(row) if row else None

    def list_validation_issue_reviews(self, document_id: str) -> list[ValidationIssueReview]:
        rows = self.connection.execute(
            """
            SELECT * FROM validation_issue_reviews
            WHERE document_id = ?
            ORDER BY updated_at, issue_id
            """,
            (document_id,),
        ).fetchall()
        return [_validation_issue_review_from_row(row) for row in rows]

    def upsert_validation_issue_review(
        self,
        *,
        issue_id: str,
        document_id: str,
        status: str,
        reviewer: str,
        note: str | None = None,
    ) -> ValidationIssueReview:
        issue = self.get_validation_issue(issue_id)
        if issue is None:
            raise KeyError(f"Validation issue not found: {issue_id}")
        if issue.document_id != document_id:
            raise ValueError("Validation issue does not belong to the supplied document")

        review = ValidationIssueReview(
            issue_id=issue_id,
            document_id=document_id,
            status=status,
            reviewer=reviewer,
            note=note,
            updated_at=self._now(),
        )
        self.connection.execute(
            """
            INSERT INTO validation_issue_reviews (
                issue_id, document_id, status, reviewer, note, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(issue_id) DO UPDATE SET
                status = excluded.status,
                reviewer = excluded.reviewer,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                review.issue_id,
                review.document_id,
                review.status,
                review.reviewer,
                review.note,
                _datetime_to_text(review.updated_at),
            ),
        )
        self.connection.commit()
        return review

    def save_nace_candidate(self, candidate: NaceCandidate) -> NaceCandidate:
        self.connection.execute(
            """
            INSERT INTO nace_candidates (
                id, segment_id, nace_code, nace_label, nace_level, rank, match_score, rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.id,
                candidate.segment_id,
                candidate.nace_code,
                candidate.nace_label,
                candidate.nace_level,
                candidate.rank,
                candidate.match_score,
                candidate.rationale,
            ),
        )
        self.connection.commit()
        return candidate

    def create_nace_candidate(
        self,
        *,
        segment_id: str,
        nace_code: str,
        nace_label: str,
        nace_level: int,
        rank: int,
        match_score: float,
        rationale: str | None = None,
    ) -> NaceCandidate:
        self._require_segment_row(segment_id)
        candidate = NaceCandidate(
            id=self._new_id("nace"),
            segment_id=segment_id,
            nace_code=nace_code,
            nace_label=nace_label,
            nace_level=nace_level,
            rank=rank,
            match_score=match_score,
            rationale=rationale,
        )
        return self.save_nace_candidate(candidate)

    def replace_nace_candidates(
        self,
        segment_id: str,
        candidates: list[NaceCandidate],
    ) -> list[NaceCandidate]:
        self._require_segment_row(segment_id)
        if any(candidate.segment_id != segment_id for candidate in candidates):
            raise ValueError("All NACE candidates must belong to the supplied segment")
        persisted_candidates = [
            candidate
            if candidate.id
            else NaceCandidate(
                id=self._new_id("nace"),
                segment_id=candidate.segment_id,
                nace_code=candidate.nace_code,
                nace_label=candidate.nace_label,
                nace_level=candidate.nace_level,
                rank=candidate.rank,
                match_score=candidate.match_score,
                rationale=candidate.rationale,
            )
            for candidate in candidates
        ]
        self.connection.execute("DELETE FROM nace_candidates WHERE segment_id = ?", (segment_id,))
        for candidate in persisted_candidates:
            self.connection.execute(
                """
                INSERT INTO nace_candidates (
                    id, segment_id, nace_code, nace_label, nace_level,
                    rank, match_score, rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.id,
                    candidate.segment_id,
                    candidate.nace_code,
                    candidate.nace_label,
                    candidate.nace_level,
                    candidate.rank,
                    candidate.match_score,
                    candidate.rationale,
                ),
            )
        self.connection.commit()
        return self.list_nace_candidates(segment_id)

    def list_nace_candidates(self, segment_id: str) -> list[NaceCandidate]:
        rows = self.connection.execute(
            """
            SELECT * FROM nace_candidates
            WHERE segment_id = ?
            ORDER BY rank, match_score DESC, id
            """,
            (segment_id,),
        ).fetchall()
        return [_nace_candidate_from_row(row) for row in rows]

    def get_nace_selection(self, segment_id: str) -> NaceSelection | None:
        row = self.connection.execute(
            "SELECT * FROM segment_nace_selections WHERE segment_id = ?",
            (segment_id,),
        ).fetchone()
        return _nace_selection_from_row(row) if row else None

    def delete_nace_selection(self, segment_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM segment_nace_selections WHERE segment_id = ?",
            (segment_id,),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def list_nace_selections(self, document_id: str) -> list[NaceSelection]:
        rows = self.connection.execute(
            """
            SELECT selections.*
            FROM segment_nace_selections AS selections
            JOIN segment_rows AS rows ON rows.id = selections.segment_id
            WHERE rows.document_id = ?
            ORDER BY rows.created_at, rows.id
            """,
            (document_id,),
        ).fetchall()
        return [_nace_selection_from_row(row) for row in rows]

    def upsert_nace_selection(
        self,
        *,
        segment_id: str,
        nace_code: str,
        nace_label: str,
        nace_level: int,
        match_score: float | None = None,
        rationale: str | None = None,
        source: str,
        reviewer: str | None = None,
    ) -> NaceSelection:
        self._require_segment_row(segment_id)
        selection = NaceSelection(
            segment_id=segment_id,
            nace_code=nace_code,
            nace_label=nace_label,
            nace_level=nace_level,
            match_score=match_score,
            rationale=rationale,
            source=source,
            reviewer=reviewer,
            updated_at=self._now(),
        )
        self.connection.execute(
            """
            INSERT INTO segment_nace_selections (
                segment_id, nace_code, nace_label, nace_level, match_score,
                rationale, source, reviewer, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(segment_id) DO UPDATE SET
                nace_code = excluded.nace_code,
                nace_label = excluded.nace_label,
                nace_level = excluded.nace_level,
                match_score = excluded.match_score,
                rationale = excluded.rationale,
                source = excluded.source,
                reviewer = excluded.reviewer,
                updated_at = excluded.updated_at
            """,
            (
                selection.segment_id,
                selection.nace_code,
                selection.nace_label,
                selection.nace_level,
                selection.match_score,
                selection.rationale,
                selection.source,
                selection.reviewer,
                _datetime_to_text(selection.updated_at),
            ),
        )
        self.connection.commit()
        return selection

    def save_esg_factor(self, factor: EsgFactor) -> EsgFactor:
        self.connection.execute(
            """
            INSERT INTO esg_factors (
                id, segment_id, document_id, factor_type, polarity, description,
                page_ref, evidence_text, confidence, is_company_wide,
                segment_link_type, esg_category, score_relevant, impact_mechanism,
                evidence_source, cluster_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                factor.id,
                factor.segment_id,
                factor.document_id,
                factor.factor_type,
                factor.polarity,
                factor.description,
                factor.page_ref,
                factor.evidence_text,
                factor.confidence,
                int(factor.is_company_wide),
                factor.segment_link_type,
                factor.esg_category,
                None if factor.score_relevant is None else int(factor.score_relevant),
                factor.impact_mechanism,
                factor.evidence_source,
                factor.cluster_key,
            ),
        )
        self.connection.commit()
        return factor

    def create_esg_factor(
        self,
        *,
        document_id: str,
        segment_id: str | None,
        factor_type: str,
        polarity: str,
        description: str,
        page_ref: str | None,
        evidence_text: str,
        confidence: float | None,
        is_company_wide: bool,
        segment_link_type: str | None = None,
        esg_category: str | None = None,
        score_relevant: bool | None = None,
        impact_mechanism: str | None = None,
        evidence_source: str | None = None,
        cluster_key: str | None = None,
    ) -> EsgFactor:
        self._require_document(document_id)
        if segment_id is not None:
            segment = self._require_segment_row(segment_id)
            if segment.document_id != document_id:
                raise ValueError("ESG factor segment does not belong to the supplied document")

        link_type = segment_link_type or default_esg_link_type(
            is_company_wide=is_company_wide,
            segment_name=None if segment_id is None else segment.segment_name,
            linked_business_activity=impact_mechanism,
        )
        category = esg_category or esg_category_for_factor(factor_type)
        relevant = (
            score_relevant
            if score_relevant is not None
            else score_relevant_esg_link(link_type, is_company_wide)
        )
        mechanism = impact_mechanism or normalize_english_meaning(factor_type).replace(" ", "_")
        source = evidence_source or page_ref
        factor = EsgFactor(
            id=self._new_id("esg"),
            segment_id=segment_id,
            document_id=document_id,
            factor_type=factor_type,
            polarity=polarity,
            description=description,
            page_ref=page_ref,
            evidence_text=evidence_text,
            confidence=confidence,
            is_company_wide=is_company_wide,
            segment_link_type=link_type,
            esg_category=category,
            score_relevant=relevant,
            impact_mechanism=mechanism,
            evidence_source=source,
            cluster_key=cluster_key
            or esg_cluster_key(
                segment_id=segment_id,
                esg_category=category,
                factor_type=factor_type,
                impact_mechanism=mechanism,
                page_ref=source,
            ),
        )
        return self.save_esg_factor(factor)

    def get_esg_factor(self, factor_id: str) -> EsgFactor | None:
        row = self.connection.execute(
            "SELECT * FROM esg_factors WHERE id = ?",
            (factor_id,),
        ).fetchone()
        return _esg_factor_from_row(row) if row else None

    def list_esg_factors(self, document_id: str) -> list[EsgFactor]:
        rows = self.connection.execute(
            """
            SELECT * FROM esg_factors
            WHERE document_id = ?
            ORDER BY id
            """,
            (document_id,),
        ).fetchall()
        return [_esg_factor_from_row(row) for row in rows]

    def update_esg_factor(self, factor_id: str, **changes: Any) -> EsgFactor:
        allowed_fields = {
            "segment_id",
            "factor_type",
            "polarity",
            "description",
            "page_ref",
            "evidence_text",
            "confidence",
            "is_company_wide",
            "segment_link_type",
            "esg_category",
            "score_relevant",
            "impact_mechanism",
            "evidence_source",
            "cluster_key",
        }
        if not changes:
            factor = self.get_esg_factor(factor_id)
            if factor is None:
                raise KeyError(f"ESG factor not found: {factor_id}")
            return factor

        unknown_fields = set(changes) - allowed_fields
        if unknown_fields:
            raise ValueError(f"Unsupported ESG factor fields: {sorted(unknown_fields)}")

        current = self.get_esg_factor(factor_id)
        if current is None:
            raise KeyError(f"ESG factor not found: {factor_id}")
        segment_id = changes.get("segment_id", current.segment_id)
        if segment_id is not None:
            segment = self._require_segment_row(segment_id)
            if segment.document_id != current.document_id:
                raise ValueError("ESG factor segment does not belong to the factor document")

        assignments = ", ".join(f"{field} = ?" for field in changes)
        values = [_esg_factor_db_value(field, value) for field, value in changes.items()]
        values.append(factor_id)
        self.connection.execute(f"UPDATE esg_factors SET {assignments} WHERE id = ?", values)
        self.connection.commit()
        return self.get_esg_factor(factor_id) or current

    def save_segment_score(self, score: SegmentScore) -> SegmentScore:
        self.connection.execute(
            """
            INSERT INTO segment_scores (
                id, segment_id, base_score, adjustment_score, final_score, weight_share, rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score.id,
                score.segment_id,
                score.base_score,
                score.adjustment_score,
                score.final_score,
                score.weight_share,
                score.rationale,
            ),
        )
        self.connection.commit()
        return score

    def list_segment_scores(self, segment_id: str) -> list[SegmentScore]:
        rows = self.connection.execute(
            """
            SELECT * FROM segment_scores
            WHERE segment_id = ?
            ORDER BY id
            """,
            (segment_id,),
        ).fetchall()
        return [_segment_score_from_row(row) for row in rows]

    def list_document_segment_scores(self, document_id: str) -> list[SegmentScore]:
        rows = self.connection.execute(
            """
            SELECT scores.*
            FROM segment_scores AS scores
            JOIN segment_rows AS segments ON segments.id = scores.segment_id
            WHERE segments.document_id = ?
            ORDER BY segments.created_at, segments.id, scores.id
            """,
            (document_id,),
        ).fetchall()
        return [_segment_score_from_row(row) for row in rows]

    def save_company_score(self, score: CompanyScore) -> CompanyScore:
        self.connection.execute(
            """
            INSERT INTO company_scores (
                id, document_id, weighted_average_score, included_weight_share,
                included_segment_count, denominator_value, scale_min, scale_max,
                score_direction, rationale, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score.id,
                score.document_id,
                score.weighted_average_score,
                score.included_weight_share,
                score.included_segment_count,
                _decimal_to_text(score.denominator_value),
                score.scale_min,
                score.scale_max,
                score.score_direction,
                score.rationale,
                _datetime_to_text(score.created_at),
            ),
        )
        self.connection.commit()
        return score

    def list_company_scores(self, document_id: str) -> list[CompanyScore]:
        rows = self.connection.execute(
            """
            SELECT * FROM company_scores
            WHERE document_id = ?
            ORDER BY created_at, id
            """,
            (document_id,),
        ).fetchall()
        return [_company_score_from_row(row) for row in rows]

    def replace_document_scores(
        self,
        document_id: str,
        *,
        segment_scores: list[SegmentScore],
        company_score: CompanyScore | None,
    ) -> None:
        self._require_document(document_id)
        segment_ids = [row.id for row in self.list_segment_rows(document_id)]
        segment_id_set = set(segment_ids)
        for score in segment_scores:
            if score.segment_id not in segment_id_set:
                raise ValueError("Segment score does not belong to the supplied document")
        if company_score is not None and company_score.document_id != document_id:
            raise ValueError("Company score does not belong to the supplied document")

        if segment_ids:
            placeholders = ", ".join("?" for _ in segment_ids)
            self.connection.execute(
                f"DELETE FROM segment_scores WHERE segment_id IN ({placeholders})",
                segment_ids,
            )
        self.connection.execute("DELETE FROM company_scores WHERE document_id = ?", (document_id,))

        for score in segment_scores:
            self.connection.execute(
                """
                INSERT INTO segment_scores (
                    id, segment_id, base_score, adjustment_score,
                    final_score, weight_share, rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    score.id,
                    score.segment_id,
                    score.base_score,
                    score.adjustment_score,
                    score.final_score,
                    score.weight_share,
                    score.rationale,
                ),
            )

        if company_score is not None:
            self.connection.execute(
                """
                INSERT INTO company_scores (
                    id, document_id, weighted_average_score, included_weight_share,
                    included_segment_count, denominator_value, scale_min, scale_max,
                    score_direction, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_score.id,
                    company_score.document_id,
                    company_score.weighted_average_score,
                    company_score.included_weight_share,
                    company_score.included_segment_count,
                    _decimal_to_text(company_score.denominator_value),
                    company_score.scale_min,
                    company_score.scale_max,
                    company_score.score_direction,
                    company_score.rationale,
                    _datetime_to_text(company_score.created_at),
                ),
            )
        self.connection.commit()

    def create_review_event(
        self,
        *,
        document_id: str,
        reviewer: str,
        action: str,
        segment_id: str | None = None,
        field_changed: str | None = None,
        old_value: str | None = None,
        new_value: str | None = None,
        note: str | None = None,
    ) -> ReviewEvent:
        event = ReviewEvent(
            id=self._new_id("review"),
            document_id=document_id,
            segment_id=segment_id,
            reviewer=reviewer,
            action=action,
            field_changed=field_changed,
            old_value=old_value,
            new_value=new_value,
            note=note,
            timestamp=self._now(),
        )
        return self.save_review_event(event)

    def save_review_event(self, event: ReviewEvent) -> ReviewEvent:
        self.connection.execute(
            """
            INSERT INTO review_events (
                id, document_id, segment_id, reviewer, action, field_changed,
                old_value, new_value, note, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.document_id,
                event.segment_id,
                event.reviewer,
                event.action,
                event.field_changed,
                event.old_value,
                event.new_value,
                event.note,
                _datetime_to_text(event.timestamp),
            ),
        )
        self.connection.commit()
        return event

    def list_review_events(self, document_id: str) -> list[ReviewEvent]:
        rows = self.connection.execute(
            """
            SELECT * FROM review_events
            WHERE document_id = ?
            ORDER BY timestamp, id
            """,
            (document_id,),
        ).fetchall()
        return [_review_event_from_row(row) for row in rows]

    def is_document_export_ready(self, document_id: str) -> bool:
        document = self.get_document(document_id)
        if document is None or document.status != DOCUMENT_STATUS_APPROVED:
            return False

        pending_count = self.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM segment_rows
            WHERE document_id = ?
              AND status NOT IN (?, ?, ?)
            """,
            (document_id, *sorted(EXPORT_READY_SEGMENT_STATUSES)),
        ).fetchone()["count"]
        return pending_count == 0

    def create_export_record(
        self,
        *,
        document_id: str,
        format: str,
        path: str,
        enforce_review_gate: bool = True,
    ) -> ExportRecord:
        if enforce_review_gate and not self.is_document_export_ready(document_id):
            raise ValueError("Cannot create export record before document approval")

        export = ExportRecord(
            id=self._new_id("export"),
            document_id=document_id,
            format=format,
            path=path,
            created_at=self._now(),
        )
        return self.save_export_record(export)

    def save_export_record(self, export: ExportRecord) -> ExportRecord:
        self.connection.execute(
            """
            INSERT INTO export_records (id, document_id, format, path, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                export.id,
                export.document_id,
                export.format,
                export.path,
                _datetime_to_text(export.created_at),
            ),
        )
        self.connection.commit()
        return export

    def list_export_records(self, document_id: str) -> list[ExportRecord]:
        rows = self.connection.execute(
            """
            SELECT * FROM export_records
            WHERE document_id = ?
            ORDER BY created_at, id
            """,
            (document_id,),
        ).fetchall()
        return [_export_record_from_row(row) for row in rows]

    def _new_id(self, prefix: str) -> str:
        return self._id_factory(prefix)

    def _now(self) -> datetime:
        return self._clock()

    def _require_document(self, document_id: str) -> Document:
        document = self.get_document(document_id)
        if not document:
            raise KeyError(f"Document not found: {document_id}")
        return document

    def _require_segment_row(self, segment_id: str) -> SegmentRow:
        segment = self.get_segment_row(segment_id)
        if not segment:
            raise KeyError(f"Segment row not found: {segment_id}")
        return segment


def _default_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _datetime_to_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _optional_datetime_to_text(value: datetime | None) -> str | None:
    return _datetime_to_text(value) if value is not None else None


def _datetime_from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_datetime_from_text(value: str | None) -> datetime | None:
    return _datetime_from_text(value) if value is not None else None


def _decimal_to_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _decimal_from_text(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _json_to_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _json_from_text(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _document_db_value(field: str, value: Any) -> Any:
    if field == "reported_total":
        return _decimal_to_text(value)
    if field == "updated_at":
        return _datetime_to_text(value)
    return value


def _segment_db_value(field: str, value: Any) -> Any:
    if field in {"revenue_value", "normalized_value"}:
        return _decimal_to_text(value)
    if field == "updated_at":
        return _datetime_to_text(value)
    if field == "needs_review":
        return None if value is None else int(bool(value))
    return value


def _esg_factor_db_value(field: str, value: Any) -> Any:
    if field in {"is_company_wide", "score_relevant"}:
        return None if value is None else int(bool(value))
    return value


def _document_from_row(row: sqlite3.Row) -> Document:
    return Document(
        id=row["id"],
        company_name=row["company_name"],
        document_name=row["document_name"],
        source_path=row["source_path"],
        fiscal_period=row["fiscal_period"],
        status=row["status"],
        reported_total=_decimal_from_text(row["reported_total"]),
        currency=row["currency"],
        scale=row["scale"],
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
        analysis_notes=row["analysis_notes"],
    )


def _document_queue_job_from_row(row: sqlite3.Row) -> DocumentQueueJob:
    return DocumentQueueJob(
        id=row["id"],
        document_id=row["document_id"],
        status=row["status"],
        requested_by=row["requested_by"],
        provider_name=row["provider_name"],
        model=row["model"],
        worker_id=row["worker_id"],
        error_message=row["error_message"],
        created_at=_datetime_from_text(row["created_at"]),
        started_at=_optional_datetime_from_text(row["started_at"]),
        finished_at=_optional_datetime_from_text(row["finished_at"]),
    )


def _parsed_page_from_row(row: sqlite3.Row) -> ParsedPage:
    return ParsedPage(
        id=row["id"],
        document_id=row["document_id"],
        page_number=row["page_number"],
        text=row["text"],
        blocks_json=_json_from_text(row["blocks_json"], {}),
        tables_json=_json_from_text(row["tables_json"], {}),
        language=row["language"],
        parser_sources=tuple(_json_from_text(row["parser_sources"], [])),
        has_text=bool(row["has_text"]),
        created_at=_datetime_from_text(row["created_at"]),
    )


def _page_candidate_from_row(row: sqlite3.Row) -> PageCandidate:
    return PageCandidate(
        id=row["id"],
        document_id=row["document_id"],
        page_number=row["page_number"],
        relevance_score=row["relevance_score"],
        matched_signals_json=_json_from_text(row["matched_signals_json"], {}),
        reason=row["reason"],
    )


def _segment_row_from_row(row: sqlite3.Row) -> SegmentRow:
    return SegmentRow(
        id=row["id"],
        document_id=row["document_id"],
        segment_name=row["segment_name"],
        revenue_raw=row["revenue_raw"],
        revenue_value=_decimal_from_text(row["revenue_value"]),
        currency=row["currency"],
        scale=row["scale"],
        period_label=row["period_label"],
        normalized_value=_decimal_from_text(row["normalized_value"]),
        page_ref=row["page_ref"],
        section_ref=row["section_ref"],
        metric_basis=row["metric_basis"],
        confidence=row["confidence"],
        status=row["status"],
        extraction_method=row["extraction_method"],
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
        row_type=row["row_type"],
        segment_type=row["segment_type"],
        segment_name_original=row["segment_name_original"],
        segment_name_normalized=row["segment_name_normalized"],
        language=row["language"],
        needs_review=None if row["needs_review"] is None else bool(row["needs_review"]),
        classification_rationale=row["classification_rationale"],
    )


def _segment_evidence_from_row(row: sqlite3.Row) -> SegmentEvidence:
    return SegmentEvidence(
        id=row["id"],
        segment_id=row["segment_id"],
        document_id=row["document_id"],
        page_number=row["page_number"],
        snippet_text=row["snippet_text"],
        bbox_json=_json_from_text(row["bbox_json"], None),
        parser_source=row["parser_source"],
        evidence_kind=row["evidence_kind"],
        evidence_original=row["evidence_original"],
        evidence_translation=row["evidence_translation"],
        language=row["language"],
    )


def _validation_issue_from_row(row: sqlite3.Row) -> ValidationIssue:
    return ValidationIssue(
        id=row["id"],
        document_id=row["document_id"],
        segment_id=row["segment_id"],
        severity=row["severity"],
        issue_type=row["issue_type"],
        message=row["message"],
        created_at=_datetime_from_text(row["created_at"]),
    )


def _validation_issue_review_from_row(row: sqlite3.Row) -> ValidationIssueReview:
    return ValidationIssueReview(
        issue_id=row["issue_id"],
        document_id=row["document_id"],
        status=row["status"],
        reviewer=row["reviewer"],
        note=row["note"],
        updated_at=_datetime_from_text(row["updated_at"]),
    )


def _nace_candidate_from_row(row: sqlite3.Row) -> NaceCandidate:
    return NaceCandidate(
        id=row["id"],
        segment_id=row["segment_id"],
        nace_code=row["nace_code"],
        nace_label=row["nace_label"],
        nace_level=row["nace_level"],
        rank=row["rank"],
        match_score=row["match_score"],
        rationale=row["rationale"],
    )


def _nace_selection_from_row(row: sqlite3.Row) -> NaceSelection:
    return NaceSelection(
        segment_id=row["segment_id"],
        nace_code=row["nace_code"],
        nace_label=row["nace_label"],
        nace_level=row["nace_level"],
        match_score=row["match_score"],
        rationale=row["rationale"],
        source=row["source"],
        reviewer=row["reviewer"],
        updated_at=_datetime_from_text(row["updated_at"]),
    )


def _esg_factor_from_row(row: sqlite3.Row) -> EsgFactor:
    return EsgFactor(
        id=row["id"],
        segment_id=row["segment_id"],
        document_id=row["document_id"],
        factor_type=row["factor_type"],
        polarity=row["polarity"],
        description=row["description"],
        page_ref=row["page_ref"],
        evidence_text=row["evidence_text"],
        confidence=row["confidence"],
        is_company_wide=bool(row["is_company_wide"]),
        segment_link_type=row["segment_link_type"],
        esg_category=row["esg_category"],
        score_relevant=(
            None if row["score_relevant"] is None else bool(row["score_relevant"])
        ),
        impact_mechanism=row["impact_mechanism"],
        evidence_source=row["evidence_source"],
        cluster_key=row["cluster_key"],
    )


def _segment_score_from_row(row: sqlite3.Row) -> SegmentScore:
    return SegmentScore(
        id=row["id"],
        segment_id=row["segment_id"],
        base_score=row["base_score"],
        adjustment_score=row["adjustment_score"],
        final_score=row["final_score"],
        weight_share=row["weight_share"],
        rationale=row["rationale"],
    )


def _company_score_from_row(row: sqlite3.Row) -> CompanyScore:
    return CompanyScore(
        id=row["id"],
        document_id=row["document_id"],
        weighted_average_score=row["weighted_average_score"],
        included_weight_share=row["included_weight_share"],
        included_segment_count=row["included_segment_count"],
        denominator_value=_decimal_from_text(row["denominator_value"]),
        scale_min=row["scale_min"],
        scale_max=row["scale_max"],
        score_direction=row["score_direction"],
        rationale=row["rationale"],
        created_at=_datetime_from_text(row["created_at"]),
    )


def _review_event_from_row(row: sqlite3.Row) -> ReviewEvent:
    return ReviewEvent(
        id=row["id"],
        document_id=row["document_id"],
        segment_id=row["segment_id"],
        reviewer=row["reviewer"],
        action=row["action"],
        field_changed=row["field_changed"],
        old_value=row["old_value"],
        new_value=row["new_value"],
        note=row["note"],
        timestamp=_datetime_from_text(row["timestamp"]),
    )


def _export_record_from_row(row: sqlite3.Row) -> ExportRecord:
    return ExportRecord(
        id=row["id"],
        document_id=row["document_id"],
        format=row["format"],
        path=row["path"],
        created_at=_datetime_from_text(row["created_at"]),
    )
