# Revenue Segment Revenue-Segment Extraction Implementation Plan

## Current State Summary

The configured workspace at `/Users/angelolandiza/Documents/CS 294 Project FINAL` now contains the initial internal data model and SQLite persistence layer for the revenue segment extraction prototype.

Audit findings by requested area:

1. Backend structure: `revenue_segment_extractor/` now contains internal models, API-facing schemas, SQLite persistence helpers, a repository, a review service, deterministic ingestion, first-pass LLM revenue extraction, NACE mapping, ESG extraction/review, prototype scoring, and export services. No API routes are present yet.
2. Frontend / Streamlit structure: `streamlit_app.py` provides local review, ESG, scoring, and export controls.
3. API routes and response shapes: no route definitions are present. Pydantic response schemas exist for the implemented data entities.
4. Database or persistence layer: local SQLite initialization/reset helpers and repository methods are present.
5. Parsing, extraction, validation, NACE, ESG, scoring, and export logic: these prototype workflows are implemented as local services and Streamlit controls.
6. Tests and gaps: unit tests cover model serialization, repository flows, extraction, validation, review logging, NACE/ESG behavior, scoring calculations, export output, and database init/reset.
7. Prompts, model calls, API key handling, and config: first-pass extraction now uses a versioned prompt builder, strict provider interface, fake provider, Anthropic provider, and environment-based configuration. Real Anthropic mode requires `ANTHROPIC_API_KEY`; tests use fake mode.
8. Sample files, ground truth, and output format: generated CSV/XLSX/JSON exports are supported; representative sample PDFs and a broader ground-truth set remain project work.

Because no HTTP API or frontend exists yet, the current contract is limited to internal dataclasses, SQLite tables, and Pydantic response schemas.

## Main Risks

- No baseline application exists in this workspace, so future implementation must establish contracts carefully before feature work begins.
- The core extraction problem is accuracy-sensitive and should not be implemented as a single prompt-only workflow.
- PDF annual reports vary heavily in layout, currency presentation, table structure, OCR quality, and segment naming.
- Revenue values can appear in different scales, periods, currencies, and table contexts; normalization must preserve evidence.
- NACE and ESG mapping can introduce false certainty unless candidates, confidence, and review status are modeled explicitly.
- Final exports must be gated by review status so unreviewed rows cannot leave the prototype as final data.
- API/frontend contracts are not yet available; once created, they should be versioned or regression-tested before changes.

## Ordered Implementation Phases

### Phase 1: Project Skeleton and Contracts

Goal: create a minimal runnable local prototype structure without implementing the full extraction pipeline.

Status: partially complete. Internal models, Pydantic schemas, SQLite persistence, database management script, and unit tests now exist. API route and frontend choices remain open.

Likely files/modules:

- `requirements.txt`
- `revenue_segment_extractor/__init__.py`
- `revenue_segment_extractor/models.py`
- `revenue_segment_extractor/api/schemas.py`
- `revenue_segment_extractor/persistence/database.py`
- `revenue_segment_extractor/persistence/repository.py`
- `revenue_segment_extractor/persistence/review.py`
- `tests/test_models_and_schemas.py`
- `tests/test_persistence_repository.py`
- `docs/API_CONTRACT.md`
- `docs/DATA_CONTRACT.md`

Expected work:

- Choose one backend entrypoint style, preferably FastAPI if an API is required or Streamlit-only if the prototype is intentionally local UI first.
- Define strict Python data models for the target entities in `docs/DATA_CONTRACT.md`.
- Establish environment variable names without hardcoded secrets.
- Add tests for schema defaults, review status rules, and export gating.

### Phase 2: Deterministic PDF Parsing and Candidate Retrieval

Goal: extract text, page metadata, and candidate revenue sections before any LLM calls.

Likely files/modules:

- `src/revenue_segment_extractor/parsing/pdf_parser.py`
- `src/revenue_segment_extractor/parsing/page_model.py`
- `src/revenue_segment_extractor/retrieval/candidates.py`
- `tests/test_pdf_parser.py`
- `tests/test_candidates.py`
- `samples/` or `fixtures/` with small non-sensitive PDFs/text fixtures

Expected work:

- Parse PDFs into `ParsedPage` records.
- Identify candidate pages/sections with deterministic keywords, table cues, and section headings.
- Preserve page references and raw evidence snippets.
- Add fixtures that cover table rows, narrative revenue mentions, missing revenue, and multi-currency edge cases.

### Phase 3: Strict LLM Extraction

Goal: use LLMs only on narrowed evidence windows and validate responses against strict schemas.

Status: first-pass revenue segment extraction is implemented for candidate page bundles. Second-pass verification and arbitration remain out of scope.

Likely files/modules:

- `src/revenue_segment_extractor/llm/client.py`
- `src/revenue_segment_extractor/llm/prompts.py`
- `src/revenue_segment_extractor/extraction/segment_extractor.py`
- `src/revenue_segment_extractor/validation/normalization.py`
- `tests/test_segment_extractor.py`
- `tests/test_normalization.py`

Expected work:

- Require structured LLM responses matching the `SegmentRow` and `SegmentEvidence` contract.
- Keep prompt templates versioned and covered by tests for required fields.
- Normalize currency, scale, period, revenue units, and review status in Python after model output.
- Treat malformed LLM output as a validation issue, not as silently accepted data.

### Phase 4: Validation, Review Workflow, and Export Gate

Goal: make human review the final quality gate before final exports.

Likely files/modules:

- `src/revenue_segment_extractor/validation/rules.py`
- `src/revenue_segment_extractor/review/events.py`
- `src/revenue_segment_extractor/export/exporter.py`
- `tests/test_validation_rules.py`
- `tests/test_review_gate.py`
- `tests/test_exporter.py`

Expected work:

- Add validation issues for missing evidence, ambiguous scale, missing period, invalid currency, duplicate segment rows, and low-confidence mappings.
- Implement review statuses: `pending`, `approved`, `edited`, and `rejected`.
- Block final export unless every row is approved, edited, or rejected.
- Export the required core columns to CSV/XLSX/JSON with stable headers.

### Phase 5: NACE, ESG, and Prototype Scoring

Goal: add mapping and scoring as reviewable enrichments rather than hidden final truth.

Likely files/modules:

- `src/revenue_segment_extractor/nace/mapping.py`
- `src/revenue_segment_extractor/esg/factors.py`
- `src/revenue_segment_extractor/scoring/segment_score.py`
- `tests/test_nace_mapping.py`
- `tests/test_esg_factors.py`
- `tests/test_segment_score.py`

Expected work:

- Return ranked `NaceCandidate` records with evidence and confidence.
- Extract ESG factors from evidence windows and document source references.
- Score segments using transparent, test-covered inputs.
- Keep these enrichments separate from core revenue extraction so revenue export remains auditable.

### Phase 6: Local UI and API Integration

Goal: provide a reviewable local prototype without breaking established contracts.

Likely files/modules:

- `app.py` or `streamlit_app.py`
- `src/revenue_segment_extractor/api/routes.py`
- `src/revenue_segment_extractor/api/schemas.py`
- `tests/test_api_contract.py`
- `tests/test_streamlit_smoke.py`

Expected work:

- Upload/select documents.
- Show parsed candidates, extracted rows, validation issues, evidence, and review controls.
- Persist review events.
- Disable final export until review gate passes.
- Add smoke tests for API routes and UI startup.

## Tests That Should Exist By The End

- Contract tests for all target data entities and required export columns.
- Parser tests for page extraction, section references, and evidence windows.
- Candidate retrieval tests using deterministic text fixtures.
- LLM schema tests with mocked model responses, including malformed output.
- Normalization tests for currency, scale, revenue units, and periods.
- Validation tests for missing/ambiguous fields and duplicate rows.
- Review workflow tests for pending, approved, edited, and rejected transitions.
- Export gate tests proving final export is blocked until all rows are resolved.
- Export format tests for CSV, XLSX, and JSON headers and record shapes.
- API contract tests for every route once routes exist.
- UI smoke tests once a frontend or Streamlit app exists.

## Manual Verification Checklist

After the first runnable prototype exists:

1. Start the app with the documented local command.
2. Upload or select a sample annual report PDF.
3. Confirm parsed pages and candidate revenue sections are visible.
4. Run extraction and confirm each row contains evidence text and a page/section reference.
5. Confirm validation issues appear for incomplete or ambiguous rows.
6. Approve, edit, or reject every extracted row.
7. Confirm export is blocked while any row is still pending.
8. Export CSV, XLSX, and JSON after review is complete.
9. Confirm exported records contain the core output columns in the documented order.
10. Compare exported rows against the ground-truth fixture for the sample document.
