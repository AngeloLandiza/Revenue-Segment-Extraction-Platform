from __future__ import annotations

import json
import re
from html import escape
from decimal import InvalidOperation
from pathlib import Path

import streamlit as st

from revenue_segment_extractor.extraction import (
    EsgExtractionService,
    ExtractionSettings,
    create_provider,
)
from revenue_segment_extractor.extraction.providers import LLMProviderError
from revenue_segment_extractor.extraction.usage import TrackedLLMProvider, WorkflowUsageTracker
from revenue_segment_extractor.exporting import ExportService
from revenue_segment_extractor.ingestion import (
    PdfIngestionService,
    locate_evidence_snippet,
    render_page_with_bbox_to_png,
)
from revenue_segment_extractor.models import (
    DOCUMENT_STATUS_APPROVED,
    EsgFactor,
    ExportRecord,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_RUNNING,
    ParsedPage,
    SEGMENT_STATUS_APPROVED,
    SEGMENT_STATUS_REJECTED,
    SegmentEvidence,
    SegmentScore,
    VALIDATION_ISSUE_STATUS_ACKNOWLEDGED,
    VALIDATION_ISSUE_STATUS_RESOLVED,
)
from revenue_segment_extractor.nace import NaceMappingService
from revenue_segment_extractor.persistence import (
    DEFAULT_DATABASE_PATH,
    ReviewService,
    SQLiteRepository,
    connect_database,
    initialize_database,
)
from revenue_segment_extractor.persistence.review import DocumentReviewState
from revenue_segment_extractor.persistence.review import REVIEWED_ESG_STATUSES
from revenue_segment_extractor.queueing import DocumentQueueService
from revenue_segment_extractor.scoring import ScoringService
from revenue_segment_extractor.ui.review import (
    build_pipeline_steps,
    build_review_tasks,
    build_summary_cards,
    can_export,
    changed_esg_factor_rows,
    changed_segment_rows,
    current_pipeline_step,
    esg_factor_table_rows,
    nace_candidate_table_rows,
    pipeline_progress,
    segment_table_rows,
)


UPLOAD_DIR = Path("data/uploads")
EVIDENCE_PREVIEW_DIR = Path("data/evidence_previews")
REVIEWER_PLACEHOLDER = "analyst@example.com"
AUTO_APPROVE_NOTE = "Auto-approved in Streamlit review after two-step confirmation."
EXPORT_DOWNLOADS = {
    "csv": ("CSV", "text/csv"),
    "xlsx": ("XLSX", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "json": ("JSON audit", "application/json"),
}
PROVIDER_OPTIONS = {
    "anthropic": "anthropic - real LLM extraction",
    "fake": "fake - deterministic local smoke test",
}
WORKFLOW_USAGE_SESSION_KEY = "workflow_usage_by_document"


def main() -> None:
    st.set_page_config(page_title="Revenue Segment Review", layout="wide")
    st.title("Revenue Segment Review Workbench")
    st.caption(
        "For analysts reviewing annual reports, segment revenue, "
        "source evidence, NACE classification, ESG factors, and export readiness."
    )

    repository = _repository()
    review_service = ReviewService(repository)
    reviewer = st.sidebar.text_input(
        "Reviewer",
        value="",
        placeholder=REVIEWER_PLACEHOLDER,
        help="Used in the review-event audit trail.",
    ) or "unknown_reviewer"

    selected_document_id = _document_selector(repository)
    uploaded_document_id = _upload_and_analyze(repository, reviewer)
    _render_queue_controls(repository, reviewer)
    document_id = uploaded_document_id or selected_document_id

    if not document_id:
        st.info("Upload an annual report or select an existing document to begin review.")
        return

    state = review_service.get_document_review_state(document_id)
    _render_pipeline(state)
    _render_summary(state)
    _render_workflow_usage(state.document.id)
    _render_review_tasks(state)
    _render_document_controls(repository, review_service, state, reviewer)
    segment_tab, esg_tab, scoring_tab = st.tabs(["Segment Review", "ESG Factors", "Scoring"])
    with segment_tab:
        _render_review_table(review_service, state, reviewer)
        _render_evidence(state)
        _render_validation_panel(review_service, state, reviewer)
        _render_manual_row_form(review_service, state, reviewer)
    with esg_tab:
        _render_esg_panel(repository, review_service, state, reviewer)
    with scoring_tab:
        _render_scoring_panel(repository, state)
    _render_export_controls(repository, state)


@st.cache_resource
def _repository() -> SQLiteRepository:
    connection = connect_database(DEFAULT_DATABASE_PATH, check_same_thread=False)
    initialize_database(connection)
    return SQLiteRepository(connection)


def _document_selector(repository: SQLiteRepository) -> str | None:
    documents = repository.list_documents()
    if not documents:
        return None
    options = {
        f"{_shorten(doc.company_name, 34)} | {_shorten(doc.document_name, 34)} | {doc.id}": doc.id
        for doc in documents
    }
    selected = st.sidebar.selectbox("Existing document", options=list(options), index=0)
    return options[selected]


def _provider_name_from_label(label: str) -> str:
    for provider_name, provider_label in PROVIDER_OPTIONS.items():
        if provider_label == label:
            return provider_name
    raise ValueError(f"Unknown extraction provider label: {label}")


def _strip_selection_column(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{key: value for key, value in row.items() if key != "selected"} for row in rows]


def _upload_and_analyze(repository: SQLiteRepository, reviewer: str) -> str | None:
    st.sidebar.divider()
    st.sidebar.subheader("New document")
    uploaded_file = st.sidebar.file_uploader("Upload annual report / 10-K PDF", type=["pdf"])
    company_name = st.sidebar.text_input(
        "Company name",
        placeholder="Example: Allianz SE",
        help="Leave blank to auto-detect only when the first pages clearly identify the company.",
    )
    fiscal_period = st.sidebar.text_input(
        "Fiscal period",
        placeholder="Example: FY2025",
        help="Optional. The parser can infer clear annual-report years.",
    )
    currency = st.sidebar.text_input(
        "Currency",
        placeholder="Example: USD, EUR, GBP",
        help="Optional. Used as a document-level hint and reviewed later per row.",
    )
    scale = st.sidebar.text_input(
        "Scale",
        placeholder="Example: millions",
        help="Optional. Examples: thousands, millions, billions.",
    )
    settings = ExtractionSettings.from_env()
    provider_keys = list(PROVIDER_OPTIONS)
    default_provider = (
        settings.provider_name if settings.provider_name in PROVIDER_OPTIONS else "anthropic"
    )
    provider_label = st.sidebar.selectbox(
        "Extraction provider",
        [PROVIDER_OPTIONS[key] for key in provider_keys],
        index=provider_keys.index(default_provider),
    )
    provider_name = _provider_name_from_label(provider_label)
    if not st.sidebar.button(
        "Queue extraction",
        disabled=uploaded_file is None,
        use_container_width=True,
    ):
        return None

    assert uploaded_file is not None
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = UPLOAD_DIR / uploaded_file.name
    pdf_path.write_bytes(uploaded_file.getbuffer())

    with st.status("Preparing document", expanded=True) as status:
        st.write("Parsing PDF text and tables")
        try:
            ingestion = PdfIngestionService(repository).ingest_pdf(
                pdf_path=pdf_path,
                company_name=company_name or None,
                fiscal_period=fiscal_period or None,
                currency=currency or None,
                scale=scale or None,
            )
        except ValueError as exc:
            status.update(label="Analysis stopped", state="error")
            st.error(str(exc))
            return None
        job = DocumentQueueService(repository).enqueue_document(
            document_id=ingestion.document.id,
            requested_by=reviewer,
            provider_name=provider_name,
            model=settings.model,
        )
        st.write(f"Queued extraction job `{job.id}`")
        status.update(label="Document queued", state="complete")
    return ingestion.document.id


def _render_queue_controls(repository: SQLiteRepository, reviewer: str) -> None:
    st.sidebar.divider()
    st.sidebar.subheader("Document queue")
    pending_jobs = repository.list_document_queue_jobs(statuses=(QUEUE_STATUS_PENDING,))
    running_jobs = repository.list_document_queue_jobs(statuses=(QUEUE_STATUS_RUNNING,))
    failed_jobs = repository.list_document_queue_jobs(statuses=(QUEUE_STATUS_FAILED,))
    st.sidebar.caption(
        f"Pending: {len(pending_jobs)} | Running: {len(running_jobs)} | Failed: {len(failed_jobs)}"
    )

    if st.sidebar.button("Process next queued document", use_container_width=True):
        with st.status("Processing queued document", expanded=True) as status:
            result = DocumentQueueService(repository).process_next(worker_id=reviewer)
            if result is None:
                status.update(label="No queued documents", state="complete")
                st.info("No pending queue jobs.")
                return
            if not result.succeeded:
                status.update(label="Queued extraction failed", state="error")
                st.error(result.error_message or "Queued extraction failed.")
                return
            assert result.analysis is not None
            _save_workflow_usage(result.job.document_id, result.analysis.tracker)
            for warning in result.analysis.warnings:
                st.warning(warning)
            status.update(label="Queued extraction complete", state="complete")
        st.rerun()

    latest_jobs = repository.list_document_queue_jobs(limit=5)
    if latest_jobs:
        with st.sidebar.expander("Latest jobs"):
            st.dataframe(
                [
                    {
                        "Job": job.id,
                        "Document": job.document_id,
                        "Status": job.status,
                        "Provider": job.provider_name,
                        "Model": job.model,
                    }
                    for job in latest_jobs
                ],
                hide_index=True,
                use_container_width=True,
            )


def _render_pipeline(state: DocumentReviewState) -> None:
    st.subheader("Workflow")
    steps = build_pipeline_steps(state)
    current_index = current_pipeline_step(steps)
    st.progress(pipeline_progress(steps), text=f"Current step: {steps[current_index]['stage']}")
    columns = st.columns(len(steps))
    for index, (column, step) in enumerate(zip(columns, steps, strict=True), start=1):
        column.markdown(
            f"**{index}. {step['stage']}**  \n"
            f"`{step['status'].replace('_', ' ')}`"
        )


def _render_summary(state: DocumentReviewState) -> None:
    st.subheader("Document")
    cards = build_summary_cards(state)
    top = st.columns(4)
    with top[0]:
        _summary_card("Company", str(cards["company"]))
    with top[1]:
        _summary_card("Document", str(cards["document"]))
    with top[2]:
        _summary_card("Period", str(cards["fiscal_period"] or "Needs review"))
    with top[3]:
        _summary_card("Pages", str(cards["pages"]))

    bottom = st.columns(5)
    bottom[0].metric("Rows", cards["rows"])
    bottom[1].metric("Pending", cards["pending_rows"])
    bottom[2].metric("Flagged", cards["flagged_rows"])
    bottom[3].metric("ESG", cards["esg_factors"])
    bottom[4].metric("Reconciliation", str(cards["reconciliation"]).replace("_", " "))


def _render_workflow_usage(document_id: str) -> None:
    usage = _workflow_usage_for_document(document_id)
    if usage is None:
        return

    st.subheader("Workflow Usage")
    st.caption("Pre-review runtime and estimated LLM usage for the latest analysis in this session.")
    columns = st.columns(4)
    columns[0].metric("Workflow time", _format_duration(float(usage["elapsed_seconds"])))
    columns[1].metric("LLM calls", int(usage["call_count"]))
    columns[2].metric("Tokens", f"{int(usage['total_tokens']):,}")
    columns[3].metric("Estimated cost", _format_cost(float(usage["estimated_cost_usd"])))

    with st.expander("LLM call details"):
        st.caption(
            "Costs use standard Anthropic family rates for Opus and Sonnet. "
            "Provider token usage is used when available; otherwise token counts are estimated from text length."
        )
        st.dataframe(
            _workflow_usage_rows(usage),
            hide_index=True,
            use_container_width=True,
        )


def _save_workflow_usage(document_id: str, tracker: WorkflowUsageTracker) -> None:
    st.session_state.setdefault(WORKFLOW_USAGE_SESSION_KEY, {})[document_id] = {
        "elapsed_seconds": tracker.elapsed_seconds,
        "call_count": tracker.call_count,
        "input_tokens": tracker.input_tokens,
        "output_tokens": tracker.output_tokens,
        "total_tokens": tracker.total_tokens,
        "estimated_cost_usd": tracker.estimated_cost_usd,
        "calls": [
            {
                "provider": call.provider_name,
                "model": call.model,
                "prompt_version": call.prompt_version,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "total_tokens": call.total_tokens,
                "estimated_cost_usd": call.estimated_cost_usd,
                "token_source": call.token_source,
                "status": call.status,
            }
            for call in tracker.calls
        ],
    }


def _workflow_usage_for_document(document_id: str) -> dict[str, object] | None:
    usage_by_document = st.session_state.get(WORKFLOW_USAGE_SESSION_KEY, {})
    usage = usage_by_document.get(document_id)
    return usage if isinstance(usage, dict) else None


def _workflow_usage_rows(usage: dict[str, object]) -> list[dict[str, object]]:
    rows = []
    calls = usage.get("calls", [])
    if not isinstance(calls, list):
        return rows
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, dict):
            continue
        rows.append(
            {
                "Call": index,
                "Status": call.get("status"),
                "Prompt": call.get("prompt_version"),
                "Model": call.get("model"),
                "Input tokens": call.get("input_tokens"),
                "Output tokens": call.get("output_tokens"),
                "Total tokens": call.get("total_tokens"),
                "Token source": call.get("token_source"),
                "Estimated cost": _format_cost(float(call.get("estimated_cost_usd", 0.0))),
            }
        )
    return rows


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remaining_seconds = divmod(int(round(seconds)), 60)
    return f"{minutes}m {remaining_seconds}s"


def _format_cost(cost: float) -> str:
    if cost <= 0:
        return "$0.0000"
    return f"${cost:.4f}"


def _summary_card(label: str, value: str) -> None:
    st.markdown(
        f"""
        <div style="padding:0.15rem 0 0.35rem 0;">
          <div style="font-size:0.88rem;font-weight:600;margin-bottom:0.35rem;">
            {escape(label)}
          </div>
          <div title="{escape(value)}" style="
            font-size:1.75rem;
            line-height:1.18;
            overflow-wrap:anywhere;
            word-break:break-word;
            white-space:normal;">
            {escape(value)}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_review_tasks(state: DocumentReviewState) -> None:
    st.subheader("Approval Checklist")
    st.caption("Click a task to jump to its review section before document approval.")
    tasks = build_review_tasks(state)
    columns = st.columns(len(tasks))
    for column, task in zip(columns, tasks, strict=True):
        percent = int(task["percent"])
        label = escape(str(task["label"]))
        detail = escape(str(task["detail"]))
        target = escape(str(task["target"]))
        column.markdown(
            f"""
            <a href="?focus={target}#{target}" style="text-decoration:none; color:inherit;">
              <div style="display:flex; gap:0.65rem; align-items:center; min-height:5.5rem;">
                <div style="
                  width:52px;
                  height:52px;
                  border-radius:50%;
                  background:conic-gradient(#256f5b {percent * 3.6}deg, #e8ecef 0deg);
                  display:flex;
                  align-items:center;
                  justify-content:center;
                  flex:0 0 auto;">
                  <div style="
                    width:38px;
                    height:38px;
                    border-radius:50%;
                    background:white;
                    display:flex;
                    align-items:center;
                    justify-content:center;
                    font-size:0.78rem;
                    font-weight:700;">{percent}%</div>
                </div>
                <div>
                  <div style="font-weight:700; line-height:1.2;">{label}</div>
                  <div style="font-size:0.78rem; color:#586069; line-height:1.25;">{detail}</div>
                </div>
              </div>
            </a>
            """,
            unsafe_allow_html=True,
        )


def _render_document_controls(
    repository: SQLiteRepository,
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
) -> None:
    st.subheader("Actions")
    col1, col2 = st.columns(2)
    if col1.button("Rerun extraction", help="Add this document to the processing queue."):
        settings = ExtractionSettings.from_env()
        job = DocumentQueueService(repository).enqueue_document(
            document_id=state.document.id,
            requested_by=reviewer,
            provider_name=settings.provider_name,
            model=settings.model,
        )
        st.success(f"Queued extraction job `{job.id}`.")
        st.rerun()

    if col1.button("Refresh NACE candidates"):
        settings = ExtractionSettings.from_env()
        tracker = WorkflowUsageTracker()
        provider = TrackedLLMProvider(create_provider(settings.provider_name), tracker)
        _run_nace_mapping(repository, state.document.id, provider, settings.model)
        tracker.stop()
        _save_workflow_usage(state.document.id, tracker)
        st.rerun()

    if col1.button("Refresh ESG factors"):
        settings = ExtractionSettings.from_env()
        tracker = WorkflowUsageTracker()
        provider = TrackedLLMProvider(create_provider(settings.provider_name), tracker)
        EsgExtractionService(repository, provider, settings).extract_document(state.document.id)
        tracker.stop()
        _save_workflow_usage(state.document.id, tracker)
        st.rerun()

    if state.approval_check.blockers:
        st.error("Document approval is blocked.")
        for blocker in state.approval_check.blockers:
            st.write(f"- {blocker}")
    elif state.approval_check.warnings:
        st.warning("Warnings remain, but they do not block approval.")

    if col2.button(
        "Approve document",
        disabled=not state.approval_check.can_approve,
        help="Available only after all blocking validation and row-review requirements are cleared.",
    ):
        review_service.approve_document(
            document_id=state.document.id,
            reviewer=reviewer,
            note="Document approved in Streamlit review.",
        )
        st.rerun()

    if col2.button(
        "Auto approve document",
        disabled=state.document.status == DOCUMENT_STATUS_APPROVED,
        help=(
            "Starts a two-step confirmation flow. It approves complete non-rejected rows, "
            "accepts top NACE candidates where available, marks validation issues, approves ESG "
            "factors, then attempts document approval."
        ),
    ):
        st.session_state[f"auto_approve_step_{state.document.id}"] = 1
    _render_auto_approve_confirmation(review_service, state, reviewer)


def _render_auto_approve_confirmation(
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
) -> None:
    step_key = f"auto_approve_step_{state.document.id}"
    step = int(st.session_state.get(step_key, 0) or 0)
    if step <= 0:
        return

    st.warning(
        "Auto approval will approve every complete non-rejected revenue row, accept the "
        "highest-ranked NACE candidate where no NACE selection exists, acknowledge or resolve "
        "validation issues with an audit note, and approve pending ESG factors."
    )
    col1, col2 = st.columns([1, 3])
    if step == 1:
        if col1.button("Continue", key=f"auto_approve_continue_{state.document.id}"):
            st.session_state[step_key] = 2
            st.rerun()
        if col2.button("Cancel auto approval", key=f"auto_approve_cancel_{state.document.id}"):
            st.session_state.pop(step_key, None)
            st.rerun()
        return

    st.error(
        "Final warning: this will write review audit events and approve the document if no "
        "blocking quality checks remain."
    )
    if col1.button("Auto approve now", key=f"auto_approve_final_{state.document.id}"):
        try:
            summary = _auto_approve_document(review_service, state, reviewer)
            st.success(
                "Auto approval completed: "
                f"{summary['rows']} row(s), {summary['nace']} NACE mapping(s), "
                f"{summary['validation']} validation issue(s), and "
                f"{summary['esg']} ESG factor(s) updated."
            )
            st.session_state.pop(step_key, None)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if col2.button("Cancel", key=f"auto_approve_final_cancel_{state.document.id}"):
        st.session_state.pop(step_key, None)
        st.rerun()


def _auto_approve_document(
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
) -> dict[str, int]:
    errors: list[str] = []
    summary = {"rows": 0, "nace": 0, "validation": 0, "esg": 0}

    for row in state.segment_rows:
        if row.status == SEGMENT_STATUS_REJECTED:
            continue
        if row.id not in state.nace_selection_by_segment:
            candidates = state.nace_candidates_by_segment.get(row.id, ())
            if candidates:
                candidate = min(candidates, key=lambda item: item.rank)
                review_service.accept_nace_candidate(
                    document_id=state.document.id,
                    segment_id=row.id,
                    candidate_id=candidate.id,
                    reviewer=reviewer,
                    note=AUTO_APPROVE_NOTE,
                )
                summary["nace"] += 1
        if row.status == SEGMENT_STATUS_APPROVED:
            continue
        try:
            review_service.approve_segment_row(
                document_id=state.document.id,
                segment_id=row.id,
                reviewer=reviewer,
                note=AUTO_APPROVE_NOTE,
            )
            summary["rows"] += 1
        except ValueError as exc:
            errors.append(f"{row.segment_name}: {exc}")

    for item in state.validation_issues:
        current_status = item.review.status if item.review else "open"
        if current_status in {VALIDATION_ISSUE_STATUS_ACKNOWLEDGED, VALIDATION_ISSUE_STATUS_RESOLVED}:
            continue
        target_status = (
            VALIDATION_ISSUE_STATUS_RESOLVED
            if item.issue.severity == "error"
            else VALIDATION_ISSUE_STATUS_ACKNOWLEDGED
        )
        try:
            review_service.mark_validation_issue(
                document_id=state.document.id,
                issue_id=item.issue.id,
                reviewer=reviewer,
                status=target_status,
                note=AUTO_APPROVE_NOTE,
            )
            summary["validation"] += 1
        except ValueError as exc:
            errors.append(f"{item.issue.issue_type}: {exc}")

    for factor in state.esg_factors:
        current_status = state.esg_status_by_factor.get(factor.id, "pending")
        if current_status in REVIEWED_ESG_STATUSES:
            continue
        review_service.approve_esg_factor(
            document_id=state.document.id,
            factor_id=factor.id,
            reviewer=reviewer,
            note=AUTO_APPROVE_NOTE,
        )
        summary["esg"] += 1

    if errors:
        raise ValueError("Auto approval stopped. Fix these items first:\n- " + "\n- ".join(errors))

    refreshed = review_service.get_document_review_state(state.document.id)
    if refreshed.approval_check.blockers:
        raise ValueError(
            "Auto approval could not approve the document. Remaining blockers:\n- "
            + "\n- ".join(refreshed.approval_check.blockers)
        )
    review_service.approve_document(
        document_id=state.document.id,
        reviewer=reviewer,
        note=AUTO_APPROVE_NOTE,
    )
    return summary


def _run_nace_mapping(
    repository: SQLiteRepository,
    document_id: str,
    provider: object | None,
    model: str | None = None,
) -> None:
    try:
        service = NaceMappingService(
            repository,
            provider=provider,
            model=model or ExtractionSettings.from_env().model,
        )
        service.map_document(document_id)
    except (FileNotFoundError, ValueError, LLMProviderError) as exc:
        st.warning(f"NACE mapping skipped: {exc}")


def _render_review_table(
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
) -> None:
    _section_header("segment-review", "Segment Review")
    original_rows = segment_table_rows(state)
    select_all = st.checkbox(
        "Select all rows for batch action",
        key=f"select_all_segments_{state.document.id}",
    )
    selectable_rows = [dict(row, selected=select_all) for row in original_rows]
    st.caption("Review revenue values, period, unit, page reference, and NACE mapping before export.")
    edited_rows = st.data_editor(
        selectable_rows,
        key=f"segments_{state.document.id}",
        hide_index=True,
        disabled=["id", "status", "nace_code", "nace_label"],
        width="stretch",
        column_order=[
            "selected",
            "segment_name",
            "revenue_raw",
            "normalized_value",
            "currency",
            "scale",
            "period_label",
            "page_ref",
            "status",
            "nace_code",
            "reviewer_note",
        ],
        column_config=_segment_column_config(),
    )
    selected_ids = [
        str(row["id"])
        for row in edited_rows
        if row.get("selected")
    ]
    batch_col1, batch_col2, batch_col3 = st.columns([1, 1, 3])
    if batch_col1.button("Approve selected", disabled=not selected_ids):
        _batch_approve_rows(review_service, state, reviewer, selected_ids)
    if batch_col2.button("Reject selected", disabled=not selected_ids):
        for row_id in selected_ids:
            review_service.reject_segment_row(
                document_id=state.document.id,
                segment_id=row_id,
                reviewer=reviewer,
                note="Batch rejected in Streamlit review.",
            )
        st.rerun()
    batch_col3.caption(f"{len(selected_ids)} row(s) selected")

    cleaned_edited_rows = _strip_selection_column(edited_rows)
    if st.button("Save row edits"):
        for row_id, changes, note in changed_segment_rows(original_rows, cleaned_edited_rows):
            if changes:
                review_service.update_segment_row(
                    document_id=state.document.id,
                    segment_id=row_id,
                    reviewer=reviewer,
                    changes=changes,
                    note=note,
                )
            elif note:
                review_service.add_reviewer_note(
                    document_id=state.document.id,
                    segment_id=row_id,
                    reviewer=reviewer,
                    note=note,
                )
        st.rerun()

    if not state.segment_rows:
        return

    selected_row = st.selectbox(
        "Row to approve or reject",
        options=state.segment_rows,
        format_func=lambda row: f"{row.segment_name} ({row.status})",
    )
    note = st.text_input(
        "Row note",
        key=f"row_note_{selected_row.id}",
        placeholder="Example: Value matches Note 4 segment table.",
    )
    col1, col2 = st.columns(2)
    if col1.button("Approve row"):
        try:
            review_service.approve_segment_row(
                document_id=state.document.id,
                segment_id=selected_row.id,
                reviewer=reviewer,
                note=note or None,
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if col2.button("Reject row"):
        review_service.reject_segment_row(
            document_id=state.document.id,
            segment_id=selected_row.id,
            reviewer=reviewer,
            note=note or None,
        )
        st.rerun()

    _render_nace_panel(review_service, state, selected_row.id, reviewer)


def _render_nace_panel(
    review_service: ReviewService,
    state: DocumentReviewState,
    segment_id: str,
    reviewer: str,
) -> None:
    _section_header("nace-review", "NACE Rev.2")
    selected = state.nace_selection_by_segment.get(segment_id)
    if selected:
        st.caption(
            f"Selected: {selected.nace_code} - {selected.nace_label} "
            f"(level {selected.nace_level}, {selected.source})"
        )

    candidate_rows = nace_candidate_table_rows(state, segment_id)
    if candidate_rows:
        select_all = st.checkbox(
            "Select all NACE candidates for batch action",
            key=f"select_all_nace_{segment_id}",
        )
        selectable_candidates = [dict(row, selected=select_all) for row in candidate_rows]
        edited_candidates = st.data_editor(
            selectable_candidates,
            key=f"nace_candidates_{segment_id}",
            hide_index=True,
            disabled=["candidate_id", "rank", "code", "label", "level", "match_score", "rationale"],
            width="stretch",
            column_order=[
                "selected",
                "rank",
                "code",
                "label",
                "level",
                "match_score",
                "rationale",
            ],
            column_config=_nace_column_config(),
        )
        selected_candidate_ids = [
            str(row["candidate_id"])
            for row in edited_candidates
            if row.get("selected")
        ]
        batch_col1, batch_col2 = st.columns([1, 4])
        if batch_col1.button(
            "Accept selected",
            disabled=not selected_candidate_ids,
            key=f"accept_selected_nace_{segment_id}",
        ):
            _batch_accept_nace_candidates(
                review_service,
                state,
                segment_id,
                reviewer,
                selected_candidate_ids,
            )
        batch_col2.caption(
            f"{len(selected_candidate_ids)} candidate(s) selected; accepting uses the highest-ranked selected candidate."
        )
        candidate_options = {
            f"{row['rank']}. {row['code']} - {row['label']}": row["candidate_id"]
            for row in candidate_rows
        }
        chosen_label = st.selectbox(
            "Accept candidate",
            options=list(candidate_options),
            key=f"nace_candidate_{segment_id}",
        )
        if st.button("Accept NACE candidate", key=f"accept_nace_{segment_id}"):
            review_service.accept_nace_candidate(
                document_id=state.document.id,
                segment_id=segment_id,
                candidate_id=candidate_options[chosen_label],
                reviewer=reviewer,
                note="Accepted NACE candidate in Streamlit review.",
            )
            st.rerun()
    else:
        st.write("No NACE candidates stored for this row.")

    with st.form(f"nace_override_{segment_id}", clear_on_submit=True):
        code = st.text_input("Override code", placeholder="Example: 65.12")
        label = st.text_input("Override label", placeholder="Example: Non-life insurance")
        level = st.number_input("Override level", min_value=1, max_value=4, value=4, step=1)
        rationale = st.text_area(
            "Override rationale",
            placeholder="Example: Segment activity is underwriting property and casualty policies.",
        )
        note = st.text_input("Override note", placeholder="Example: Reviewed against NACE Rev.2.")
        submitted = st.form_submit_button("Save NACE override")
    if submitted:
        try:
            review_service.override_segment_nace(
                document_id=state.document.id,
                segment_id=segment_id,
                reviewer=reviewer,
                nace_code=code,
                nace_label=label,
                nace_level=int(level),
                rationale=rationale or None,
                note=note or None,
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def _render_esg_panel(
    repository: SQLiteRepository,
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
) -> None:
    _section_header("esg-review", "ESG Review")
    if not state.esg_factors:
        st.info("No ESG factors stored for this document.")
        if st.button("Run ESG extraction", key=f"run_esg_empty_{state.document.id}"):
            settings = ExtractionSettings.from_env()
            provider = create_provider(settings.provider_name)
            EsgExtractionService(repository, provider, settings).extract_document(state.document.id)
            st.rerun()
        return

    linked_rows = esg_factor_table_rows(state, company_wide=False)
    company_rows = esg_factor_table_rows(state, company_wide=True)
    select_all_esg = st.checkbox(
        "Select all ESG factors for batch action",
        key=f"select_all_esg_{state.document.id}",
    )

    st.caption("Segment-linked ESG")
    selectable_linked_rows = [dict(row, selected=select_all_esg) for row in linked_rows]
    edited_linked = st.data_editor(
        selectable_linked_rows,
        key=f"linked_esg_{state.document.id}",
        hide_index=True,
        disabled=["id", "segment_id", "segment_name", "status"],
        width="stretch",
        column_order=[
            "selected",
            "segment_name",
            "factor_type",
            "polarity",
            "description",
            "page_ref",
            "confidence",
            "status",
        ],
        column_config=_esg_column_config(),
    )
    st.caption("Company-wide ESG")
    selectable_company_rows = [dict(row, selected=select_all_esg) for row in company_rows]
    edited_company = st.data_editor(
        selectable_company_rows,
        key=f"company_esg_{state.document.id}",
        hide_index=True,
        disabled=["id", "segment_id", "segment_name", "status"],
        width="stretch",
        column_order=[
            "selected",
            "factor_type",
            "polarity",
            "description",
            "page_ref",
            "confidence",
            "status",
        ],
        column_config=_esg_column_config(),
    )

    selected_esg_factor_ids = [
        str(row["id"])
        for row in edited_linked + edited_company
        if row.get("selected")
    ]
    batch_col1, batch_col2, batch_col3 = st.columns([1, 1, 3])
    if batch_col1.button("Approve selected ESG", disabled=not selected_esg_factor_ids):
        _batch_mark_esg_factors(
            review_service,
            state,
            reviewer,
            selected_esg_factor_ids,
            action="approve",
        )
    if batch_col2.button("Reject selected ESG", disabled=not selected_esg_factor_ids):
        _batch_mark_esg_factors(
            review_service,
            state,
            reviewer,
            selected_esg_factor_ids,
            action="reject",
        )
    batch_col3.caption(f"{len(selected_esg_factor_ids)} ESG factor(s) selected")

    if st.button("Save ESG edits"):
        cleaned_linked = _strip_selection_column(edited_linked)
        cleaned_company = _strip_selection_column(edited_company)
        for factor_id, changes in (
            changed_esg_factor_rows(linked_rows, cleaned_linked)
            + changed_esg_factor_rows(company_rows, cleaned_company)
        ):
            review_service.update_esg_factor(
                document_id=state.document.id,
                factor_id=factor_id,
                reviewer=reviewer,
                changes=changes,
                note="Edited ESG factor in Streamlit review.",
            )
        st.rerun()

    selected_factor = st.selectbox(
        "ESG factor to review",
        options=state.esg_factors,
        format_func=lambda factor: (
            f"{factor.factor_type} - "
            f"{state.esg_status_by_factor.get(factor.id, 'pending')}"
        ),
    )
    _render_esg_evidence_preview(repository, state, selected_factor)
    segment_options = {"Company-wide": None} | {
        row.segment_name: row.id for row in state.segment_rows
    }
    chosen_segment = st.selectbox(
        "Segment link target",
        options=list(segment_options),
        key=f"esg_relink_{selected_factor.id}",
    )
    note = st.text_input(
        "ESG note",
        key=f"esg_note_{selected_factor.id}",
        placeholder="Example: Evidence is company-wide, keep unlinked.",
    )
    col1, col2, col3, col4 = st.columns(4)
    if col1.button("Approve ESG factor", key=f"approve_esg_{selected_factor.id}"):
        review_service.approve_esg_factor(
            document_id=state.document.id,
            factor_id=selected_factor.id,
            reviewer=reviewer,
            note=note or None,
        )
        st.rerun()
    if col2.button("Reject ESG factor", key=f"reject_esg_{selected_factor.id}"):
        review_service.reject_esg_factor(
            document_id=state.document.id,
            factor_id=selected_factor.id,
            reviewer=reviewer,
            note=note or None,
        )
        st.rerun()
    if col3.button("Unlink", key=f"unlink_esg_{selected_factor.id}"):
        review_service.unlink_esg_factor(
            document_id=state.document.id,
            factor_id=selected_factor.id,
            reviewer=reviewer,
            note=note or None,
        )
        st.rerun()
    if col4.button("Relink", key=f"relink_esg_{selected_factor.id}"):
        target_segment_id = segment_options[chosen_segment]
        if target_segment_id is None:
            review_service.unlink_esg_factor(
                document_id=state.document.id,
                factor_id=selected_factor.id,
                reviewer=reviewer,
                note=note or None,
            )
        else:
            review_service.relink_esg_factor(
                document_id=state.document.id,
                factor_id=selected_factor.id,
                segment_id=target_segment_id,
                reviewer=reviewer,
                note=note or None,
            )
        st.rerun()


def _render_esg_evidence_preview(
    repository: SQLiteRepository,
    state: DocumentReviewState,
    factor: EsgFactor,
) -> None:
    st.text_area(
        "ESG evidence text",
        value=factor.evidence_text,
        height=120,
        disabled=True,
        key=f"esg_evidence_{factor.id}",
    )

    page_number = _page_number_from_ref(factor.page_ref)
    if page_number is None:
        st.caption("No page reference is available for this ESG factor.")
        return

    parsed_page = next(
        (page for page in repository.list_parsed_pages(state.document.id) if page.page_number == page_number),
        None,
    )
    if parsed_page is None:
        st.caption(f"Page {page_number} is not available in parsed document pages.")
        return

    bbox = _locate_esg_evidence_bbox(parsed_page, factor.evidence_text)
    if bbox is None:
        st.caption(f"Page {page_number} is referenced, but no bounding box match was found.")
        return

    output_path = (
        EVIDENCE_PREVIEW_DIR
        / state.document.id
        / f"esg-{factor.id}-page-{page_number}.png"
    )
    try:
        preview_path = render_page_with_bbox_to_png(
            state.document.source_path,
            page_number,
            output_path,
            bbox,
        )
    except (OSError, ValueError) as exc:
        st.caption(f"ESG page highlight unavailable: {exc}")
        return

    st.image(
        str(preview_path),
        caption=f"Page {page_number} with highlighted ESG evidence source.",
        use_container_width=True,
    )


def _locate_esg_evidence_bbox(page: ParsedPage, evidence_text: str) -> dict | None:
    if not evidence_text.strip():
        return None
    try:
        matches = locate_evidence_snippet(page, evidence_text, max_matches=1)
    except ValueError:
        return None
    if not matches:
        return None
    bbox = matches[0].get("bbox")
    return bbox if isinstance(bbox, dict) else None


def _render_scoring_panel(repository: SQLiteRepository, state: DocumentReviewState) -> None:
    _section_header("scoring", "Prototype Scoring")
    st.warning(
        "Prototype demo score only. This is not an official rating or sustainability score."
    )
    if state.pending_row_count:
        st.info("Scores include approved or edited segment rows only. Pending rows are excluded.")

    if st.button("Compute prototype scores", key=f"score_{state.document.id}"):
        try:
            result = ScoringService(repository).score_document(state.document.id)
        except (FileNotFoundError, ValueError) as exc:
            st.error(str(exc))
            return
        if result.company_score is None:
            st.warning("No company score could be calculated from reviewed rows with revenue.")
        else:
            st.success("Prototype scores updated.")
        st.rerun()

    company_scores = repository.list_company_scores(state.document.id)
    segment_scores = repository.list_document_segment_scores(state.document.id)
    latest_company_score = company_scores[-1] if company_scores else None
    score_by_segment = {score.segment_id: score for score in segment_scores}

    if latest_company_score:
        col1, col2, col3 = st.columns(3)
        col1.metric("Company prototype score", latest_company_score.weighted_average_score)
        col2.metric("Included revenue share", latest_company_score.included_weight_share)
        col3.metric("Included segments", latest_company_score.included_segment_count)
        st.caption(
            f"Scale: {latest_company_score.scale_min} to {latest_company_score.scale_max}; "
            f"{latest_company_score.score_direction.replace('_', ' ')}."
        )
    else:
        st.write("No prototype scores stored yet.")

    table_rows = _scoring_table_rows(state, score_by_segment)
    if table_rows:
        st.dataframe(
            table_rows,
            hide_index=True,
            width="stretch",
            column_config=_scoring_column_config(),
        )


def _scoring_table_rows(
    state: DocumentReviewState,
    score_by_segment: dict[str, SegmentScore],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for segment in state.segment_rows:
        score = score_by_segment.get(segment.id)
        if score is None:
            continue
        rationale = _score_rationale_text(getattr(score, "rationale", None))
        selection = state.nace_selection_by_segment.get(segment.id)
        rows.append(
            {
                "segment": segment.segment_name,
                "nace_code": selection.nace_code if selection else "",
                "revenue_share": score.weight_share,
                "base_score": score.base_score,
                "esg_adjustment": score.adjustment_score,
                "final_score": score.final_score,
                "rationale": rationale,
            }
        )
    return rows


def _score_rationale_text(rationale: str | None) -> str:
    if not rationale:
        return ""
    try:
        parsed = json.loads(rationale)
    except json.JSONDecodeError:
        return rationale
    base = str(parsed.get("base_score_rationale", ""))
    adjustments = parsed.get("esg_adjustments", [])
    adjustment_text = "; ".join(
        str(item.get("rationale", "")) for item in adjustments if item.get("rationale")
    )
    return " | ".join(part for part in (base, adjustment_text) if part)


def _render_evidence(state: DocumentReviewState) -> None:
    _section_header("evidence", "Evidence")
    if not state.segment_rows:
        st.write("No segment rows available.")
        return
    selected_segment_id = st.selectbox(
        "Segment evidence",
        options=[row.id for row in state.segment_rows],
        format_func=lambda row_id: next(
            row.segment_name for row in state.segment_rows if row.id == row_id
        ),
    )
    evidence_items = state.evidence_by_segment.get(selected_segment_id, ())
    if not evidence_items:
        st.write("No evidence stored for this row.")
        return
    for evidence in evidence_items:
        with st.expander(f"Page {evidence.page_number} - {evidence.parser_source}", expanded=True):
            if evidence.parser_source in {"ocr", "vision", "text_fallback"}:
                st.warning(
                    "This evidence came from OCR/vision fallback text. Review the source PDF before approval."
                )
            st.text_area(
                "Evidence text",
                value=evidence.snippet_text,
                height=120,
                disabled=True,
                key=f"evidence_{evidence.id}",
            )
            st.caption(f"Parser source: {evidence.parser_source}")
            _render_evidence_page_preview(state, evidence)


def _render_evidence_page_preview(
    state: DocumentReviewState,
    evidence: SegmentEvidence,
) -> None:
    if not evidence.bbox_json:
        st.caption("No stored bounding box is available for this evidence item.")
        return

    output_path = (
        EVIDENCE_PREVIEW_DIR
        / state.document.id
        / f"{evidence.id}-page-{evidence.page_number}.png"
    )
    try:
        preview_path = render_page_with_bbox_to_png(
            state.document.source_path,
            evidence.page_number,
            output_path,
            evidence.bbox_json,
        )
    except (OSError, ValueError) as exc:
        st.caption(f"Page highlight unavailable: {exc}")
        return

    st.image(
        str(preview_path),
        caption=f"Page {evidence.page_number} with highlighted evidence source.",
        use_container_width=True,
    )


def _render_validation_panel(
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
) -> None:
    _section_header("validation", "Validation")
    if not state.validation_issues:
        st.success("No validation issues.")
        return
    issue_rows = _validation_issue_table_rows(state)
    select_all = st.checkbox(
        "Select all validation issues for batch action",
        key=f"select_all_validation_{state.document.id}",
    )
    selectable_rows = [dict(row, selected=select_all) for row in issue_rows]
    edited_rows = st.data_editor(
        selectable_rows,
        key=f"validation_issues_{state.document.id}",
        hide_index=True,
        disabled=["issue_id", "severity", "issue_type", "review_status", "blocks_approval", "message"],
        width="stretch",
        column_order=[
            "selected",
            "severity",
            "issue_type",
            "review_status",
            "blocks_approval",
            "message",
        ],
        column_config=_validation_issue_column_config(),
    )
    selected_issue_ids = [
        str(row["issue_id"])
        for row in edited_rows
        if row.get("selected")
    ]
    batch_note = st.text_input(
        "Batch validation note",
        placeholder="Example: Reviewed source pages; issue accepted for prototype output.",
        key=f"batch_validation_note_{state.document.id}",
    )
    batch_col1, batch_col2, batch_col3 = st.columns([1, 1, 3])
    if batch_col1.button("Acknowledge selected", disabled=not selected_issue_ids):
        _batch_mark_validation_issues(
            review_service,
            state,
            reviewer,
            selected_issue_ids,
            VALIDATION_ISSUE_STATUS_ACKNOWLEDGED,
            batch_note or "Batch acknowledged in Streamlit review.",
        )
    if batch_col2.button("Resolve selected", disabled=not selected_issue_ids):
        _batch_mark_validation_issues(
            review_service,
            state,
            reviewer,
            selected_issue_ids,
            VALIDATION_ISSUE_STATUS_RESOLVED,
            batch_note or "Batch resolved in Streamlit review.",
        )
    batch_col3.caption(f"{len(selected_issue_ids)} validation issue(s) selected")

    for item in state.validation_issues:
        review_status = item.review.status if item.review else "open"
        with st.expander(
            f"{item.issue.severity.upper()} - {item.issue.issue_type} - {review_status}",
            expanded=item.blocks_approval,
        ):
            st.write(item.issue.message)
            st.caption(item.why_it_matters)
            st.write(f"Blocks approval: {item.blocks_approval}")
            note = st.text_input(
                "Issue note",
                key=f"issue_note_{item.issue.id}",
                placeholder="Example: Verified rounding bridge in Note 4.",
            )
            col1, col2 = st.columns(2)
            if col1.button("Acknowledge", key=f"ack_{item.issue.id}"):
                try:
                    review_service.mark_validation_issue(
                        document_id=state.document.id,
                        issue_id=item.issue.id,
                        reviewer=reviewer,
                        status=VALIDATION_ISSUE_STATUS_ACKNOWLEDGED,
                        note=note,
                    )
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if col2.button("Resolve", key=f"resolve_{item.issue.id}"):
                review_service.mark_validation_issue(
                    document_id=state.document.id,
                    issue_id=item.issue.id,
                    reviewer=reviewer,
                    status=VALIDATION_ISSUE_STATUS_RESOLVED,
                    note=note or None,
                )
                st.rerun()


def _validation_issue_table_rows(state: DocumentReviewState) -> list[dict[str, object]]:
    return [
        {
            "issue_id": item.issue.id,
            "severity": item.issue.severity,
            "issue_type": item.issue.issue_type,
            "review_status": item.review.status if item.review else "open",
            "blocks_approval": item.blocks_approval,
            "message": item.issue.message,
        }
        for item in state.validation_issues
    ]


def _render_manual_row_form(
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
) -> None:
    st.subheader("Add Missing Row")
    with st.form("manual_row_form", clear_on_submit=True):
        segment_name = st.text_input("Segment name", placeholder="Example: Commercial Banking")
        revenue_raw = st.text_input("Revenue raw", placeholder="Example: $120 million")
        normalized_value = st.text_input("Normalized value", placeholder="Example: 120000000")
        currency = st.text_input("Currency", value=state.document.currency or "", placeholder="Example: USD")
        scale = st.text_input("Scale", value=state.document.scale or "", placeholder="Example: millions")
        period = st.text_input("Period", value=state.document.fiscal_period or "", placeholder="Example: FY2025")
        page_ref = st.text_input("Page/section reference", placeholder="Example: p. 42, Note 4")
        evidence_text = st.text_area(
            "Evidence text",
            placeholder="Paste the source sentence or table row supporting this segment revenue.",
        )
        note = st.text_input("Reviewer note", placeholder="Example: Added missing segment from Note 4.")
        submitted = st.form_submit_button("Add row")
    if submitted:
        try:
            review_service.add_manual_segment_row(
                document_id=state.document.id,
                reviewer=reviewer,
                segment_name=segment_name,
                revenue_raw=revenue_raw or None,
                revenue_value=normalized_value or None,
                normalized_value=normalized_value or None,
                currency=currency or None,
                scale=scale or None,
                period_label=period or None,
                page_ref=page_ref or None,
                evidence_text=evidence_text or None,
                note=note or None,
            )
            st.rerun()
        except (ValueError, InvalidOperation) as exc:
            st.error(str(exc))


def _render_export_controls(repository: SQLiteRepository, state: DocumentReviewState) -> None:
    _section_header("export", "Export")
    export_ready = can_export(state)
    if state.document.status != DOCUMENT_STATUS_APPROVED:
        st.info("Export is disabled until the document is approved.")

    latest_exports = repository.list_export_records(state.document.id)
    latest_by_format = _latest_export_records_by_format(latest_exports)
    if latest_exports:
        csv_record = latest_by_format.get("csv")
        latest = csv_record or latest_exports[-1]
        st.caption(f"Latest export folder: {Path(latest.path).parent}")
        st.caption(f"Latest export timestamp: {latest.created_at.isoformat()}")
        _render_export_downloads(latest_by_format, disabled=not export_ready)
    else:
        st.caption("No export has been created for this document.")

    if st.button("Create export files", disabled=not export_ready):
        try:
            bundle = ExportService(repository).export_document(state.document.id)
            st.success(f"Exported to {bundle.output_dir}")
        except ValueError as exc:
            st.error(str(exc))
            return
        st.rerun()


def _render_export_downloads(
    records_by_format: dict[str, ExportRecord],
    *,
    disabled: bool,
) -> None:
    columns = st.columns(len(EXPORT_DOWNLOADS))
    for column, (file_format, (label, mime_type)) in zip(columns, EXPORT_DOWNLOADS.items()):
        record = records_by_format.get(file_format)
        if record is None:
            column.button(f"Download {label}", disabled=True)
            continue

        file_path = Path(record.path)
        if not file_path.is_file():
            column.warning(f"{label} file is missing.")
            continue

        column.download_button(
            f"Download {label}",
            data=file_path.read_bytes(),
            file_name=file_path.name,
            mime=mime_type,
            disabled=disabled,
        )


def _latest_export_records_by_format(records: list[ExportRecord]) -> dict[str, ExportRecord]:
    latest_by_format: dict[str, ExportRecord] = {}
    for record in records:
        file_format = record.format.casefold()
        existing = latest_by_format.get(file_format)
        if existing is None or record.created_at >= existing.created_at:
            latest_by_format[file_format] = record
    return latest_by_format


def _batch_approve_rows(
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
    selected_ids: list[str],
) -> None:
    errors: list[str] = []
    approved_count = 0
    row_names = {row.id: row.segment_name for row in state.segment_rows}
    for row_id in selected_ids:
        try:
            review_service.approve_segment_row(
                document_id=state.document.id,
                segment_id=row_id,
                reviewer=reviewer,
                note="Batch approved in Streamlit review.",
            )
            approved_count += 1
        except ValueError as exc:
            errors.append(f"{row_names.get(row_id, row_id)}: {exc}")

    if errors:
        if approved_count:
            st.warning(f"Approved {approved_count} row(s); {len(errors)} row(s) still need fixes.")
        for error in errors:
            st.error(error)
        return
    st.rerun()


def _batch_mark_validation_issues(
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
    selected_issue_ids: list[str],
    status: str,
    note: str,
) -> None:
    errors: list[str] = []
    updated_count = 0
    issue_labels = {
        item.issue.id: f"{item.issue.severity.upper()} - {item.issue.issue_type}"
        for item in state.validation_issues
    }
    for issue_id in selected_issue_ids:
        try:
            review_service.mark_validation_issue(
                document_id=state.document.id,
                issue_id=issue_id,
                reviewer=reviewer,
                status=status,
                note=note,
            )
            updated_count += 1
        except ValueError as exc:
            errors.append(f"{issue_labels.get(issue_id, issue_id)}: {exc}")

    if errors:
        if updated_count:
            st.warning(f"Updated {updated_count} issue(s); {len(errors)} issue(s) still need attention.")
        for error in errors:
            st.error(error)
        return
    st.rerun()


def _batch_accept_nace_candidates(
    review_service: ReviewService,
    state: DocumentReviewState,
    segment_id: str,
    reviewer: str,
    selected_candidate_ids: list[str],
) -> None:
    candidates = [
        candidate
        for candidate in state.nace_candidates_by_segment.get(segment_id, ())
        if candidate.id in set(selected_candidate_ids)
    ]
    if not candidates:
        return
    selected_candidate = min(candidates, key=lambda candidate: candidate.rank)
    review_service.accept_nace_candidate(
        document_id=state.document.id,
        segment_id=segment_id,
        candidate_id=selected_candidate.id,
        reviewer=reviewer,
        note="Batch accepted NACE candidate in Streamlit review.",
    )
    st.rerun()


def _batch_mark_esg_factors(
    review_service: ReviewService,
    state: DocumentReviewState,
    reviewer: str,
    selected_factor_ids: list[str],
    *,
    action: str,
) -> None:
    factor_labels = {
        factor.id: f"{factor.factor_type} ({factor.page_ref or 'no page'})"
        for factor in state.esg_factors
    }
    errors: list[str] = []
    updated_count = 0
    for factor_id in selected_factor_ids:
        try:
            if action == "approve":
                review_service.approve_esg_factor(
                    document_id=state.document.id,
                    factor_id=factor_id,
                    reviewer=reviewer,
                    note="Batch approved ESG factor in Streamlit review.",
                )
            elif action == "reject":
                review_service.reject_esg_factor(
                    document_id=state.document.id,
                    factor_id=factor_id,
                    reviewer=reviewer,
                    note="Batch rejected ESG factor in Streamlit review.",
                )
            else:
                raise ValueError(f"Unsupported ESG batch action: {action}")
            updated_count += 1
        except (KeyError, ValueError) as exc:
            errors.append(f"{factor_labels.get(factor_id, factor_id)}: {exc}")

    if errors:
        if updated_count:
            st.warning(f"Updated {updated_count} ESG factor(s); {len(errors)} still need attention.")
        for error in errors:
            st.error(error)
        return
    st.rerun()


def _segment_column_config() -> dict[str, object]:
    return {
        "selected": st.column_config.CheckboxColumn("Select", width="small"),
        "segment_name": st.column_config.TextColumn("Segment", width="medium"),
        "revenue_raw": st.column_config.TextColumn("Raw value", width="small"),
        "normalized_value": st.column_config.TextColumn("Revenue value", width="small"),
        "currency": st.column_config.TextColumn("Currency", width="small"),
        "scale": st.column_config.TextColumn("Scale", width="small"),
        "period_label": st.column_config.TextColumn("Period", width="small"),
        "page_ref": st.column_config.TextColumn("Page", width="small"),
        "status": st.column_config.TextColumn("Review", width="small"),
        "nace_code": st.column_config.TextColumn("NACE", width="small"),
        "reviewer_note": st.column_config.TextColumn("Reviewer note", width="medium"),
    }


def _validation_issue_column_config() -> dict[str, object]:
    return {
        "selected": st.column_config.CheckboxColumn("Select", width="small"),
        "severity": st.column_config.TextColumn("Severity", width="small"),
        "issue_type": st.column_config.TextColumn("Issue type", width="medium"),
        "review_status": st.column_config.TextColumn("Review", width="small"),
        "blocks_approval": st.column_config.CheckboxColumn("Blocks", width="small"),
        "message": st.column_config.TextColumn("Message", width="large"),
    }


def _nace_column_config() -> dict[str, object]:
    return {
        "selected": st.column_config.CheckboxColumn("Select", width="small"),
        "rank": st.column_config.NumberColumn("Rank", width="small"),
        "code": st.column_config.TextColumn("Code", width="small"),
        "label": st.column_config.TextColumn("NACE label", width="medium"),
        "level": st.column_config.NumberColumn("Level", width="small"),
        "match_score": st.column_config.NumberColumn("Score", width="small", format="%.2f"),
        "rationale": st.column_config.TextColumn("Rationale", width="large"),
    }


def _esg_column_config() -> dict[str, object]:
    return {
        "selected": st.column_config.CheckboxColumn("Select", width="small"),
        "segment_name": st.column_config.TextColumn("Segment", width="medium"),
        "factor_type": st.column_config.TextColumn("Factor type", width="medium"),
        "polarity": st.column_config.TextColumn("Polarity", width="small"),
        "description": st.column_config.TextColumn("Description", width="large"),
        "page_ref": st.column_config.TextColumn("Page", width="small"),
        "evidence_text": st.column_config.TextColumn("Evidence", width="large"),
        "confidence": st.column_config.NumberColumn("Confidence", width="small", format="%.2f"),
        "status": st.column_config.TextColumn("Review", width="small"),
    }


def _scoring_column_config() -> dict[str, object]:
    return {
        "segment": st.column_config.TextColumn("Segment", width="medium"),
        "nace_code": st.column_config.TextColumn("NACE", width="small"),
        "revenue_share": st.column_config.NumberColumn("Revenue share", width="small", format="%.3f"),
        "base_score": st.column_config.NumberColumn("Base", width="small", format="%.2f"),
        "esg_adjustment": st.column_config.NumberColumn("ESG adjustment", width="small", format="%.2f"),
        "final_score": st.column_config.NumberColumn("Final", width="small", format="%.2f"),
        "rationale": st.column_config.TextColumn("Rationale", width="large"),
    }


def _section_header(section_id: str, title: str) -> None:
    escaped_id = escape(section_id)
    escaped_title = escape(title)
    if _focused_section() == section_id:
        st.markdown(
            f"""
            <div id="{escaped_id}" style="
              padding:0.55rem 0.75rem;
              border-left:4px solid #256f5b;
              background:#eef7f3;
              margin:0.5rem 0;">
              <h3 style="margin:0;">{escaped_title}</h3>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(f'<span id="{escaped_id}"></span>', unsafe_allow_html=True)
        st.subheader(title)


def _focused_section() -> str | None:
    value = st.query_params.get("focus")
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value else None


def _shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _page_number_from_ref(page_ref: str | None) -> int | None:
    if not page_ref:
        return None
    match = re.search(r"\d+", page_ref)
    return int(match.group(0)) if match else None


if __name__ == "__main__":
    main()
