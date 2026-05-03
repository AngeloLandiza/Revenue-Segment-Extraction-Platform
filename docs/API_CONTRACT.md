# API Contract

## Current Contract

No HTTP API routes are implemented in the workspace yet. There are still no route paths, request bodies, HTTP status conventions, or error response shapes to preserve.

This change adds API-facing Pydantic response schemas only. They are internal Python schema classes intended to be reused by future FastAPI or Streamlit integration without exposing persistence dataclasses directly.

PDF ingestion and first-pass extraction are currently exposed as local CLI commands, not HTTP endpoints:

```bash
python scripts/ingest_pdf.py /path/to/annual-report.pdf --company-name "Example Corp"
python scripts/extract_revenue_segments.py doc_... --provider fake
```

ESG extraction is currently exposed through the Python service layer and Streamlit controls, not an HTTP route:

```python
from fitch_extractor.extraction import EsgExtractionService, create_provider

summary = EsgExtractionService(repository, create_provider("fake")).extract_document(document_id)
```

Prototype scoring is currently exposed through the Python service layer, Streamlit scoring tab, and export service, not an HTTP route:

```python
from fitch_extractor.scoring import ScoringService

result = ScoringService(repository).score_document(document_id)
```

## Current API-Facing Schemas

Implemented in `fitch_extractor/api/schemas.py`:

- `DocumentResponse`
- `ParsedPageResponse`
- `PageCandidateResponse`
- `IngestionSummaryResponse`
- `ExtractionSummaryResponse`
- `SegmentRowResponse`
- `SegmentEvidenceResponse`
- `ValidationIssueResponse`
- `DocumentReviewStateResponse`
- `NaceCandidateResponse`
- `NaceSelectionResponse`
- `EsgFactorResponse`
- `SegmentScoreResponse`
- `CompanyScoreResponse`
- `ReviewEventResponse`
- `ExportRecordResponse`
- `DocumentExportResponse`

These schemas mirror the implemented internal entities and can serialize from dataclass instances via Pydantic `from_attributes`.

## Backwards Compatibility

No prior API routes or response fields existed in this workspace, so no existing API behavior was changed.

Future HTTP routes must document their response shapes here in the same change that introduces them. Once routes exist, tests should assert that documented response fields remain available unless a breaking change is explicitly recorded in `docs/ARCHITECTURE_DECISIONS.md`.

## Target Route Inventory

The following route inventory remains a planning target, not an implemented HTTP contract:

| Area | Target behavior |
| --- | --- |
| Document upload/import | Accept a PDF and create a `Document` record. |
| Parsing status | Return parsed pages and candidate extraction sections. |
| Extraction run | Run deterministic retrieval plus strict-schema LLM extraction on selected candidates. |
| Validation | Return validation issues for extracted segment rows. |
| Review | Record approve, edit, and reject decisions. |
| NACE mapping | Return ranked NACE candidates for review. |
| ESG factors | Return extracted ESG factor candidates with evidence. |
| Scoring | Return prototype segment scores with inputs and explanations. |
| Export | Write CSV, XLSX, and JSON audit files only after review gate passes. |

## Scoring Service Contract

There is no HTTP scoring route yet. The backend-facing service entrypoint is:

```python
ScoringService(repository).score_document(document_id)
```

The service:

- reads `config/scoring_rules.yaml`
- scores only `approved` or `edited` non-total segment rows
- excludes rejected and unreviewed rows
- uses reviewer-selected NACE mapping, then top NACE candidate, then configured fallback
- includes only approved or edited segment-linked ESG factors
- persists `SegmentScore` rows and a `CompanyScore` summary
- labels output as prototype/demo only

Future HTTP responses should expose `SegmentScoreResponse` and `CompanyScoreResponse` without weakening the existing review/export gate.

## NACE Mapping Service Contract

There is no HTTP NACE route yet. The backend-facing service entrypoint is:

```python
NaceMappingService(repository, provider=provider).map_document(document_id)
```

The service runs broad deterministic candidate retrieval, then LLM classification with prompt version `nace_mapping_v2` when a provider is configured. The LLM response is strict JSON and can return:

- `mapped`
- `not_applicable`
- `needs_review`

The LLM may only select codes from the provided candidate list. Invented codes raise `ValueError`.

Automatic selections are stored only for high-confidence `mapped` decisions. Total, elimination, reconciliation, reported-only, and roll-up rows are left without a selected NACE code. Reviewer accept/override selections are preserved when mapping reruns.

## Ingestion CLI Summary Shape

`scripts/ingest_pdf.py` prints JSON with this shape:

```json
{
  "candidate_count": 3,
  "candidate_pages": [
    {
      "document_id": "doc_...",
      "id": "candidate_...",
      "matched_signals_json": {
        "terms": [{"term": "operating segments", "weight": 8.5}]
      },
      "page_number": 42,
      "reason": "matched terms: operating segments; 1 detected table(s).",
      "relevance_score": 17.25
    }
  ],
  "document": {
    "company_name": "Example Corp",
    "document_name": "annual-report.pdf",
    "source_path": "/absolute/path/to/annual-report.pdf",
    "status": "new"
  },
  "no_text_pages": [7],
  "page_count": 120,
  "parsed_page_count": 120
}
```

The document object includes the complete `DocumentResponse` field set. Candidate objects include the complete `PageCandidateResponse` field set.

## Extraction CLI Summary Shape

`scripts/extract_revenue_segments.py` runs first-pass extraction for an already ingested document and prints JSON with this shape:

```json
{
  "bundle_count": 1,
  "candidate_page_count": 2,
  "document": {
    "company_name": "Example Corp",
    "document_name": "annual-report.pdf",
    "id": "doc_..."
  },
  "extracted_row_count": 2,
  "model": "claude-sonnet-4-6,
  "persisted_row_count": 2,
  "prompt_version": "first_pass_revenue_segments_v1",
  "provider_name": "fake",
  "segment_rows": [
    {
      "segment_name": "Commercial",
      "revenue_raw": "$120",
      "revenue_value": "120",
      "currency": "USD",
      "scale": "millions",
      "period_label": "FY2025",
      "normalized_value": "120000000",
      "page_ref": "p. 2",
      "section_ref": "Note 4 - Operating Segments",
      "metric_basis": "External revenue",
      "status": "ready_for_review"
    }
  ],
  "validation_issue_count": 0,
  "validation_issues": []
}
```

The document object includes the complete `DocumentResponse` field set. Segment row objects include the complete `SegmentRowResponse` field set. Validation issue objects include the complete `ValidationIssueResponse` field set.

When a provider returns multiple years for accepted segment rows, the extraction service persists only the latest detected year. Prior-year skips appear in `validation_issues` with `issue_type = "prior_period_row_skipped"`.

When the service suppresses duplicate secondary segment tables, skips appear in `validation_issues` with `issue_type = "secondary_segment_table_skipped"`. Metric alignment from external revenue/income to total revenue/income happens before persistence and does not change the response shape.

When a provider returns a generic consolidated line item as a segment name, such as `Revenue`, the row is skipped as `non_segment_revenue_disclosure` instead of being persisted as a first-pass segment row.

If initial deterministic candidate windows produce no accepted rows, extraction may run an internal `candidate_page_discovery_v1` provider step. This does not change the CLI/API summary shape; discovered pages are reflected only through candidate counts, bundle counts, validation issues, and any rows that pass the normal extraction pipeline.

## Response Principles

- Return stable JSON objects with explicit fields.
- Keep API schemas separate from persistence dataclasses.
- Include identifiers for documents, pages, rows, evidence, and review events.
- Include page/section references wherever evidence is shown.
- Include validation issues instead of silently accepting invalid extraction output.
- Mark clean first-pass extraction rows as `ready_for_review` and uncertain rows as `needs_review`.
- Never expose API keys, raw provider errors containing secrets, or local absolute paths in user-facing responses.
- Block final export while any segment row remains `pending`, `ready_for_review`, or `needs_review`, and until the document itself is approved.

## Export Service Contract

There is still no HTTP framework in this repository. The backend-facing export entrypoint is:

```python
from fitch_extractor.api.exports import export_reviewed_document

response = export_reviewed_document(repository, document_id)
```

Response schema: `DocumentExportResponse`

Fields:

- `document_id`
- `output_dir`
- `csv_path`
- `json_path`
- `xlsx_path`
- `exported_at`
- `records`

The handler delegates to `ExportService.export_document(document_id)`, which writes:

- `exports/{document_id}/revenue_segments.csv`
- `exports/{document_id}/revenue_segments.xlsx`
- `exports/{document_id}/audit_export.json`

Export is rejected with `ValueError` unless the document status is `approved` and every segment row is `approved`, `edited`, or `rejected`. Rejected rows are excluded from the CSV and XLSX files and retained in the JSON audit export.

## Review Service Contract

There is still no HTTP route layer. The API-facing contract is the service plus Pydantic response schemas that future routes or the Streamlit UI can reuse.

### Fetch document review state

Service method: `ReviewService.get_document_review_state(document_id)`

Response schema: `DocumentReviewStateResponse`

Includes:

- `document`
- `page_count`
- `segment_rows`
- `evidence_by_segment`
- `nace_candidates_by_segment`
- `nace_selection_by_segment`
- `esg_factors`
- `esg_status_by_factor`
- `validation_issues`
- `review_events`
- `approval_check`

### Segment row actions

Service methods:

- `update_segment_row(document_id, segment_id, reviewer, changes, note=None)`
- `approve_segment_row(document_id, segment_id, reviewer, note=None)`
- `reject_segment_row(document_id, segment_id, reviewer, note=None)`
- `add_manual_segment_row(document_id, reviewer, ..., evidence_text=None, note=None)`
- `add_reviewer_note(document_id, reviewer, note, segment_id=None)`
- `accept_nace_candidate(document_id, segment_id, candidate_id, reviewer, note=None)`
- `override_segment_nace(document_id, segment_id, reviewer, nace_code, nace_label, nace_level, match_score=None, rationale=None, note=None)`

Every action writes a `ReviewEventResponse`-compatible record with reviewer, action, old value, new value, note, and timestamp. Row edits store corrected values on `segment_rows`; the previous values remain auditable through review events and original evidence rows are not overwritten.

NACE candidate acceptance stores `source = reviewer_accept` in `segment_nace_selections`. Manual NACE override stores `source = reviewer_override` and logs a review event with `action = override_nace_code`.

### Validation issue actions

Service method: `mark_validation_issue(document_id, issue_id, reviewer, status, note=None)`

Allowed statuses:

- `open`
- `acknowledged`
- `resolved`

Validation issues remain immutable. Reviewer state is stored in `validation_issue_reviews` and exposed as `ValidationIssueReviewResponse` inside each `ReviewedValidationIssueResponse`.

### ESG factor actions

Service methods:

- `update_esg_factor(document_id, factor_id, reviewer, changes, note=None)`
- `unlink_esg_factor(document_id, factor_id, reviewer, note=None)`
- `relink_esg_factor(document_id, factor_id, segment_id, reviewer, note=None)`
- `approve_esg_factor(document_id, factor_id, reviewer, note=None)`
- `reject_esg_factor(document_id, factor_id, reviewer, note=None)`

Every ESG action writes a `ReviewEventResponse`-compatible record. ESG factor review status is event-derived through `field_changed = esg_factor:{factor_id}:status`, so future HTTP routes should return both `EsgFactorResponse` rows and `esg_status_by_factor` from `DocumentReviewStateResponse`.

Allowed editable ESG fields are:

- `segment_id`
- `factor_type`
- `polarity`
- `description`
- `page_ref`
- `evidence_text`
- `confidence`
- `is_company_wide`

Relinking requires the target segment to belong to the same document. Unlinking sets `segment_id = null` and `is_company_wide = true`.

### Document approval

Service methods:

- `check_document_approval(document_id)`
- `approve_document(document_id, reviewer, note=None)`

`DocumentApprovalCheckResponse.can_approve` is false when:

- any row is not `approved`, `edited`, or `rejected`
- any non-rejected row is missing segment name, normalized revenue value, currency, scale, period, page/section reference, or evidence text
- any `error` validation issue is not resolved
- any `total_reconciliation_mismatch` issue is not resolved or acknowledged with a reviewer note
- company or document name is missing
- no segment rows exist
