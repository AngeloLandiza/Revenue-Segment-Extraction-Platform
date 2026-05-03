# Review Workflow

The review workflow is the final quality gate for revenue-segment output. Extraction may create `ready_for_review` or `needs_review` rows, but export remains blocked until a reviewer addresses every row and approves the whole document.

## Analyst Flow

1. Upload a PDF or select an existing document in `streamlit_app.py`.
2. Start analysis to run parsing, candidate page selection, extraction, validation, verification/arbitration, and review setup.
3. Inspect summary cards for company, document, fiscal period, pages, row count, pending rows, flagged rows, and reconciliation status.
4. Review the editable segment table.
5. Inspect evidence for selected rows, including page number, evidence text, parser source, and a highlighted page preview when bounding-box coordinates are available.
6. Inspect ESG factor evidence in the ESG tab; the selected factor shows evidence text and a highlighted page preview when the evidence can be matched to parsed page text.
7. Edit incorrect row fields, reject false positives, approve valid rows, or add missing rows manually.
8. Review validation issues and mark them `acknowledged` or `resolved` when appropriate.
9. Approve the document only after all blocking conditions are cleared.
10. Export controls remain disabled until document approval succeeds.

## Review Actions

`ReviewService` owns review business logic:

- `get_document_review_state`
- `update_segment_row`
- `approve_segment_row`
- `reject_segment_row`
- `add_manual_segment_row`
- `add_reviewer_note`
- `mark_validation_issue`
- `check_document_approval`
- `approve_document`

Each action creates a `ReviewEvent` with reviewer, action, old value, new value, note, and timestamp.

## Auditability

- Original `SegmentEvidence` rows are never overwritten during review.
- Extracted row values may be corrected on `SegmentRow`, but old values are captured in `ReviewEvent`.
- Validation issues are immutable. Reviewer state is stored separately in `validation_issue_reviews`.
- Reviewer notes are event records; document-level notes are appended to `Document.analysis_notes`.

## Approval Blocks

Document approval is blocked when:

- any segment row is not `approved`, `edited`, or `rejected`
- any non-rejected row is missing segment name, normalized revenue value, currency, scale, period, page/section reference, or evidence text
- any `error` validation issue is not `resolved`
- any `total_reconciliation_mismatch` issue is not `resolved` or `acknowledged` with a reviewer note
- company name or document name is missing
- the document has no segment rows

Warnings that are not reconciliation mismatches remain visible but do not block approval unless future requirements classify them as blocking.

## Streamlit UI

Run:

```bash
streamlit run streamlit_app.py
```

The UI intentionally keeps business rules in `ReviewService` and testable display helpers in `revenue_segment_extractor/ui/review.py`. Streamlit is responsible for rendering controls and forwarding user actions.

The segment evidence panel renders the source PDF page with the stored evidence bounding box highlighted when coordinates are available. The ESG review panel uses the selected factor's page reference and evidence text to locate a matching parsed text block on demand, then renders the same highlighted page preview. If an evidence item has no stored bounding box, no parsed ESG match, or the source PDF cannot be rendered, the page number and evidence text remain visible for review.
