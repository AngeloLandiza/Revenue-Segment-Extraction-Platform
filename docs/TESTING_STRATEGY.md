# Testing Strategy

## Current Test State

The repository uses `unittest` for dependency-light local tests.

Current test command after installing requirements in a virtual environment:

```bash
python -m unittest discover -s tests
```

Current coverage added for the data and persistence layer:

- Required docs scaffold exists.
- Internal dataclasses serialize datetimes and decimals predictably.
- Pydantic response schemas serialize from internal dataclasses.
- Document create/read/update flows.
- Segment row create/read/update flows.
- Related persistence for parsed pages, page candidates, evidence, validation issues, NACE candidates, ESG factors, and scores.
- Review event logging and segment status updates.
- Export metadata creation blocked while rows remain unreviewed.
- SQLite database file initialize/reset helpers.
- PyMuPDF text block, bounding-box, no-text page, and PNG rendering support.
- Low-text page detection and optional OCR/vision fallback provider behavior.
- Fallback text persistence with parser source metadata such as `ocr` and `vision`.
- pdfplumber table extraction path.
- Deterministic candidate scoring and adjacent page inclusion.
- Curated multilingual revenue/segment/total keyword matching.
- Approximate evidence snippet lookup.
- Persistence of ingestion-created parsed pages and page candidates.
- First-pass prompt construction requirements.
- First-pass prompt language guidance for non-English pages and original segment-label preservation.
- Strict extraction schema validation for valid, missing-field, and extra-field outputs.
- Fake provider extraction from prompt table rows.
- Invalid JSON handling as a persisted validation issue.
- Markdown-wrapped provider JSON is extracted before strict schema validation.
- Non-JSON provider responses remain validation issues and do not persist rows.
- Persistence of extracted review-ready rows and evidence.
- Conservative deduplication that preserves rows with different evidence.
- Integration path from fixture PDF parsing to candidate ranking to fake LLM extraction to stored rows.
- Integration path from low-text PDF parsing through fake OCR fallback to fake LLM extraction and stored fallback evidence.
- Extraction candidate filtering that excludes weak non-segment pages and prefers financial statement segment notes.
- Provider row rejection when a row references a page outside the prompt bundle.
- Primary segmentation extraction coverage for all current-period table columns, including dash/null reconciliation columns.
- TOC/index and accounting-update pages are excluded from extraction prompts.
- Segment-note intro pages include the following page when needed for the revenue table.
- No eligible extraction candidates with parsed pages produces a warning, not an error.
- Structural numeric-table candidates are eligible without adding hard-coded multilingual term lists.
- Prior-period rows are filtered before persistence when a later year is also extracted.
- Repeated 10-K `Table of Contents` running headers are not treated as true TOC pages when the page contains segment revenue evidence.
- External revenue/income rows are aligned to preferred total revenue/income rows when the same parsed page clearly contains them.
- Secondary duplicate segment tables are skipped after selecting the strongest primary segment table.
- Generic consolidated revenue line items are rejected when returned as segment names.
- LLM candidate discovery can recover a missed page after deterministic candidates produce no accepted rows, without requiring live provider calls in tests.
- Currency, scale, numeric, dash-only, decimal, negative, period, and page-reference normalization edge cases.
- Deterministic validation for evidence completeness, currency/scale/period mismatches, invalid metric bases, duplicate segments, total reconciliation, declared segment coverage, consolidated income statement false positives, and geography/product revenue segmentation.
- Fake second-pass verification with strict schema output.
- Arbitration trigger logic so the optional arbitration pass is skipped for clean documents and used only for validation/verification failures.
- Integration path from first-pass extraction to normalization, validation, verification, stored issues, confidence, and `needs_review` status.
- NACE Rev.2 CSV reference loading with instruction rows before the header.
- Deterministic NACE candidate retrieval using normalized text, fuzzy matching, and keyword overlap.
- LLM mapping through the fake provider and rejection of invented NACE codes.
- Persistence of up to five NACE candidates per segment.
- Reviewer NACE override storage and review-event audit logging.
- CSV and JSON export population for selected NACE fields.
- ESG keyword retrieval and generic boilerplate filtering.
- Strict ESG schema validation.
- Fake-provider ESG extraction without live LLM calls.
- Deterministic ESG segment-link rules and company-wide fallback.
- ESG review event logging for edit/link/approval actions.
- Export of approved segment-linked ESG summaries and full JSON ESG audit records.
- Evaluation gold/prediction row matching with normalized segment names, values, periods, and page references.
- Evaluation metrics for precision, recall, F1, value accuracy, page accuracy, reconciliation pass rate, reviewer edit rate, validation issue averages, and elapsed time.
- Evaluation failure taxonomy classification for missing, extra, duplicate, wrong value, wrong unit/scale, wrong period, total handling, OCR, non-English, NACE ambiguity, ESG over-linking, and table parsing issues.
- Integration coverage for loading fake gold/prediction files and writing all evaluation reports.

## Test Fixtures

Fixture builders are available in `tests/fixtures.py`:

- `build_document()`
- `build_segment_row()`

They use deterministic IDs, timestamps, revenue values, and review status defaults.

## Dependency Setup

Pydantic is required for API-facing schemas. Because the system Python may be externally managed, use a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests
```

## Target Test Layers

### Contract tests

Continue validating all target data entities and required export columns:

- `Document`
- `ParsedPage`
- `PageCandidate`
- `SegmentRow`
- `SegmentEvidence`
- `ValidationIssue`
- `NaceCandidate`
- `NaceSelection`
- `EsgFactor`
- `SegmentScore`
- `ReviewEvent`
- `ExportRecord`

### Parser and retrieval tests

Use deterministic fixtures to verify:

- PDF pages are parsed into stable page records.
- Page dimensions, text-block bounding boxes, and parser sources are preserved.
- pdfplumber table rows and cell structures are captured where available.
- Weak/no-text pages are marked with `has_text = false`.
- Optional fallback runs only for low-text pages and leaves normal pages on PyMuPDF/pdfplumber output.
- Fallback source metadata is persisted in `parser_sources`, `blocks_json.text_fallback`, and evidence when matched.
- Candidate revenue pages are found before LLM calls.
- Common non-English revenue, sales, turnover, segment, and total phrases are matched without machine translation.
- Adjacent pages are included when they have their own deterministic signal.
- Evidence lookup returns page-level bbox references for snippets.
- Non-revenue pages are not ranked unless a relevant deterministic signal or proximity rule applies.
- Evidence windows do not drop table labels, periods, currencies, or units.

### LLM extraction tests

Use fake or mocked provider responses to verify:

- Prompts require strict JSON/schema output.
- Prompts tell the model to extract regardless of report language and preserve original official segment labels.
- Malformed model output creates validation issues.
- Extraction runs only on deterministic candidate windows.
- Model output is normalized and validated in Python.
- No test calls a real Anthropic API.
- Non-segment ESG/taxonomy/energy-use disclosures are not persisted as segment rows.
- Multi-year tables persist only the latest detected year when row period labels expose multiple years.
- Provider output that chooses customer-only/external revenue is corrected when deterministic page text exposes a better total segment metric on the same table.
- Candidate discovery returns only page numbers; extraction, validation, and persistence are still tested through the standard first-pass path.
- Second-pass verifier output is strict-schema validated and fakeable.
- Arbitration trigger tests prove clean documents do not call arbitration and uncertain documents use the configured arbitration model.
- ESG extraction prompts are tested with fake providers and strict schemas.
- ESG factors are linked only when segment names or business activities are explicit in evidence.
- Generic ESG indexes, cross-reference pages, and non-material governance boilerplate are skipped.
- Prototype scoring uses config-driven NACE base scores, reviewed ESG adjustments, score caps, and revenue weights.
- Scoring excludes rejected rows and total rows from segment contributions.
- Missing NACE mappings fall back to the configured default score with explicit rationale.

### Validation and review tests

Verify:

- Missing evidence is rejected or flagged.
- Ambiguous currency, scale, or period creates validation issues.
- Duplicate segment rows are detected.
- Dash-only values are not converted to zero without explicit context.
- Expenses, losses, assets, profit, EBIT, EBITDA, tax, cost of sales, and finance income are rejected as core revenue segment rows.
- Explicit total rows and document-level reported totals reconcile within configured tolerance.
- Missing declared table segments are flagged for review.
- Review statuses transition only through allowed states.
- Review events are written for row edits, row approvals/rejections, manual row additions, reviewer notes, validation issue state changes, and document approval.
- Manual row additions persist as `edited` rows with optional manual evidence.
- Validation issue review state supports acknowledgement and resolution without mutating the original validation issue.
- Document approval is blocked while any row is still unreviewed: `pending`, `needs_review`, or `ready_for_review`.
- Document approval is blocked by missing required fields, unresolved `error` issues, and unacknowledged `total_reconciliation_mismatch` issues.
- Final export is blocked until the document is approved.
- Reviewer NACE candidate acceptance and manual override write `segment_nace_selections`.
- NACE overrides create `ReviewEvent` audit rows.
- NACE LLM mapping rejects invented codes, marks total/reconciliation rows as not applicable, uses nearby context for ambiguous segment labels, and preserves reviewer overrides on rerun.
- ESG edits, unlink/relink actions, approvals, and rejections create `ReviewEvent` audit rows.
- Company-wide ESG factors remain unlinked unless a reviewer explicitly relinks them to a segment.
- CSV export uses the required stable column order and current reviewed values.
- JSON audit export includes all rows, evidence, validation issues, review events, timestamp, and pipeline config summary.
- Rejected rows are excluded from the main CSV/XLSX files and retained in the audit JSON.
- XLSX export creates an Office Open XML package with the revenue segment worksheet.

### Export tests

Verify CSV, XLSX, and JSON output:

- Includes the required export columns documented in `docs/EXPORTS.md`.
- Uses stable column order.
- Excludes rejected rows from the main CSV/XLSX files.
- Includes rejected rows in the JSON audit file.
- Preserves evidence text and page/section references.
- Refuses final export until all rows are approved, edited, or rejected.
- Refuses final export until the document itself is approved.
- Creates persisted `ExportRecord` rows for CSV, XLSX, and JSON outputs.
- Populates NACE fields from reviewer selection or the top generated candidate.
- Includes all top NACE candidates and the selected NACE mapping in the JSON audit file.
- Populates `esg_factor_summary` only from approved or edited segment-linked ESG factors.
- Includes full ESG factor records, company-wide context, rejected factors, and review status in JSON audit export.
- Recomputes prototype score fields at export time and includes segment/company score details.

### Evaluation tests

Verify:

- Gold CSV/JSON files require the documented manually labeled columns.
- Prediction loading prefers `audit_export.json` over CSV when both exist in an export directory.
- Matching thresholds are configurable and do not hard-code benchmark outcomes.
- Wrong values remain matched when segment/period evidence is strong enough, so failure analysis can classify them as `wrong_value`.
- Reports are regenerated from inputs and include no claimed performance outside the supplied gold set.

### API and UI tests

The current Streamlit app keeps business logic in services and testable UI helpers.

Verify:

- API response shapes remain backwards compatible through Pydantic schema tests.
- `fitch_extractor/ui/review.py` helper tests cover pipeline steps, summary/table shaping, table edit detection, and export readiness.
- Streamlit-specific smoke checks should be added once the project chooses a browser/UI test runner.
- Future HTTP route contract tests should target the same `ReviewService` methods rather than duplicating review rules in route handlers.

Current command:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m unittest tests.test_evaluation
.venv/bin/python -m compileall fitch_extractor streamlit_app.py scripts tests
```

## Failure Documentation

When tests fail:

- Record whether the failure existed before the current change.
- Fix failures caused by the current change before merging.
- Document unrelated pre-existing failures in the final response and decision log.
