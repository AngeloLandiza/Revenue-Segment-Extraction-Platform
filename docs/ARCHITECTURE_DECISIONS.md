# Architecture Decisions

## 2026-05-02: GitHub Repository Landing Page Without GitHub Pages

### What changed

- Removed the GitHub Pages workflow and static site artifacts.
- Updated the root `README.md` to identify itself as the GitHub repository landing page.
- Updated documentation references so `docs/` is a reference folder, not a Pages site.
- Kept Streamlit Community Cloud as the hosted interactive app path.
- Updated deployment contract tests to assert that GitHub Pages is absent and the root README is
  the visible GitHub project entry point.

### Why this design was chosen

The user no longer wants GitHub Pages. GitHub automatically renders the root `README.md` on the
repository home page, which is the simplest way to make the project presentable without maintaining
a second static site. Streamlit Community Cloud remains the correct option for the interactive app
because it can run Python and keep secrets outside the repository.

### Alternatives rejected

- Keeping the Pages workflow as optional: rejected because the request was to stop using GitHub
  Pages.
- Moving all docs into the README: rejected because the README should stay readable while detailed
  architecture, validation, review, NACE, ESG, scoring, and testing references remain useful.
- Adding a new web frontend: rejected because the existing Streamlit app already provides the
  working prototype and review flow.

### Tradeoffs remaining

- The GitHub repository page now depends entirely on the root README for first impressions.
- Detailed docs remain in Markdown files under `docs/`, so reviewers need to click through for
  deeper implementation details.
- Hosted Streamlit runtime storage is still ephemeral unless a production deployment adds managed
  storage.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py compare_extractions_to_ground_truth.py test_claude_api.py
```

Manual verification:

Open the pushed GitHub repository and confirm the root `README.md` renders on the repository home
page. Confirm there is no `.github/workflows/pages.yml`, no `docs/index.html`, and no
`docs/.nojekyll`.

### How this supports the Fitch project requirements

- Preserves the Streamlit review workbench and all extraction, validation, NACE, ESG, scoring, and
  export gates.
- Keeps the project easy for industry reviewers to inspect from the GitHub repository page.
- Avoids implying that GitHub Pages can run the PDF/LLM workflow.
- Keeps secrets out of the repository and directs hosted app usage to Streamlit Community Cloud.

## 2026-05-02: Resume Project Description Guidance

### What changed

- Added resume-ready wording guidance for describing the Fitch revenue-segment extraction prototype.
- No application code, schemas, UI behavior, persistence, extraction logic, review gates, or export behavior changed.
- Clarified resume language around AI-assisted development tools so the project description stays honest
  without weakening ownership of the software work.

### Why this design was chosen

The request was for accurate resume positioning, not a runtime feature. The existing project already
implements a hybrid deterministic retrieval plus schema-bound LLM extraction workflow, so the safest
response was to describe the work precisely without overstating production deployment or team ownership.

### Alternatives rejected

- Changing application code: rejected because no product behavior change was requested.
- Claiming the project as a fully deployed production system: rejected because this repository is a
  production-minded local prototype.
- Omitting collaboration context: rejected because the finance-student collaboration and company
  representative presentation are relevant signals for an AWE/finance-technology resume.

### Tradeoffs remaining

- Resume wording must stay concise, so it cannot capture every module in the pipeline.
- "RAG" is best framed as "RAG-style" or "retrieval-augmented" unless the target audience expects
  vector-database retrieval specifically.
- Quantified metrics should only be added after evaluation results are finalized and defensible.
- Mentioning tools such as Codex and Cursor can signal modern workflow fluency, but it should not replace
  the technical substance of the implemented system.

### How to test or verify the change

Review the suggested resume bullets against the implemented workflow documented in `README.md`,
`docs/PARSING_AND_RETRIEVAL.md`, `docs/LLM_EXTRACTION.md`, `docs/NACE_MAPPING.md`, and
`docs/REVIEW_WORKFLOW.md`.

### How this supports the Fitch project requirements

- Keeps the project description aligned with the hybrid deterministic-plus-LLM architecture.
- Highlights segment extraction, validation, review gates, NACE mapping, ESG extraction, and export.
- Preserves accurate ownership language by separating the software implementation from the broader
  finance collaboration and presentation work.

## 2026-05-01: Proposal-Ready Documentation And Deployment Defaults

### What changed

- Rewrote `README.md` into an adoption-facing project guide with architecture, setup, review gate, deployment, testing, evaluation, security, and limitation sections.
- Added `docs/README.md` as a structured documentation index.
- Added `docs/STREAMLIT_COMMUNITY_CLOUD.md` for hosted Streamlit setup and verification.
- Updated `docs/index.html` so the GitHub Pages static site links to the documentation index and Streamlit deployment guide.
- Changed `.env.example` and `scripts/run_streamlit_anthropic.sh` so optional arbitration stays disabled unless a user explicitly enables it.
- Refactored small Streamlit UI helpers for provider-label mapping and data-editor selection cleanup.
- Added deployment/documentation contract tests and expanded UI helper tests.
- Removed generated Python bytecode caches from the working tree.

### Why this design was chosen

The prompt asked for a cleaner, industry-ready proposal without changing the existing extraction,
review, validation, NACE, ESG, scoring, or export behavior. Documentation and deployment defaults
were the highest-value cleanup areas because reviewers need to understand how to run the app,
where the quality gates live, and why GitHub Pages and Streamlit Community Cloud serve different
roles. The code refactor stayed intentionally small and local to repeated Streamlit UI helpers.

### Alternatives rejected

- Rewriting the application structure: rejected because it would risk the existing frontend/API
  contract and review workflow for limited proposal value.
- Adding a new documentation generator: rejected because GitHub Pages already publishes the
  `docs/` folder and static Markdown/HTML is sufficient.
- Enabling arbitration by default in demo config: rejected because arbitration is optional and can
  request unavailable or expensive models if configured casually.
- Adding new lint/type dependencies: rejected because the repository does not currently define
  those tools and the cleanup did not require new packages.

### Tradeoffs remaining

- Streamlit Community Cloud storage remains ephemeral, so a production deployment would need
  managed storage for PDFs, SQLite data, previews, and exports.
- The README and docs are clearer, but they must continue to be maintained alongside code changes.
- The Streamlit entry file remains large; only low-risk duplicated helper logic was refactored in
  this pass to avoid behavior churn.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py compare_extractions_to_ground_truth.py test_claude_api.py
```

Manual verification:

```bash
export FITCH_EXTRACTION_PROVIDER=fake
.venv/bin/streamlit run streamlit_app.py
```

Open the app, upload a PDF, click `Queue extraction`, process the next queued document, review the
rows, approve or reject every row, approve the document, and create exports. Also open
`docs/index.html` and confirm the static GitHub Pages links resolve to the documentation index and
Streamlit Community Cloud guide.

### How this supports the Fitch project requirements

- Preserves the hybrid deterministic-plus-LLM pipeline and strict schema boundaries.
- Keeps Python normalization, validation, NACE mapping, ESG extraction, scoring, human review, and
  export gates unchanged.
- Makes the core output columns, review gate, security posture, and deployment model easier for
  industry reviewers to evaluate.
- Keeps GitHub Pages static and documentation-only while documenting how Streamlit Community Cloud
  can run the interactive app with private secrets.
- Adds tests that prevent accidental regression of the deployment entry point, documentation
  contract, GitHub Pages artifact path, and safe arbitration defaults.

## 2026-05-01: GitHub Pages Shell And Document Queue

### What changed

- Added a SQLite-backed `document_queue_jobs` table, repository methods, queue service, and terminal worker.
- Changed Streamlit uploads and extraction reruns to enqueue work before extraction.
- Added `scripts/run_streamlit_anthropic.sh`, `.env.example`, and `.gitignore` for safe local startup without committing secrets.
- Added a GitHub Pages workflow and static `docs/index.html` project page.

### Why this design was chosen

GitHub Pages is static hosting, so it cannot run the Python Streamlit application, process PDFs, or protect Anthropic API keys. The safe design is to publish a static project page from `docs/` while keeping extraction behind a local or private server runtime that reads environment variables. A persisted SQLite queue gives multiple Streamlit sessions and terminal workers one shared, auditable handoff point for document throughput.

### Alternatives rejected

- Embedding the Anthropic key in frontend code or Pages config: rejected because browser-delivered secrets are public.
- Replacing Streamlit with a new static frontend: rejected as a large rewrite that would not satisfy PDF processing or LLM execution on Pages.
- Adding an external broker such as Redis or Celery: rejected because the local prototype already uses SQLite and the prompt asks for a GitHub-ready prototype, not new infrastructure.
- Keeping immediate upload-time extraction only: rejected because concurrent users could trigger overlapping long-running jobs without queue visibility.

### Tradeoffs remaining

- The queue serializes work in the shared SQLite database, but it is still a local prototype queue, not a distributed production worker fleet.
- Processing still requires a running Python environment with private environment variables.
- The GitHub Pages site is documentation and launch guidance only; the interactive extraction workbench must run locally or on a server.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_persistence_repository tests.test_queueing
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
cp .env.example .env.local
# Add a private ANTHROPIC_API_KEY to .env.local.
scripts/run_streamlit_anthropic.sh
```

Upload a PDF, click `Queue extraction`, then click `Process next queued document` in the sidebar. For terminal processing, run `.venv/bin/python scripts/process_queue.py --all`.

### How this supports the Fitch project requirements

- Preserves the hybrid deterministic-plus-LLM pipeline and strict extraction schemas.
- Keeps Python normalization, validation, review gates, NACE, ESG, scoring, and export behavior intact.
- Prevents final export changes and keeps the human review gate as the final quality control.
- Improves multi-user throughput by making document processing explicit, serialized, and auditable.
- Keeps API keys out of the repository and out of static GitHub Pages.

## 2026-04-30: Consolidated Pipeline Walkthrough Documentation

### What changed

- Added `docs/PIPELINE_WALKTHROUGH.md`.
- Documented the pipeline in numbered steps from configuration, upload, PDF parsing, keyword retrieval, LLM extraction, validation, NACE, ESG, review, scoring, usage metrics, and export.
- Included implementation details such as keyword search, structural page scoring, strict schemas, Python normalization, bbox evidence matching, and review gates.

### Why this design was chosen

The project already had focused docs for parsing, LLM extraction, ESG, NACE, review, scoring, and export, but no single clean walkthrough that explained how the whole pipeline connects. A consolidated numbered document is easier for reviewers, analysts, and graders to follow without changing runtime behavior.

### Alternatives rejected

- Expanding `README.md`: rejected because the requested explanation is too detailed for the main setup/readme path.
- Replacing the existing focused docs: rejected because those files are useful for module-specific detail.
- Adding diagrams only: rejected because the user asked for exact numbered pipeline steps and terms.

### Tradeoffs remaining

- The walkthrough is documentation-only and must stay aligned with future code changes.
- It intentionally summarizes some implementation details instead of duplicating every prompt and schema in full.
- Existing focused docs remain the canonical place for module-specific commands and edge cases.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

Open `docs/PIPELINE_WALKTHROUGH.md` and confirm the numbered flow matches the Streamlit workflow: upload or select a document, run extraction, review segment and ESG evidence, approve rows, compute scores, approve the document, and export.

### How this supports the Fitch project requirements

- Makes the hybrid deterministic-plus-LLM design explicit.
- Shows where keyword search, strict schemas, Python validation, review gates, NACE, ESG, scoring, and export fit together.
- Helps demonstrate why the project is production-minded rather than a single prompt-only workflow.

## 2026-04-30: ESG Evidence Highlight Preview

### What changed

- Added an ESG evidence preview in the Streamlit ESG review panel.
- The selected ESG factor now shows its evidence text and, when possible, a rendered source PDF page with a highlighted evidence area.
- Reused parsed pages, `locate_evidence_snippet`, and `render_page_with_bbox_to_png` instead of adding ESG bbox columns or a new evidence table.
- Added focused tests for ESG bbox lookup and page-reference parsing.

### Why this design was chosen

ESG factors already store `page_ref` and `evidence_text`, and parsed pages already store text-block bbox coordinates. Matching the selected factor's evidence text to parsed page blocks at review time gives analysts the visual source check with minimal code and no persistence/API/export changes.

### Alternatives rejected

- Adding `bbox_json` to `esg_factors`: rejected for this prompt because it would require a schema migration and repository changes.
- Creating an `esg_evidence` table: rejected as more durable but too broad for the requested minimal change.
- Re-running ESG extraction to force bbox output: rejected because bbox coordinates should come from deterministic parsing, not the LLM.

### Tradeoffs remaining

- ESG highlights appear only when `page_ref` points to a parsed page and `evidence_text` matches a parsed text block well enough.
- The computed ESG bbox is not persisted, so it is regenerated when the selected factor is rendered.
- OCR/vision-only ESG evidence may still lack usable bbox coordinates.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_ui_review_enrichment
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open a document with ESG factors, go to `ESG Factors`, select a factor with a page reference, and confirm the evidence text appears with a highlighted source page when the text can be matched.

### How this supports the Fitch project requirements

- Extends source-evidence review from revenue segments to ESG factors.
- Keeps deterministic parsing as the source of bbox coordinates.
- Preserves the existing review, validation, and export contracts while making analyst validation more visual.

## 2026-04-30: Evidence Page Highlight Preview

### What changed

- Added `render_page_with_bbox_to_png` to render a PDF page with a visible highlight over a stored evidence bounding box.
- Updated the Streamlit evidence panel to show the highlighted source page image for each evidence item with bbox coordinates.
- Kept page number, parser source, and evidence text visible even when no bbox exists or page rendering fails.
- Updated review workflow documentation and added a rendering regression test.

### Why this design was chosen

The extraction pipeline already stores page numbers, evidence snippets, parser source, and bbox JSON. Rendering a highlighted page from that existing data gives analysts a direct visual source check without changing extraction, persistence, exports, or the API contract.

### Alternatives rejected

- Adding a new PDF viewer component: rejected because it would add dependency and UI complexity for a local prototype.
- Persisting preview images in the database: rejected because previews are derived artifacts and can be regenerated from the source PDF and bbox.
- Replacing evidence text with only an image: rejected because text remains necessary for review, accessibility, and export auditability.

### Tradeoffs remaining

- Highlights are only available when the stored evidence item has bbox coordinates.
- Preview PNGs are generated under `data/evidence_previews/` and can be regenerated if deleted.
- OCR or LLM-only evidence may still have no bbox, so analysts must use the page number and snippet in those cases.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_pdf_ingestion
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open a document with extracted rows, go to `Segment Review`, then `Evidence`. Select a row with stored bbox evidence and confirm the source PDF page renders with the evidence area highlighted.

### How this supports the Fitch project requirements

- Strengthens human review by showing the original page location for each extracted revenue segment reference.
- Preserves auditability by keeping evidence text and page references alongside the visual highlight.
- Maintains the hybrid pipeline and final export contract with a UI-only review improvement.

## 2026-04-30: Arbitration Default Avoids Opus Calls

### What changed

- Changed `DEFAULT_ARBITRATION_MODEL` from the Opus model string to the configured extraction model.
- Updated the arbitration prompt and UI/CLI-facing docs to describe arbitration as a configured LLM pass instead of an Opus-specific pass.
- Kept the existing `llm_opus_arbitration_*` issue type names for backwards compatibility, while changing the human-readable result message to say `LLM arbitration`.
- Updated arbitration tests so uncertain cases still call the configured arbitration model, but the default no longer creates an Opus request.

### Why this design was chosen

The failed Opus request came from the optional arbitration path, not from the core deterministic retrieval, first-pass extraction, normalization, validation, NACE, ESG, review, or export workflow. Changing the default model removes the accidental Opus call with a very small code change while preserving the feature for users who explicitly configure a different arbitration model.

### Alternatives rejected

- Removing arbitration entirely: rejected because the existing optional hard-case path and tests are useful.
- Renaming persisted arbitration issue types now: rejected because review logic and existing validation records rely on the current names.
- Silently overriding an explicitly set `FITCH_ARBITRATION_MODEL`: rejected because explicit environment configuration should remain visible and controllable.

### Tradeoffs remaining

- Existing issue type names still contain `opus` for backwards compatibility even when arbitration uses Sonnet.
- If a shell still exports `FITCH_ARBITRATION_MODEL=claude-opus-4-7`, that explicit setting will still request Opus until the variable is unset or changed.
- Arbitration remains disabled by default and still runs only for uncertain documents when enabled.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_verification_arbitration
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
unset FITCH_ARBITRATION_MODEL
.venv/bin/streamlit run streamlit_app.py
```

Run extraction on a document. In the `Workflow Usage` details, confirm no call uses an Opus model unless `FITCH_ARBITRATION_MODEL` was explicitly set to one.

### How this supports the Fitch project requirements

- Keeps the hybrid pipeline and human review gate unchanged.
- Removes a fragile optional model dependency from the default local prototype workflow.
- Maintains backwards compatibility for review records and API-facing validation issue types.

## 2026-04-30: Pre-Review Workflow Usage Summary

### What changed

- Added a lightweight LLM usage tracker that wraps the existing provider interface.
- Captured workflow duration, LLM call count, input/output token counts per call, and estimated total LLM cost for Streamlit analysis runs before analyst review.
- Added a Streamlit `Workflow Usage` section with headline metrics and an expandable per-call table.
- Added tests for Sonnet/Opus cost calculation, provider-reported token usage, estimated token fallback, and failed request accounting.

### Why this design was chosen

The provider wrapper records usage across revenue extraction, optional verification/arbitration, NACE mapping, and ESG extraction without changing each service's business logic. The UI stores the latest run metrics in Streamlit session state, which keeps the change small and avoids adding persistence or export fields for prototype-only operating telemetry.

### Alternatives rejected

- Persisting workflow usage in SQLite: rejected for now because the request only needs a small UI section and persistence would require schema migration and additional repository methods.
- Adding token/cost columns to final exports: rejected because final Fitch deliverables should remain reviewed revenue-segment outputs, not operational run telemetry.
- Estimating token counts only from text length even when provider usage exists: rejected because Anthropic responses can provide actual input/output token usage.

### Tradeoffs remaining

- Metrics are session-scoped in Streamlit and disappear after an app restart.
- Cost estimates use standard Anthropic family rates for Opus and Sonnet and do not account for prompt caching, batch discounts, long-context premiums, or non-Anthropic providers.
- Token counts are estimated from character length when provider usage is unavailable, including fake-provider smoke runs.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_llm_usage
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Upload a PDF, choose either the Anthropic provider or fake smoke-test provider, and click `Run extraction`. After the workflow completes, confirm the `Workflow Usage` section appears above the approval checklist and that the expander lists each LLM request with model, prompt version, tokens, and estimated cost.

### How this supports the Fitch project requirements

- Keeps the hybrid pipeline intact by measuring the existing deterministic-plus-LLM workflow rather than replacing it.
- Makes pre-review operating cost and latency visible before human review.
- Preserves the final review and export gates because usage telemetry is display-only and does not affect row approval or final CSV/XLSX/JSON outputs.

## 2026-04-30: Hydro Ottawa NACE Candidate Recovery and Summary Wrapping

### What changed

- Changed NACE validation errors to say `outside the supplied candidate set` instead of `outside the reference set`.
- Updated NACE mapping so an LLM returning an existing-but-unsupplied code no longer aborts the whole document; the affected row keeps retrieved candidates, clears auto-selection, gets a warning, and remains reviewable.
- Classified government grant income and business interruption / insurance proceeds as reconciliation-style non-business rows, excluding them from NACE and scoring.
- Added a utility-infrastructure NACE hint for `42.22`, scoped to service/infrastructure rows so it can appear as a candidate for mixed utility service segments without being forced as a high-confidence mapping.
- Replaced the top Streamlit document summary `st.metric` values with wrapped HTML summary cards so long company/document names are not cut off.

### Why this design was chosen

The NACE reference already contains codes such as `65.12` and `42.22`; the failure was caused by the LLM returning codes that were not in the candidate list it was given. Keeping candidate-list validation is still important because it prevents arbitrary invented mappings, but a single bad row should not stop the rest of the document. The Hydro Ottawa revenue rows also show why conservative row classification matters: insurance proceeds and grant income are income/reconciliation rows, not operating activity segments.

### Alternatives rejected

- Accepting any valid NACE reference code returned by the LLM: rejected because the model could bypass retrieval evidence and over-map weak rows.
- Mapping business interruption proceeds to `65.12`: rejected because Hydro Ottawa is not operating a non-life insurance segment; the proceeds relate to an insurance recovery.
- Auto-selecting `42.22` for Commercial services: rejected because the segment appears mixed and should be reviewed before selecting a primary code.
- Keeping truncated `st.metric` cards: rejected because the document identity needs to remain readable in review.

### Tradeoffs remaining

- `42.22` is a candidate for utility infrastructure service contexts, not a definitive answer.
- Rows extracted from Hydro Ottawa page 48 are revenue/income line items rather than a formal reportable segment note, so analyst review remains important.
- The wrapped summary cards use simple inline HTML to avoid a broader Streamlit layout rewrite.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_enrichment_classification tests.test_nace_mapping tests.test_ui_review_enrichment
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open `10443_AR_2024_English.pdf`. Confirm the document name wraps in the top summary, business interruption proceeds and government grant income are not counted as NACE/scoring rows, and Commercial services can show `42.22` as a candidate that still needs review.

### How this supports the Fitch project requirements

- Keeps the hybrid retrieval-before-LLM design while making candidate validation less brittle.
- Avoids assigning insurance-sector NACE to insurance recoveries.
- Preserves mixed-segment review instead of forcing a single high-confidence NACE code.
- Improves the review UI without changing the backend response contract.

## 2026-04-30: Conservative Generalized Enrichment Gates

### What changed

- Added deterministic row classification for `business_segment`, subtotal, total, elimination, reconciliation, and unknown rows.
- Added segment type classification for single-activity, mixed, geographic, customer, product, and unclear segments.
- Added additive metadata fields for original/normalized segment names, language, evidence translation placeholders, ESG linkage type, ESG category, score relevance, impact mechanism, and ESG cluster keys.
- Updated NACE mapping to skip non-business rows and require human review for mixed or unclear business segments instead of auto-selecting a high-confidence code.
- Updated ESG persistence and scoring so only segment-specific, score-relevant factors can affect segment scores, similar factors are clustered, and ESG adjustments are capped.
- Updated exports and API schemas additively so existing fields remain available.

### Why this design was chosen

The enrichment layer now uses deterministic Python gates before LLM-assisted mapping or scoring. This keeps revenue extraction stable while making NACE, ESG, and scoring more defensible across industries, report layouts, currencies, and languages. The implementation is additive: existing frontend/API fields still exist, while richer metadata is available for review and audit.

### Alternatives rejected

- Company-specific rules for a known report: rejected because the pipeline must generalize across annual reports and 10-Ks.
- Forcing every extracted row into one NACE code: rejected because totals, eliminations, geographic/customer splits, and mixed activities need review or exclusion from scoring.
- Summing every approved ESG factor independently: rejected because repeated similar evidence can over-improve or over-penalize a segment.
- Replacing original labels with translations: rejected because official segment labels must remain auditable.

### Tradeoffs remaining

- Language normalization is deterministic and conservative; it preserves original text and uses simple normalized English hints rather than full translation.
- Segment type classification is intentionally cautious, so some legitimate segments will need review before NACE selection.
- ESG clustering uses structured factor metadata and evidence source; weakly described factors may still require analyst cleanup.
- The prototype scoring remains demonstration-only and depends on reviewed NACE/ESG inputs.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_enrichment_classification tests.test_nace_mapping tests.test_esg_extraction tests.test_scoring_service
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Upload or open a reviewed document with business rows plus totals/eliminations. Confirm totals and eliminations remain visible but do not receive NACE selections or scores, mixed segments show review metadata, company-wide ESG factors remain company-level, and final exports include the additive metadata columns.

### How this supports the Fitch project requirements

- Preserves the hybrid pipeline by adding deterministic enrichment gates before LLM-dependent NACE/ESG decisions.
- Keeps strict schemas and Python validation/normalization as the source of score eligibility.
- Makes mixed segments, multilingual evidence, company-wide ESG, and reconciliation rows auditable instead of overconfident.
- Maintains the human review gate and backwards-compatible frontend/API response shape.

## 2026-04-30: Two-Step Auto Approval Action

### What changed

- Added an `Auto approve document` action in the Streamlit review controls.
- The action requires two explicit confirmation steps before it writes any review changes.
- After final confirmation, it approves complete non-rejected segment rows, accepts the highest-ranked NACE candidate where no selection exists, marks open validation issues with an audit note, approves pending ESG factors, and then attempts document approval.
- Added regression coverage for the auto-approval helper across row review, NACE selection, validation review, ESG review, and document approval.

### Why this design was chosen

The implementation uses existing `ReviewService` methods instead of bypassing review state directly. That keeps reviewer events, validation checks, missing-field checks, NACE selection audit events, ESG review events, and document approval gating intact. The two-step confirmation flow makes the convenience action explicit because it can update many review records at once.

### Alternatives rejected

- Updating database statuses directly: rejected because it would bypass audit logs and approval validation.
- Approving rejected rows: rejected because rejected rows are already reviewed and must remain excluded from final CSV export.
- Auto-filling missing required row fields: rejected because that would create unsupported data and reduce trust.
- Skipping the final `approve_document` gate: rejected because the document should only approve when existing blockers are cleared.

### Tradeoffs remaining

- The action is intentionally aggressive and should be used only when the analyst has inspected the extracted rows and evidence.
- Rows with missing required values still stop auto approval and require manual correction.
- If no NACE candidates exist for a row, the action does not invent a classification.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_review_workflow
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open a reviewed document, click `Auto approve document`, click `Continue`, read the final warning, then click `Auto approve now`. Confirm the document becomes approved only if no blockers remain.

### How this supports the Fitch project requirements

- Reduces reviewer effort for documents that have already been inspected and only need final status updates.
- Preserves human confirmation, audit logging, and final approval gates.
- Keeps rejected rows excluded and avoids fabricating missing data.

## 2026-04-30: Export File Downloads in Review UI

### What changed

- Added Streamlit download buttons for generated CSV, XLSX, and JSON audit export files.
- The export panel now shows the latest export folder and timestamp.
- Download buttons are based on recorded export files and remain disabled when the document is not currently export-ready.
- Added a small helper and regression test to choose the latest export record per file format.

### Why this design was chosen

The existing `ExportService` already writes final files and records their paths after enforcing document approval. Reusing those export records keeps the download feature as a UI convenience instead of creating another export path. This preserves the final review gate and avoids duplicating CSV generation logic inside Streamlit.

### Alternatives rejected

- Generating CSV bytes directly in the UI: rejected because it would duplicate export logic and risk diverging from the approved final export.
- Allowing downloads before the document is export-ready: rejected because stale or unapproved outputs should not be treated as final deliverables.
- Adding a separate file-serving API route: rejected because the current local prototype uses Streamlit and can download from local export files directly.

### Tradeoffs remaining

- Download buttons require the export files to still exist on disk at the recorded paths.
- If a user deletes the `exports/` folder, they must create the export files again before downloading.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_review_workflow
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open an approved/export-ready document, click `Create export files`, then use `Download CSV` in the Export section.

### How this supports the Fitch project requirements

- Makes final reviewed outputs easier to collect for class submission and downstream ESG/NACE workflows.
- Preserves the approval gate before final export artifacts are downloadable.
- Keeps rejected rows excluded by relying on the existing export service.

## 2026-04-30: NACE Checklist Progress for Applicable Rows

### What changed

- Updated the review checklist's NACE progress calculation to count only non-rejected rows that represent operating segment activities.
- Excluded total, reported, consolidated, elimination, reconciliation, reclassification, and hedging rows from the NACE checklist denominator.
- Added a regression test for the case where all applicable NACE rows are approved but roll-up rows previously kept progress at `3/6`.

### Why this design was chosen

The NACE review task should measure activity-classification work, not financial roll-up or reconciliation rows that should not receive an activity code. Keeping this rule in the review helper fixes the visible progress issue without changing extraction, persistence, exports, or the analyst's detailed NACE controls.

### Alternatives rejected

- Requiring analysts to assign dummy NACE codes to totals or eliminations: rejected because it would reduce output quality.
- Changing stored segment rows or deleting roll-up rows: rejected because those rows can still be useful evidence for reconciliation and review.
- Hiding all rows without NACE candidates from the table: rejected because analysts still need visibility into extracted rows.

### Tradeoffs remaining

- The rule is label-based, so an unusual roll-up label may still need manual row rejection or an override.
- Legitimate operating segments with very unusual names that look like roll-ups could be excluded from checklist progress, though the row remains visible in the review table.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_review_workflow
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open a reviewed document with operating rows plus total/reconciliation rows, accept NACE mappings for the operating rows, and confirm the NACE checklist reaches 100%.

### How this supports the Fitch project requirements

- Keeps analyst review focused on business activities that need NACE mapping.
- Avoids misleading approval progress for documents that include totals and reconciliation lines.
- Preserves human review, validation visibility, and export gating.

## 2026-04-30: Batch NACE and ESG Review Selection

### What changed

- Added selectable NACE candidate tables with a select-all checkbox.
- Added `Accept selected` for NACE candidates; when several candidates are selected for a segment, the highest-ranked selected candidate is accepted.
- Added selectable ESG tables for both segment-linked and company-wide ESG factors.
- Added `Select all ESG factors for batch action`, `Approve selected ESG`, and `Reject selected ESG`.
- Preserved single-item NACE and ESG controls for detailed review, relinking, unlinking, and overrides.
- Added test coverage for batch NACE selection behavior.

### Why this design was chosen

The review UI now uses the same selection pattern across segment rows, validation issues, NACE candidates, and ESG factors. NACE differs from the other tables because a segment should have one selected classification, so the batch action accepts the highest-ranked selected candidate instead of trying to accept every selected candidate.

All batch actions still use `ReviewService`, preserving reviewer audit events and existing validation behavior.

### Alternatives rejected

- Accepting every selected NACE candidate: rejected because it would overwrite selections repeatedly and confuse the final mapping.
- Removing detailed NACE/ESG controls: rejected because analysts still need overrides, relinking, and individual notes.
- Direct persistence updates for ESG/NACE batch actions: rejected because review events would be skipped.

### Tradeoffs remaining

- NACE batch selection is scoped to the currently selected segment's candidate table.
- ESG relinking still remains an individual action because relink targets differ by factor.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_review_workflow
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Select NACE candidates and click `Accept selected`; select ESG factors in either table and batch approve or reject them.

### How this supports the Fitch project requirements

- Speeds review for Sustainable Fitch analysts handling many extracted rows and factors.
- Keeps NACE and ESG review auditable and human-gated.
- Preserves export and persistence contracts.

## 2026-04-30: Clickable Approval Checklist Navigation

### What changed

- Made approval checklist cards clickable.
- Each card now links to the relevant review section anchor.
- Added URL focus handling so the destination section title is highlighted after navigation.
- Added target metadata to review task objects and test coverage for task targets.

### Why this design was chosen

The checklist is already the summary of what remains before approval, so making each task navigate to its work area reduces analyst hunting without changing any review logic. URL anchors are a small Streamlit-compatible solution that avoids introducing custom routing or a new frontend layer.

### Alternatives rejected

- Rebuilding the app around custom navigation state: rejected because it would be a larger UI rewrite.
- Hiding details behind the checklist: rejected because analysts still need full evidence, validation, NACE, ESG, and export context.
- Automatically switching tabs with unsupported Streamlit internals: rejected because anchor links and highlighted section titles are more stable.

### Tradeoffs remaining

- If a target is inside a non-active Streamlit tab, the highlight appears when that tab is opened.
- Anchor scrolling is browser-dependent but keeps the implementation minimal and non-invasive.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_review_workflow
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Click each approval checklist task and confirm it jumps to, or highlights, the associated review section.

### How this supports the Fitch project requirements

- Makes the human review gate faster and clearer for analysts.
- Keeps the full audit detail available while improving navigation.
- Preserves existing extraction, validation, review, and export behavior.

## 2026-04-30: Batch Validation Issue Review

### What changed

- Added a validation issue selection table in Streamlit.
- Added `Select all validation issues for batch action`.
- Added batch `Acknowledge selected` and `Resolve selected` actions with a shared reviewer note.
- Kept the existing per-issue expanders for detailed message, rationale, and individual actions.
- Added focused test coverage for validation issue table-row context.

### Why this design was chosen

This mirrors the segment-row selection pattern without changing backend review semantics. Batch validation actions still call `ReviewService.mark_validation_issue`, so reconciliation-note rules, validation status checks, and review-event logging remain centralized.

### Alternatives rejected

- Replacing the detailed validation expanders with only a table: rejected because analysts still need full messages and rationale.
- Direct database updates for batch actions: rejected because they would bypass review logging and validation checks.
- Auto-resolving all warnings: rejected because reconciliation and extraction warnings may require analyst judgment.

### Tradeoffs remaining

- Batch acknowledgement of reconciliation mismatches still requires a note; rows without a note report an error rather than being forced through.
- The validation table is a compact triage surface; detailed issue review remains in expanders below it.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_review_workflow
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open a document with validation issues, select several issues in the validation table, enter a batch note, and click acknowledge or resolve. Confirm the detailed issue expanders remain available.

### How this supports the Fitch project requirements

- Speeds analyst review of validation exceptions across large annual reports.
- Keeps review actions auditable through existing review events.
- Preserves human review as the final gate before export.

## 2026-04-30: Approval Checklist and Optional Arbitration Defaults

### What changed

- Added an approval checklist to the Streamlit UI with percent circles for row decisions, required fields, validation issues, NACE mappings, ESG factors, and export readiness.
- Added a selectable checkbox column to the segment review table, plus `Select all`, `Approve selected`, and `Reject selected` batch actions.
- Changed the default arbitration model to `claude-opus-4-7`.
- Disabled optional Opus arbitration by default.
- Made persisted optional arbitration provider/validation errors non-blocking for document approval while keeping them visible as warnings.
- Updated docs and tests for the new review-task and arbitration behavior.

### Why this design was chosen

The user needs a clearer answer to "what is left before approval" without hiding details from Sustainable Fitch analysts. The checklist summarizes the same review state that already gates approval, while detailed evidence, validation, NACE, ESG, and scoring views remain available below.

Batch approve/reject uses the existing `ReviewService` so each row still gets normal review-event logging and required-field validation. Optional arbitration is not part of the core extraction gate, and the observed Anthropic error came from a long optional Opus request, so disabling it by default avoids blocking ordinary document review.

Anthropic's current model overview lists `claude-opus-4-7` as the Claude API ID for Opus 4.7, so that is now the default arbitration model for users who explicitly re-enable arbitration.

### Alternatives rejected

- Auto-approving rows from the checklist: rejected because human review remains the final quality gate.
- Bypassing `ReviewService` for batch updates: rejected because it would skip audit logging and validation.
- Removing arbitration code entirely: rejected because it remains useful as an optional hard-case tool.
- Keeping optional arbitration errors as blocking approval errors: rejected because provider failures should not prevent review of otherwise valid extracted rows.

### Tradeoffs remaining

- The checklist summarizes readiness; analysts still need to inspect detailed tabs for evidence quality.
- Batch approve still fails individual rows with missing required fields and reports those failures rather than forcing approval.
- Re-enabling arbitration can still require streaming support for very long Anthropic requests.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_review_workflow
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_verification_arbitration
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Select a document with multiple rows, use `Select all`, approve/reject selected rows, and confirm the approval checklist percentages update. If an old `llm_arbitration_provider_error` exists, confirm it appears as a warning instead of blocking document approval.

### How this supports the Fitch project requirements

- Makes the human review gate easier to operate at analyst scale.
- Preserves validation, evidence requirements, and review-event audit logging.
- Keeps optional LLM arbitration from blocking final reviewed exports.
- Maintains backwards-compatible export and persistence contracts.

## 2026-04-30: Parallel Folder Manual Metadata Queue

### What changed

- Updated `parallel_folder_pipeline.py` so worker processes return a queued manual-metadata request instead of failing permanently when document metadata cannot be auto-detected.
- Added parent-process console prompts for queued metadata issues after the active worker batch completes.
- Resubmits each affected PDF with the supplied company name, fiscal period, currency, and scale.
- Added focused tests for missing metadata detection, queued manual request shape, retry payload merging, and specific manual-error detection.

### Why this design was chosen

Worker processes should not read from stdin because multiple workers can need input at the same time. Returning manual-input requests to the parent process keeps console interaction serialized, readable, and safe while preserving parallel processing for PDFs that do not need intervention.

The retry reuses the same worker pipeline and passes metadata into ingestion, so extraction, NACE, ESG, validation, and persistence behavior remain unchanged.

### Alternatives rejected

- Prompting directly inside worker processes: rejected because concurrent stdin prompts would interleave and be hard to answer correctly.
- Treating missing metadata as a hard failure: rejected because the user wants to repair these cases and continue the batch.
- Guessing missing metadata to avoid prompts: rejected because wrong company/currency/scale values contaminate downstream exports.
- Adding a separate persistent job queue: rejected as unnecessary for this local console pipeline.

### Tradeoffs remaining

- Manual prompts are handled after the current worker batch finishes, not instantly at the moment a worker reports the issue.
- Console input is still required; non-interactive runs fail queued metadata items if stdin ends.
- The retry reparses the PDF after manual metadata is supplied, which keeps the implementation simple at the cost of extra work for those documents.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_parallel_folder_pipeline
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py
```

Manual verification:

```bash
.venv/bin/python parallel_folder_pipeline.py /path/to/pdf-folder --provider fake --workers 4
```

Use at least one PDF whose company name cannot be auto-detected. Confirm the console prints `WAIT ... queued for manual metadata input`, asks for metadata in sequence, then retries that PDF and continues the pipeline.

### How this supports the Fitch project requirements

- Keeps high-confidence metadata behavior without blocking an entire folder run permanently.
- Preserves deterministic ingestion before LLM extraction.
- Lets analysts supply required document context when automatic detection is not reliable.
- Keeps downstream validation, review, NACE, ESG, and export contracts unchanged.

## 2026-04-30: Analyst-Focused Streamlit UX Cleanup

### What changed

- Renamed the app header and helper copy around a Sustainable Fitch analyst workflow.
- Added example placeholders for upload metadata, reviewer notes, manual row entry, NACE overrides, validation notes, and ESG notes.
- Replaced oversized pipeline metrics with compact step labels and a progress bar.
- Added table column configuration for segment review, NACE candidates, ESG factors, and scoring output so long text remains available without dominating the page.
- Moved long evidence snippets into disabled text areas and put bounding-box JSON behind an expander.
- Added UI-helper tests for compact pipeline labels and progress calculation.

### Why this design was chosen

The existing app already exposed the required review workflow, but some controls lacked examples and large table/text fields could be hard to scan. The new design keeps all analyst-facing details available while making the default page easier to operate for sustainability data teams reviewing annual reports at volume.

The change is intentionally UI-only except for helper functions in `fitch_extractor.ui.review`; it does not alter extraction, validation, persistence, API, export, or review contracts.

### Alternatives rejected

- Rebuilding the UI as a separate frontend: rejected because Streamlit is sufficient for the local prototype and a rewrite would add risk.
- Hiding validation, evidence, ESG, or NACE details: rejected because analysts need auditability and review context.
- Adding decorative visuals: rejected because this is an operational analyst workflow, not a marketing surface.

### Tradeoffs remaining

- Streamlit tables can still require horizontal scrolling for very wide documents.
- The UI is optimized for local analyst review, not concurrent multi-user production review.
- Long report text is now contained better, but page-image evidence overlays remain a documented extension point.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_review_workflow
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open the app, upload a PDF, confirm the sidebar fields show examples, run extraction, inspect the workflow progress bar, edit segment rows, inspect evidence, validation, NACE, ESG, scoring, and export panels.

### How this supports the Fitch project requirements

- Makes the human review gate more usable for Sustainable Fitch analysts.
- Keeps source evidence, validation issues, NACE choices, ESG factors, scoring rationale, and export status visible.
- Preserves deterministic parsing, extraction, validation, and final export behavior.
- Improves review efficiency without reducing audit detail.

## 2026-04-30: High-Confidence Metadata Auto-Detection

### What changed

- Added deterministic ingestion-time metadata inference for company name, fiscal period, currency, and scale.
- Streamlit now allows those upload fields to be left blank and uses inferred values when evidence is clear.
- `scripts/ingest_pdf.py --company-name` is now optional; ingestion stops with a clear error if company name cannot be detected confidently.
- Manual user-entered metadata still overrides inferred values.
- Added tests for blank-field inference, manual override behavior, and non-guessing company-name behavior.

### Why this design was chosen

The user goal was automatic metadata detection with very high accuracy. A near-100% guarantee is not possible for arbitrary PDFs, so the safest design is high precision rather than aggressive guessing: infer only from strong deterministic evidence and require manual input when evidence is weak.

This keeps metadata detection before LLM extraction, avoids introducing a new prompt or dependency, and preserves the review/export contract.

### Alternatives rejected

- LLM-based metadata detection: rejected for this minimal pass because it is less deterministic and can hallucinate plausible company names or units.
- Filename-only company detection: rejected except for company-suffix-backed names because filenames are often abbreviated or vendor-generated.
- Always filling an unknown company value: rejected because a wrong company name would pollute every downstream row and export.
- Changing the database schema to store confidence scores: rejected to keep the change small and backwards compatible.

### Tradeoffs remaining

- Some valid documents will still require manual company input if the first pages do not expose a clear legal/company name.
- Currency and scale are inferred from revenue/segment context when possible, but unusual unit wording can still require review.
- Fiscal period is normalized to `FY<year>` for clear annual-report or year-ended patterns.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_pdf_ingestion
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests
```

Manual verification:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Upload a PDF and leave company name, fiscal period, currency, and scale blank. Confirm clear documents are populated into the stored document record, and ambiguous documents stop with a message asking for company name instead of guessing.

### How this supports the Fitch project requirements

- Keeps deterministic parsing and metadata retrieval before LLM calls.
- Improves upload ergonomics without weakening validation or review.
- Avoids low-confidence guesses that could damage final exports.
- Preserves backwards compatibility because manually supplied metadata and existing document fields still work.

## 2026-04-30: Final Reliability and Submission Readiness Pass

### What changed

- Added a top-level `README.md` with setup, environment variables, CLI/UI run commands, review/export/evaluation instructions, and limitations.
- Added final submission docs: `docs/SOLUTION_DESCRIPTION.md`, `docs/PERFORMANCE_ANALYSIS.md`, `docs/REFLECTION.md`, `docs/DEMO_SCRIPT.md`, `docs/MANUAL_TEST_CHECKLIST.md`, and `docs/PROMPTS.md`.
- Added `scripts/export_document.py` so approved documents can be exported from the command line.
- Added CLI smoke tests for init, ingest, fake extraction, review approval, final export, and blocked unapproved export.
- Added provider redaction coverage so API-key-like values are removed from provider error messages.
- Added `.gitignore` entries for local environments, generated Python bytecode, local SQLite databases, uploads, exports, and reports.
- Updated Streamlit provider selection so fake mode is explicitly labeled as a deterministic smoke-test provider instead of an invisible default.

### Why this design was chosen

The project already had the core hybrid pipeline, review gate, and export service. The final pass focused on making those capabilities easy to run and demonstrate without changing the established data contracts. A small export CLI reuses `ExportService`, so the same approval gate protects both UI and command-line exports.

Provider redaction is centralized in the Anthropic provider path because provider exceptions are the only place where low-level API error text can enter app-visible validation issues or Streamlit warnings.

### Alternatives rejected

- Adding a separate backend web framework for final submission: rejected because the Streamlit UI and CLI already cover the prototype workflow.
- Making fake extraction the production default: rejected because fake mode is useful for tests and smoke demos but should not be mistaken for real extraction quality.
- Writing separate export logic for the CLI: rejected because it could drift from the UI/API export gate.
- Reporting unmeasured performance numbers: rejected because no labeled gold set is included in this checkout.

### Tradeoffs remaining

- Streamlit remains a local prototype UI rather than a multi-user production review system.
- Performance metrics require a labeled gold set and generated reviewed exports.
- Fake mode is still available for class demos without credentials, but it is clearly labeled and documented as non-quality-bearing.
- Generated local files are ignored, but this workspace is not a Git checkout, so cleanup must be verified with filesystem checks rather than Git status.

### How to test or verify the change

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_cli_smoke
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_provider_security
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q fitch_extractor streamlit_app.py scripts tests
```

Manual verification:

```bash
.venv/bin/python scripts/manage_db.py --reset
.venv/bin/streamlit run streamlit_app.py
```

Upload a PDF, run analysis, review rows/evidence/validation, approve the document, export files, and run the evaluator against a labeled gold set.

### How this supports the Fitch project requirements

- Keeps deterministic parsing and retrieval before LLM calls.
- Preserves strict LLM schemas, normalization, validation, and human review.
- Ensures final export is unavailable until all rows are approved, edited, or rejected and the document is approved.
- Keeps rejected rows out of final CSV/XLSX while retaining audit JSON history.
- Documents the workflow, prompt inventory, evaluation process, limitations, demo flow, and manual test checklist needed for class submission.

## 2026-04-30: Evaluation Harness and Reporting Workflow

### What changed

- Added `fitch_extractor.evaluation` for gold-set loading, prediction loading, row matching, metric calculation, failure classification, and report rendering.
- Added `python -m fitch_extractor.evaluate` as the CLI entrypoint for comparing CSV/JSON gold files with exported prediction files or export directories.
- Added generated report outputs under `reports/`: `evaluation_summary.md`, `evaluation_results.csv`, and `failure_analysis.md`.
- Added focused unit and integration tests for matching, metrics, failure taxonomy, and report generation.
- Added `docs/EVALUATION.md` and updated export/testing documentation for the evaluation workflow.

### Why this design was chosen

The evaluator is a separate deterministic workflow that consumes existing reviewed exports instead of changing the extraction, review, or final export contracts. This keeps evaluation reproducible and avoids mixing benchmark logic into the production-facing review/export path.

The loader prefers `audit_export.json` when available because it exposes validation issues, review events, reconciliation signals, and timestamps. It still supports `revenue_segments.csv` so older or narrower exports can be evaluated for row-level quality.

### Alternatives rejected

- Running live extraction inside the evaluator: rejected because evaluation should measure a fixed prediction artifact and avoid prompt/provider variability.
- Treating any sample CSV as ground truth automatically: rejected because only explicitly labeled gold files should drive metrics.
- Adding a new dependency for fuzzy matching: rejected because the standard-library similarity is sufficient for the small prototype gold sets.
- Hard-coding sample results into markdown reports: rejected because reports must be generated from the supplied inputs.
- Changing the export schema to satisfy evaluation: rejected because the existing frontend/API/export contract should remain stable.

### Tradeoffs remaining

- Segment matching uses configurable heuristics and should be reviewed on early gold sets to tune thresholds.
- Some failure types, such as OCR, non-English, ESG over-linking, and NACE ambiguity, need analyst notes or audit evidence because they cannot always be inferred from row values alone.
- CSV predictions cannot provide all rich metrics; use JSON audit exports for validation issue counts, reconciliation pass rate, and timing.
- The evaluator reports measured prototype performance only for the supplied labeled files, not general model performance.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest tests.test_evaluation
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor streamlit_app.py scripts tests
```

Manual verification:

```bash
.venv/bin/python -m fitch_extractor.evaluate --gold 'data/gold/*.csv' --pred exports
```

Confirm that `reports/evaluation_summary.md`, `reports/evaluation_results.csv`, and `reports/failure_analysis.md` are regenerated from the selected inputs.

### How this supports the Fitch project requirements

- Measures extraction quality, reconciliation, reviewer effort, validation issues, and common failure modes against manually labeled gold data.
- Keeps deterministic Python normalization, matching, and validation after extraction.
- Preserves human review as the final quality gate by evaluating reviewed export artifacts.
- Maintains backwards compatibility with the existing export files.
- Gives analysts a documented way to create small gold sets without claiming unmeasured model performance.

## 2026-04-30: Anthropic Model Configuration for NACE Mapping

### What changed

- Updated the default Anthropic model from `claude-3-5-sonnet-20241022` to `claude-sonnet-4-5-20250929`.
- Changed `NaceMappingService` to use the shared extraction model default instead of its own stale default.
- Changed Streamlit NACE mapping to pass the configured `ExtractionSettings.model` into `NaceMappingService`.
- Added `LLMProviderError` handling to the Streamlit NACE mapping button so provider failures show as warnings instead of crashing the app.
- Improved Anthropic provider errors to include the requested model name and provider error type.
- Updated LLM/API docs and added a test that NACE mapping defaults to the shared configured model.

### Why this design was chosen

The crash was caused by Anthropic returning `NotFoundError`, which is consistent with an unavailable model identifier. NACE mapping had a separate hard-coded default (`claude-3-5-haiku-latest`) and Streamlit did not pass the configured model, so changing `FITCH_EXTRACTION_MODEL` did not reliably control NACE mapping.

Using the shared extraction model keeps the local prototype easier to configure and avoids hidden model defaults across pipeline stages.

### Alternatives rejected

- Swallowing provider errors silently: rejected because analysts need to know when real LLM mapping did not run.
- Leaving NACE on a separate hard-coded model: rejected because it creates stale defaults and confusing failures.
- Retrying with fake mappings after an Anthropic failure: rejected because it would hide that the requested LLM run failed.

### Tradeoffs remaining

- Anthropic account access can still vary by model. If `claude-sonnet-4-5-20250929` is unavailable for an account, set `FITCH_EXTRACTION_MODEL` to a model returned by Anthropic's Models API.
- Streamlit shows provider failures as warnings; it does not automatically diagnose account/model availability.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest tests.test_nace_mapping
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor streamlit_app.py scripts tests
```

Manual verification:

```bash
export FITCH_EXTRACTION_PROVIDER=anthropic
export FITCH_EXTRACTION_MODEL=claude-sonnet-4-5-20250929
.venv/bin/streamlit run streamlit_app.py
```

Click `Run NACE mapping`. If Anthropic rejects the model, the UI should show a warning with the model name instead of a traceback.

### How this supports the Fitch project requirements

- Keeps the LLM-based NACE workflow configurable and debuggable.
- Avoids hidden stale model names in the review UI.
- Preserves human review workflow stability even when a provider call fails.

## 2026-04-30: LLM-Based NACE Mapping Workflow

### What changed

- Replaced the prior NACE rerank-only workflow with a broader `nace_mapping_v2` LLM classification step.
- Added nearby parsed-page context, company name, document name, and segment evidence to NACE mapping prompts.
- Added strict LLM decisions: `mapped`, `not_applicable`, and `needs_review`.
- Added not-applicable handling for totals, eliminations, roll-ups, reclassifications, reconciling items, and hedging rows.
- Added domain-hint candidate expansion for obvious power-generation, electricity distribution, transmission, gas, heat/steam, and cloud/hosting contexts.
- Changed automatic selection so it only stores high-confidence mapped decisions and preserves reviewer overrides.
- Updated Streamlit so fake mode still exercises the LLM-mapping path locally and Anthropic mode uses the real provider.
- Added tests for context-assisted mapping, not-applicable clearing, reviewer override preservation, and strict invented-code rejection.

### Why this design was chosen

The previous workflow let weak deterministic matches become selected NACE mappings. It also allowed generic terms such as `activities`, `operator`, `reported`, and `total` to drive mappings. The new design still retrieves candidates deterministically, but uses the LLM for the actual classification decision and requires it to explain whether a row should be mapped at all.

The LLM is constrained to a supplied candidate list so it cannot invent NACE codes. Candidate expansion and nearby context improve the odds that the right code is available before the LLM classifies the segment.

### Alternatives rejected

- Letting the LLM choose any NACE code from memory: rejected because it can hallucinate invalid or unsupported codes.
- Keeping deterministic top-candidate auto-selection: rejected because the current stored mappings showed clear false positives.
- Mapping total and elimination rows to operating NACE codes: rejected because those rows are reconciliation rows, not business activities.
- Overwriting reviewer overrides on rerun: rejected because human review remains the final quality gate.
- Adding a new NACE status table now: rejected to keep the change compatible with the existing UI/API/export contract.

### Tradeoffs remaining

- Domain hints are intentionally narrow and should be expanded only when supported by tests and real examples.
- The LLM can only choose from retrieved candidates, so retrieval quality still matters.
- `not_applicable` and `needs_review` decisions are represented by no automatic selection rather than a dedicated status table.
- Fake provider behavior is deterministic for tests; real mapping quality should be evaluated with Anthropic mode and reviewed by a human.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest tests.test_nace_mapping
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor streamlit_app.py scripts tests
```

Manual review flow:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Select a document, click `Run NACE mapping`, inspect the NACE candidates for each segment, and confirm total/elimination rows do not receive automatic selected codes. Use the reviewer accept/override controls for any remaining ambiguous row.

### How this supports the Fitch project requirements

- Keeps deterministic retrieval before LLM classification.
- Uses strict schemas for LLM output.
- Uses Python validation to reject invented NACE codes and low-confidence automatic selections.
- Keeps human review as the final quality gate.
- Avoids hiding unsupported mappings in final exports.
- Improves the foundation for score quality because scoring relies on reviewed NACE mappings.

## 2026-04-30: Prototype Scoring Layer

### What changed

- Added `config/scoring_rules.yaml` for prototype NACE base scores, scale bounds, and ESG adjustment rules.
- Added `fitch_extractor/scoring.py` to compute segment scores and company weighted-average scores.
- Added persisted `CompanyScore` records and repository methods for replacing latest document scores.
- Updated exports to recompute and include prototype score fields and score audit records.
- Added a Streamlit `Scoring` tab with a prototype-only warning and score table.
- Added scoring tests for base lookup, ESG adjustments, caps, revenue weights, total row exclusion, company average, missing NACE fallback, and export score fields.

### Why this design was chosen

The scoring layer is deterministic and transparent. It reads reviewed extraction, NACE, and ESG data from the repository, applies config-driven rules, caps scores in Python, persists the calculation output, and exposes rationales for review and export.

The model is intentionally labeled as a class/project prototype because it is not an official Fitch Ratings or Sustainable Fitch methodology. Keeping rules in config makes score assumptions easy to inspect and adjust without hiding logic in prompts.

### Alternatives rejected

- LLM-generated scoring: rejected because scoring must be transparent, deterministic, and schema/config driven.
- Hard-coding all NACE and ESG rules in Python: rejected because scoring assumptions should be editable for the prototype demonstration.
- Scoring rejected or pending rows by default: rejected because review remains the final quality gate.
- Counting total rows as scored segments: rejected because it would double-count revenue.
- Adding a migration framework now: rejected because the current SQLite setup can add the new `company_scores` table through the existing initializer.

### Tradeoffs remaining

- The YAML file is JSON-compatible YAML to avoid adding a parser dependency; it is editable but stricter than general YAML.
- Base scores are illustrative prototype assumptions and need subject-matter calibration before any real use.
- Company-wide ESG factors are excluded from segment adjustments unless a reviewer links them to a segment.
- Existing historical score rows are replaced per document so the UI/export show the latest calculation rather than a full score history.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest tests.test_scoring_service
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor streamlit_app.py scripts tests
```

Manual review flow:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open a reviewed document, go to `Scoring`, click `Compute prototype scores`, inspect segment and company scores, then approve/export the document and confirm score fields appear in CSV/XLSX/JSON.

### How this supports the Fitch project requirements

- Keeps scoring separate from extraction and review.
- Uses deterministic NACE and revenue data before any scoring output.
- Applies reviewed ESG factors as transparent adjustments with rationale.
- Excludes rejected and unreviewed rows from final persisted scoring.
- Handles total rows as denominators rather than scored segments.
- Adds score fields to final exports while preserving the export review gate.

## 2026-04-30: ESG Factor Extraction Extension

### What changed

- Added deterministic ESG candidate-page retrieval in `fitch_extractor/extraction/esg.py`.
- Added strict Pydantic ESG extraction schemas and a versioned `esg_extraction_v1` prompt.
- Added fake-provider ESG output for local tests without live LLM calls.
- Added deterministic segment-link enforcement before ESG factors are persisted.
- Added review-service actions to edit, unlink, relink, approve, and reject ESG factors through review events.
- Added an ESG tab to the Streamlit review app.
- Added approved/edited segment-linked ESG summaries to CSV/XLSX export and full ESG records to JSON audit export.
- Added focused ESG tests and updated API/data/export/testing docs.

### Why this design was chosen

The project requires a hybrid pipeline. ESG extraction now follows the same pattern as revenue extraction: deterministic retrieval first, a narrow LLM prompt second, strict schema validation, Python post-processing, persistence, and human review.

ESG status is tracked through `ReviewEvent` records instead of adding a new status column to the existing `esg_factors` table. This preserves the current storage contract while still making approvals, edits, link changes, and rejections auditable.

### Alternatives rejected

- Sending the whole report to the LLM: rejected because the pipeline must avoid full-report prompt-only extraction.
- Automatically attaching company-wide ESG factors to every segment: rejected because the core ESG rule requires explicit linkage.
- Trusting LLM segment linkage directly: rejected because Python post-processing must downgrade uncertain links to company-wide context.
- Adding ESG scoring in this change: rejected because ESG factors should not override core revenue extraction and scoring is a separate prototype step.
- Adding a new ORM or migration framework: rejected because the existing SQLite repository is explicit and sufficient for this extension.

### Tradeoffs remaining

- Segment activity matching is conservative and may leave some valid factors company-wide for reviewer relinking.
- ESG factor review status is event-derived, so consumers that need status must read review events or use `ReviewService.get_document_review_state`.
- The fake provider is deterministic and useful for tests, but it is not a substitute for real extraction quality evaluation against annual reports.
- Governance/company-wide policy factors are filtered unless there is material evidence; edge cases may require reviewer judgment.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor tests streamlit_app.py
```

Manual review flow:

```bash
.venv/bin/streamlit run streamlit_app.py
```

Open a document, use `Run ESG extraction`, review factors on the `ESG Factors` tab, edit/unlink/relink as needed, approve or reject each factor, then export after the document review gate passes.

### How this supports the Fitch project requirements

- Keeps deterministic retrieval before LLM calls and avoids full-report prompts.
- Uses strict schemas for LLM ESG output and fake-provider tests.
- Enforces the rule that ESG can link to a segment only when evidence explicitly supports the link.
- Stores company-wide ESG separately from segment-linked ESG.
- Keeps review as the final quality gate and logs review events.
- Includes ESG in final exports without allowing ESG to override revenue extraction.

## 2026-04-30: Repository Audit and Planning Baseline

### What changed

- Added project planning and contract documentation under `docs/`.
- Added a minimal smoke test scaffold under `tests/`.
- Did not implement the backend extraction pipeline, frontend, API, persistence layer, NACE mapping, ESG extraction, scoring, or export logic.

### Why this design was chosen

The workspace currently contains no application code or project metadata. The safest production-minded step is to document the absence of a baseline, define target contracts, and add a minimal verification scaffold without inventing behavior that could later conflict with the real application design.

This keeps the repository ready for staged implementation while honoring the instruction not to perform the full backend overhaul in this prompt.

### Alternatives rejected

- Building a full backend skeleton now: rejected because the prompt requested audit, cleanup, and planning only.
- Creating speculative API routes: rejected because no current API contract exists to preserve.
- Adding dependencies or framework configuration: rejected because there is no existing package convention to align with yet.
- Creating placeholder extraction, NACE, ESG, scoring, or export modules: rejected because placeholder production code would obscure the real implementation work and violate the no-dead-code requirement.

### Tradeoffs remaining

- The docs describe target contracts and implementation phases, but no extraction behavior exists yet.
- There is no current app startup command because no app source is present.
- The smoke test only verifies repository scaffolding and required docs; it does not validate domain behavior.
- Future implementation still needs to choose the app shape, dependency management, persistence model, and API framework.

### How to test or verify the change

Run:

```bash
python3 -m unittest discover -s tests
```

Expected result: the smoke test passes and verifies that the required planning and contract docs exist.

### How this supports the Fitch project requirements

- Documents the required hybrid pipeline design before implementation begins.
- Preserves contract discipline by explicitly stating that no current API contract exists in this workspace.
- Defines target entities and export columns that future code should satisfy.
- Establishes human review and export gating as core architecture requirements.
- Creates a staged plan that prioritizes deterministic parsing, strict schemas, Python validation, and test coverage before LLM-dependent extraction.

## 2026-04-30: Internal Data Model and SQLite Persistence

### What changed

- Added frozen internal dataclasses for all required Fitch pipeline entities in `fitch_extractor/models.py`.
- Added Pydantic 2 response schemas in `fitch_extractor/api/schemas.py`.
- Added SQLite initialization/reset helpers in `fitch_extractor/persistence/database.py`.
- Added `SQLiteRepository` CRUD-style persistence methods in `fitch_extractor/persistence/repository.py`.
- Added `ReviewService` for review status changes and review event logging.
- Added `scripts/manage_db.py` for local database initialization and reset.
- Added `requirements.txt` with the required Pydantic dependency.
- Added unit tests for serialization, CRUD flows, related entity persistence, review event logging, export gate enforcement, and database initialization/reset.

### Why this design was chosen

The workspace had no existing persistence approach. SQLite is the smallest reasonable local persistence layer for a production-minded prototype because it is durable, inspectable, works without a server, and supports foreign keys for the document-to-segment data graph.

Internal dataclasses keep domain state independent from API serialization. Pydantic schemas are kept in a separate API package so future routes can return validated response models without coupling HTTP behavior to SQLite row handling.

Repository methods accept injectable ID and clock functions. This keeps production IDs unique while making tests deterministic.

### Alternatives rejected

- In-memory-only persistence: rejected because the prototype needs review, validation, and export state across local runs.
- JSON files as the primary store: rejected because the entity graph has many relationships and review events; SQLite gives safer referential integrity with little overhead.
- SQLAlchemy or another ORM: rejected because there is no existing dependency stack and the current schema is small enough for explicit SQL to remain readable.
- Pydantic as the internal model layer: rejected to keep API-facing serialization separate from persistence/domain objects.
- Implementing LLM extraction, NACE/ESG algorithms, scoring, or final file export now: rejected because this prompt is scoped to the internal model and persistence layer.

### Tradeoffs remaining

- The persistence layer stores NACE, ESG, and score records, but does not compute them yet.
- Export records are metadata only; CSV/XLSX/JSON writing is still future work.
- There are no HTTP routes yet, only API-facing schemas.
- The schema uses an initialization script rather than a full migration framework. This is acceptable for the first local prototype schema but should be revisited if multiple schema versions need to be upgraded in place.
- Pydantic is now a required dependency, so tests should run inside a virtual environment on externally managed Python installations.

### How to test or verify the change

Create a virtual environment, install dependencies, and run the tests:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests
```

Initialize or reset the local SQLite database:

```bash
python scripts/manage_db.py
python scripts/manage_db.py --reset
```

### How this supports the Fitch project requirements

- Creates durable internal records for documents, parsed pages, candidate pages, segment rows, evidence, validation issues, NACE candidates, ESG factors, scores, review events, and export metadata.
- Keeps deterministic parsing/retrieval outputs (`ParsedPage`, `PageCandidate`) separate from later LLM extraction outputs (`SegmentRow`, `SegmentEvidence`).
- Preserves strict API serialization through Pydantic response schemas.
- Supports Python normalization and validation by storing raw revenue, normalized value, currency, scale, period, confidence, and validation issues separately.
- Makes human review explicit with segment statuses and immutable review event records.
- Enforces the export review gate before export metadata can be created.

## 2026-04-30: PDF Ingestion and Page Retrieval

### What changed

- Added a deterministic PDF ingestion layer under `fitch_extractor/ingestion/`.
- Added PyMuPDF page text, block, page-dimension, bounding-box, and PNG rendering support.
- Added pdfplumber table extraction with row and cell structure where pdfplumber can provide it.
- Added weak/no-text page detection and an explicit `PageTextFallback` extension point for future OCR or vision text.
- Added deterministic page relevance scoring and candidate selection with adjacent-page inclusion.
- Added approximate evidence snippet lookup against parsed PyMuPDF text-block bounding boxes.
- Added `scripts/ingest_pdf.py` to register a PDF, parse it, persist parsed pages/candidates, and print a JSON summary.
- Added `IngestionSummaryResponse` for future API reuse.
- Added parser/retrieval tests and the new `docs/PARSING_AND_RETRIEVAL.md` guide.

### Why this design was chosen

The project requires a hybrid pipeline that avoids sending whole PDFs to an LLM. A small ingestion package keeps deterministic parsing, scoring, and evidence lookup separate from persistence and future LLM extraction. PyMuPDF is used for fast page text and geometry; pdfplumber is used only for table detection and table structure, which keeps each parser focused on its strength.

The existing SQLite `ParsedPage` and `PageCandidate` entities already matched the needed storage boundary, so the change adds repository convenience methods instead of changing the database schema.

### Alternatives rejected

- Prompt-only PDF extraction: rejected because the project requires deterministic parsing and retrieval before LLM calls.
- OCR in this prompt: rejected because the task explicitly says not to implement full OCR yet.
- Adding a language-detection dependency: rejected because reliable language detection would add dependency weight and ambiguity; pages currently store `language = "unknown"` until a vetted detector is introduced.
- Sending all pages downstream: rejected because candidate ranking is required to limit later LLM context.
- Creating an HTTP server now: rejected because no API framework exists yet; the CLI command provides the required ingestion integration without introducing a new web stack.

### Tradeoffs remaining

- Bounding boxes are block-level approximations, not exact word-level proof.
- pdfplumber table cell geometry depends on the PDF's table lines and layout; not every visual table will produce structured cells.
- No OCR or vision fallback is active yet, so image-only pages are marked `has_text = false`.
- Language is stored as `unknown` until a lightweight, reliable detector is selected.
- Candidate scoring is deterministic and explainable, but it is heuristic and will need tuning against real annual reports.

### How to test or verify the change

Install dependencies and run the full test suite:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests
```

Ingest a local PDF:

```bash
python scripts/ingest_pdf.py /path/to/annual-report.pdf --company-name "Example Corp"
```

Expected result: JSON with document metadata, page counts, no-text pages, and ranked candidate pages with scores, matched signals, and reasons.

### How this supports the Fitch project requirements

- Establishes deterministic PDF parsing before any LLM extraction.
- Persists page text, tables, bounding boxes, and candidate rankings in SQLite.
- Marks weak/no-text pages without pretending OCR has run.
- Keeps future LLM extraction scoped to top candidate pages instead of whole documents.
- Preserves source page references and evidence geometry for later human review.
- Leaves LLM extraction, review UI, ESG, NACE, scoring, and final export out of scope as requested.

## 2026-04-30: First-Pass LLM Revenue Extraction

### What changed

- Added `fitch_extractor/extraction/` with strict Pydantic output schemas, a versioned first-pass prompt builder, provider interfaces, fake and Anthropic providers, conservative deduplication, configuration, and an extraction service.
- Added `scripts/extract_revenue_segments.py` to run extraction for an already ingested document.
- Added repository helpers to create segment evidence and validation issues.
- Added an API-facing `ExtractionSummaryResponse`.
- Added tests for prompt construction, schema validation, fake provider extraction, invalid JSON handling, row/evidence persistence, deduplication, and parse-to-extract integration.
- Added `docs/LLM_EXTRACTION.md`.

### Why this design was chosen

The extraction layer is separated from ingestion and persistence so deterministic parsing and candidate ranking remain the first step. Business logic depends on a narrow `LLMProvider` protocol rather than directly importing a vendor client, which keeps tests deterministic and prevents accidental live API calls.

The fake provider reads the same prompt shape as real providers and extracts from table rows deterministically. This gives useful local and CI coverage without requiring Anthropic credentials.

### Alternatives rejected

- Calling Anthropic directly from the extraction service: rejected because it would couple business logic to infrastructure and make tests unsafe.
- A single prompt-only PDF workflow: rejected because the project requires deterministic parsing/retrieval before LLM calls.
- Aggressive deduplication by segment and value only: rejected because rows with different evidence should remain reviewable.
- Adding second-pass verification, Opus arbitration, NACE, ESG, or scoring now: rejected because this prompt is scoped to first-pass revenue extraction.

### Tradeoffs remaining

- The Anthropic provider relies on prompt-enforced JSON rather than a provider-native schema mode.
- The first-pass service stores extraction notes only indirectly through validation issues and evidence because the current segment row table has no notes column.
- Deduplication is intentionally conservative and may leave near-duplicates for human review.
- Evidence bounding boxes remain block-level approximations from deterministic parsing.
- No HTTP route or frontend is implemented yet; extraction is available through Python service classes and a CLI.

### How to test or verify the change

Run:

```bash
python -m unittest discover -s tests
```

Ingest and extract with the fake provider:

```bash
python scripts/ingest_pdf.py /path/to/annual-report.pdf --company-name "Example Corp"
python scripts/extract_revenue_segments.py doc_... --provider fake
```

### How this supports the Fitch project requirements

- Keeps the hybrid pipeline intact: PDF parsing and candidate ranking happen before LLM extraction.
- Uses strict Pydantic schemas for LLM output and converts invalid output into validation issues.
- Supports a fake provider for tests and an Anthropic provider for real local runs.
- Persists first-pass rows as `pending` with evidence so human review remains the final quality gate.
- Avoids NACE, ESG, scoring, second-pass verification, and arbitration work until later phases.

## 2026-04-30: Extraction Candidate Filtering for Alliander False Positive

### What changed

- Added an extraction-stage candidate filter that only sends pages with explicit revenue-segment anchors to the LLM.
- Added a preference for financial statement segment-note pages when they are present, so MD&A summary tables, product/geographical segmentation pages, and unrelated sustainability tables are not mixed into the same extraction run.
- Added deterministic rejection for provider rows whose `page_ref` is outside the prompt bundle.
- Added deterministic rejection for rows that appear to come from ESG, EU taxonomy, climate-impact, energy-intensity, energy-use, emissions, fuel-consumption, green-financing, or similar non-segment disclosures.
- Tightened the first-pass prompt to explicitly reject ESG/taxonomy/energy-use tables, distinguish product/geographical segmentation from operating segments, and prefer financial statement note disclosures.
- Added regression tests for the Alliander-style page 161 false positive and page 257 financial statement segment note.

### Why this design was chosen

The Alliander run showed that page 257 was parsed and ranked, but page 161 was also sent to extraction because generic table, numeric, currency, and business-line signals made it look useful. The bug was not PDF parsing; it was the extraction boundary accepting weak candidates that lacked explicit revenue-segment anchors.

Filtering at extraction time keeps ingestion recall high while making the LLM context more precise. Post-validation then protects persistence if a provider still returns an unsupported page or non-segment disclosure.

### Alternatives rejected

- Lowering page 161's ingestion score only: rejected because ingestion should remain recall-oriented and other documents may need broad candidate capture.
- Depending only on stronger prompt language: rejected because the incorrect rows were already a prompt-following failure mode and need deterministic protection.
- Hard-coding Alliander page numbers: rejected because the fix must generalize beyond this PDF.
- Removing all MD&A segment pages unconditionally: rejected because some filings may only expose useful segment revenue tables outside formal note sections.

### Tradeoffs remaining

- The financial-statement preference may skip a useful MD&A duplicate or product segmentation page when a financial statement operating-segment note is available. This is intentional for first-pass precision.
- Candidate filtering is still heuristic and should be tuned with more annual-report fixtures.
- Existing bad rows already persisted in a local database are not automatically deleted; reruns should start from a cleared document/extraction state or a reset database.
- A real Anthropic rerun was not performed in this workspace because `ANTHROPIC_API_KEY` was not set.

### How to test or verify the change

Run:

```bash
python -m unittest discover -s tests
python -m compileall fitch_extractor scripts tests
```

For the stored Alliander document, extraction candidate selection should produce pages `[256, 257]`, and the prompt bundle should contain pages 256/257 with no page 161 or page 258.

### How this supports the Fitch project requirements

- Preserves the hybrid pipeline by keeping deterministic retrieval before LLM calls.
- Improves first-pass extraction precision without reducing parsed page storage.
- Prevents ESG/taxonomy/energy-use disclosures from being persisted as business/reportable/operating revenue segments.
- Keeps invalid or unsupported provider output recoverable through validation issues rather than crashes.

## 2026-04-30: Tolerant JSON Boundary Parsing for Anthropic Output

### What changed

- Added `fitch_extractor/extraction/json_response.py` to extract the first complete JSON object from provider responses before strict Pydantic validation.
- The parser accepts plain JSON, JSON wrapped in Markdown code fences, and JSON preceded/followed by short provider prose.
- Truly empty, non-JSON, or incomplete JSON responses still create `llm_output_validation` issues and do not persist rows.
- Added an Anthropic system instruction requiring a single JSON object with no Markdown or prose.
- Added tests for Markdown-wrapped JSON and non-JSON provider responses.

### Why this design was chosen

The Anthropic run returned content that failed at JSON parsing before schema validation. Since provider responses may include formatting despite prompt instructions, the extraction boundary now performs a narrow JSON-boundary cleanup and then applies the same strict schema. This preserves strict output validation while avoiding false failures caused only by code fences or surrounding text.

### Alternatives rejected

- Accepting arbitrary malformed JSON: rejected because it would weaken the schema contract and risk storing guessed data.
- Storing raw provider responses in the database for debugging: rejected because this could persist large or sensitive model output and would add debug artifacts.
- Depending only on prompt wording: rejected because the observed failure showed the provider can still produce non-raw JSON.

### Tradeoffs remaining

- Responses with no JSON object are still rejected.
- Incomplete JSON from token truncation is still rejected; increase `FITCH_EXTRACTION_MAX_TOKENS` if that appears.
- The parser extracts the first complete JSON object, so providers must still return one object matching `RevenueExtractionOutput`.

### How to test or verify the change

Run:

```bash
python -m unittest discover -s tests
python -m compileall fitch_extractor scripts tests
```

Then rerun extraction for the ingested document. Markdown-wrapped JSON should now validate and persist rows; non-JSON responses should remain visible as validation issues.

### How this supports the Fitch project requirements

- Keeps strict schemas as the source of truth for extraction output.
- Handles provider formatting errors without crashing the app.
- Does not require live API calls in tests.
- Keeps failed provider output recoverable through validation issues and avoids persisting unvalidated rows.

## 2026-04-30: Complete Primary Segmentation Column Extraction

### What changed

- Tightened the first-pass prompt so primary segmentation and segment information tables return one row for each current-period segment-table column.
- Explicitly included reconciliation columns such as `Eliminations`, `Total`, `Reclassification to reported and incidental items`, and `Reported` when they are part of the segment revenue table.
- Instructed the model to use exact table column headers as `segment_name` instead of adding explanatory qualifiers.
- Instructed the model to prefer the current fiscal/reporting period when a table presents multiple years.
- Added tests that validate and persist all six Alliander-style primary segmentation columns, including a dash/null revenue value for `Eliminations`.

### Why this design was chosen

The Alliander table is column-oriented: the revenue metric is the row (`External income`) and the reportable/reconciliation items are columns. The previous prompt was too focused on true operating segments and did not make it clear that segment-table reconciliation columns must also be extracted for review. Capturing the full current-period table column set gives reviewers the complete segment revenue bridge.

### Alternatives rejected

- Hard-coding Alliander-specific segment names in code: rejected because other annual reports will use different segment and reconciliation labels.
- Treating `Eliminations` as a validation-only issue: rejected because the user needs it as a reviewable row even when its revenue cell is a dash.
- Extracting every historical year by default: rejected because it creates duplicate-looking rows for annual-report first pass; prior years should be extracted only when the current period is absent or a later feature requests multi-period output.

### Tradeoffs remaining

- A dash value is persisted with `revenue_raw = "-"` and `revenue_value = null`, which creates a warning for review.
- The fix relies on the LLM correctly reading column-oriented tables from parsed page text; exact table reconstruction remains a future parsing improvement.
- Existing partial rows already stored in SQLite are not deleted automatically.

### How to test or verify the change

Run:

```bash
python -m unittest discover -s tests
python -m compileall fitch_extractor scripts tests
```

Then rerun extraction after clearing prior partial rows or resetting/reingesting the document. For Alliander page 257, the expected current-period segment names are `Network operator Liander`, `Other`, `Eliminations`, `Total`, `Reclassification to reported and incidental items`, and `Reported`.

### How this supports the Fitch project requirements

- Keeps first-pass extraction reviewable and evidence-linked.
- Preserves raw table values exactly, including dash cells.
- Maintains strict schema validation while broadening coverage of the complete segment revenue reconciliation table.
- Avoids NACE, ESG, scoring, and second-pass verification work in this prompt.

## 2026-04-30: Batch Extraction Candidate Cleanup

### What changed

- Added table-of-contents detection to exclude TOC/index pages from extraction prompts.
- Added accounting-update page filtering so pages about adopted segment-reporting standards are not treated as segment revenue disclosures.
- Added credit-risk/default-portfolio style terms to non-segment page filtering.
- Added context expansion so a selected segment-note intro page can include the following parsed page when the next page contains the revenue table.
- Changed `no_extraction_eligible_candidate_pages` from an error to a warning when parsed pages exist, because this is often an expected no-data outcome for Pillar 3/regulatory documents rather than a technical failure.
- Increased the default Anthropic extraction output budget from 4,000 to 8,000 tokens to reduce incomplete JSON failures on long segment tables.
- Added regression tests for TOC filtering, accounting-update filtering, adjacent continuation page inclusion, and no-eligible-candidate warning severity.

### Why this design was chosen

The batch run showed several extraction failures were caused by noisy but plausible candidate pages: table-of-contents pages, accounting-standard update pages, risk tables, and segment-note intro pages without the actual table. Fixing this at the extraction boundary keeps ingestion recall high while preventing the LLM from being asked to infer segment rows from references or unrelated disclosures.

Adjacent page inclusion is intentionally narrow: it only adds the next page when the selected page looks like a segment-note intro and does not already contain a revenue table. This helps multi-page notes such as Commerzbank and Swiss Prime Site without reopening broad whole-document prompting.

### Alternatives rejected

- Hard-coding page numbers or document names from the batch: rejected because the fix must generalize.
- Lowering ingestion scores for TOC pages only: rejected because ingestion should preserve broad candidates for review and later tuning.
- Treating every no-row document as an error: rejected because regulatory Pillar 3 documents may legitimately have no segment revenue table.
- Increasing bundle size broadly for every prompt: rejected because it would reintroduce unrelated context and token pressure.

### Tradeoffs remaining

- Candidate filtering remains heuristic and should be tuned with more batch fixtures.
- Some documents may still need OCR or better table reconstruction for complete extraction.
- Existing rows and validation issues in SQLite are not automatically cleaned up; reruns should reset or clear prior extraction outputs for a document.
- Long outputs can still truncate if a provider returns excessive prose or too many rows; the strict parser will continue to reject incomplete JSON.

### How to test or verify the change

Run:

```bash
python -m unittest discover -s tests
python -m compileall fitch_extractor scripts tests
```

For the stored batch data, candidate selection should now exclude Swiss Prime Site page 219, Citi page 10, and JPMorgan page 199; Commerzbank page 391 should prompt with page 392.

### How this supports the Fitch project requirements

- Keeps deterministic retrieval before LLM calls while making extraction prompts more precise.
- Reduces hallucinated rows from TOC and non-revenue pages.
- Improves recoverability by distinguishing expected no-data outcomes from technical errors.
- Keeps all changes in first-pass extraction and does not introduce NACE, ESG, scoring, or second-pass verification.

## 2026-04-30: Generalized Candidate Eligibility and Latest-Year Filtering

### What changed

- Replaced the partial hard-coded multilingual candidate filter expansion with a structural extraction fallback based on parsed table shape and numeric density.
- Tightened the first-pass prompt to explicitly handle any document language or regional reporting format through semantic table and note interpretation.
- Added `fitch_extractor/extraction/periods.py` to keep only the latest detected reporting year after strict LLM schema validation.
- Applied latest-year filtering across accepted rows from all page bundles before deduplication and persistence.
- Added tests for structural candidate eligibility, latest-year filtering, and prior-period row skipping before persistence.

### Why this design was chosen

The extraction layer should not try to enumerate every language, currency, and local reporting label. Deterministic retrieval now remains explainable and broad enough to pass dense candidate tables to the LLM, while the LLM handles multilingual semantic interpretation in the strict extraction prompt. Python post-processing then applies a language-agnostic year rule using detected four-digit years in validated row period labels.

This keeps the pipeline hybrid: deterministic parsing and retrieval still happen first, the LLM is used for semantic understanding, and Python validation controls what reaches the database.

### Alternatives rejected

- Adding a large list of translated segment and revenue keywords: rejected because it would be incomplete, brittle, and difficult to maintain across regions.
- Using live translation APIs during tests or extraction: rejected because it would add another external dependency and make local tests non-deterministic.
- Persisting every extracted year and relying on review to remove prior years: rejected because the project output needs the current/latest reporting period for first-pass extraction.
- Dropping rows with unknown period labels: rejected because some valid current-period rows may not have a clean period label from the provider.

### Tradeoffs remaining

- The structural fallback may send some non-segment numeric tables to the LLM. Prompt rules and row validation should reject unsupported rows, but this may increase warnings.
- Latest-year filtering depends on the provider populating `period_label` with a detectable year. Rows without a detected year are kept for review.
- Non-Latin PDFs still depend on the underlying PDF text/table extraction quality; OCR and vision fallback remain future work.
- Existing persisted prior-period rows are not automatically removed from local SQLite databases.

### How to test or verify the change

Run:

```bash
python -m unittest discover -s tests
python -m compileall fitch_extractor scripts tests
```

Manual verification should use fake extraction first, then Anthropic only when credentials are configured. A multi-year provider response should persist only rows for the latest detected year and record a `prior_period_row_skipped` validation issue.

### How this supports the Fitch project requirements

- Avoids hard-coded page numbers, document names, and language-specific keyword expansions in extraction gating.
- Uses LLM semantic interpretation where deterministic rules are weak, while keeping strict schemas and Python validation after the model call.
- Keeps final rows focused on the latest reporting period for CSV/XLSX/JSON output.
- Maintains human review as the quality gate by storing first-pass rows as pending and retaining validation issues for skipped prior-year rows.

## 2026-04-30: Primary Segment Table and Metric Selection Hardening

### What changed

- Fixed table-of-contents detection so repeated 10-K running headers such as `Table of Contents` do not exclude real segment revenue pages that contain units and table evidence.
- Strengthened the extraction prompt to prefer total segment revenue/income rows over external/customer-only rows when both appear in the same segment table.
- Added deterministic metric alignment for column-oriented tables where the model selected `External revenue` or `External income` but the same page clearly contains a preferred `Revenue` or `Total income` row.
- Added primary table selection to skip secondary duplicate segment tables after a stronger current-period table is available.
- Added unit tests for running-header TOC handling, metric alignment, and secondary table selection.

### Why this design was chosen

The ground-truth comparison showed that some incorrect values were not caused by missing pages. The pages were present, but the model selected customer-only/external rows instead of total segment revenue/income, and it duplicated later detailed tables after extracting an earlier primary table. These are extraction-boundary problems, so the fix combines clearer LLM instructions with deterministic Python safeguards before persistence.

The TOC fix keeps true TOC/index pages excluded while allowing SEC-style filings that repeat `Table of Contents` in the page header.

### Alternatives rejected

- Hard-coding document names, page numbers, or known segment labels from the ground-truth CSV: rejected because the application must generalize across issuers and formats.
- Trusting prompt changes alone: rejected because the prior extraction already showed plausible but wrong row choices from the same parsed page.
- Persisting all duplicate page groups for review: rejected because the first-pass output should not include repeated segment/value sets when a stronger primary table is already available.
- Removing all external revenue rows globally: rejected because external/customer revenue is valid when no total segment revenue/income row exists.

### Tradeoffs remaining

- Metric alignment only applies when the same parsed page exposes a clear preferred metric row. It does not guess across unrelated pages.
- Primary table selection may suppress later duplicate detail tables; skipped rows are visible as validation issues for review.
- The comparison script could only be run in fake-provider mode in this shell because Anthropic credentials were not present. A real acceptance run still needs `FITCH_EXTRACTION_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.

### How to test or verify the change

Run:

```bash
python -m unittest discover -s tests
python -m compileall fitch_extractor scripts tests
```

With Anthropic credentials configured, run:

```bash
FITCH_EXTRACTION_PROVIDER=anthropic python compare_extractions_to_ground_truth.py
```

Expected improvements are: Alphabet page 88 should no longer be blocked as a TOC page, Ørsted and Alliander should use total revenue/income values, and Citi/SAR duplicate secondary tables should be suppressed.

### How this supports the Fitch project requirements

- Preserves deterministic parsing and retrieval before LLM calls.
- Uses strict schemas followed by Python validation and normalization before database writes.
- Keeps first-pass rows pending and records skipped secondary rows as validation issues.
- Improves latest-period primary segment extraction without adding NACE, ESG, scoring, second-pass verification, or arbitration.

## 2026-04-30: Generic Consolidated Revenue Line Rejection

### What changed

- Added row-level validation that rejects generic consolidated revenue line items returned as segment names, such as `Revenue`, `Revenues`, `Sales`, `Turnover`, `Net revenue`, and `Net sales`.
- Tightened the first-pass prompt to avoid creating segment rows from consolidated income statement line labels.
- Added a regression test proving a `Revenue` row marked as not a segment breakdown is skipped with a validation issue.

### Why this design was chosen

The comparison run had one remaining extra row: a consolidated revenue line persisted as if it were a segment. This is a general extraction validation issue, not an issuer-specific issue. Rejecting generic revenue metric labels at the row boundary prevents the database from treating income statement line items as operating/reportable segments while still allowing valid labels such as `Total revenues` when they are part of a segment table.

### Alternatives rejected

- Hard-coding the offending document or page: rejected because the same failure can happen in any annual report.
- Rejecting all rows containing `total` or `revenue`: rejected because segment tables often legitimately include `Total`, `Total revenues`, or reportable segment reconciliation rows.
- Relying only on prompt language: rejected because validation should protect persistence even when provider output includes a plausible extra row.

### Tradeoffs remaining

- A real company segment literally named `Revenue` would be rejected, but that is far less likely than a provider misclassifying a consolidated revenue line item.
- The row is skipped as a validation issue; it is not automatically reviewed as a candidate segment.

### How to test or verify the change

Run:

```bash
python -m unittest discover -s tests
python -m compileall fitch_extractor scripts tests
```

With Anthropic credentials configured, rerun:

```bash
FITCH_EXTRACTION_PROVIDER=anthropic python compare_extractions_to_ground_truth.py
```

The Ørsted extra row with `segment_name = "Revenue"` should no longer persist.

### How this supports the Fitch project requirements

- Keeps first-pass extraction focused on business/reportable/operating segments and valid segment-table totals.
- Applies Python validation after strict LLM output validation and before persistence.
- Preserves human-review quality by recording skipped unsupported rows as validation issues.

## 2026-04-30: Language-Agnostic LLM Candidate Discovery Fallback

### What changed

- Removed the partial language-specific retrieval terms that had been added for one Spanish banking report case.
- Added `candidate_page_discovery_v1`, an LLM fallback prompt that reviews compact parsed-page summaries and returns likely extraction page numbers.
- Added strict Pydantic schemas for discovery output.
- Updated extraction so discovery runs only after deterministic candidate windows produce no accepted rows and no provider/schema error has occurred.
- Added tests using a deterministic fake provider to verify missed-page recovery without live API calls.

### Why this design was chosen

The Caja Rural run showed that deterministic retrieval sent balance sheet, solvency, portfolio, and credit-risk pages to extraction while missing a later disclosure page with a revenue-equivalent table. Adding one translated phrase would solve only that report and fail the project requirement to generalize across languages and formats.

The fallback uses the LLM for the part deterministic rules are weakest at: semantic page discovery across unfamiliar labels and languages. It still preserves the hybrid pipeline because PDF parsing, compact page summaries, strict schemas, Python validation, and pending-review persistence remain in place.

### Alternatives rejected

- Adding Spanish-specific or bank-specific terms to scoring: rejected because it does not generalize across languages.
- Sending the entire PDF to one extraction prompt: rejected because the project requires deterministic parsing/retrieval before LLM calls and bounded context.
- Persisting rows directly from page discovery: rejected because discovery should only choose pages; normal extraction and validation must remain the data boundary.
- Running discovery after malformed provider output: rejected because invalid JSON/schema errors are technical failures that should remain visible instead of triggering more provider calls.

### Tradeoffs remaining

- Documents with no valid segment or revenue-equivalent disclosure may produce one extra discovery call before returning no rows.
- Discovery uses compact summaries, so poor PDF text extraction can still hide the correct page.
- The fallback improves recall but does not replace human review or second-pass verification.

### How to test or verify the change

Run:

```bash
python -m unittest discover -s tests
python -m compileall fitch_extractor scripts tests
```

For a document whose initial candidates produce no rows, rerun extraction with Anthropic enabled and confirm that any discovered pages still persist rows only through the standard first-pass schema and validation path.

### How this supports the Fitch project requirements

- Avoids hardcoded language-specific retrieval rules.
- Uses deterministic parsing before LLM calls and strict schemas after LLM calls.
- Keeps human review as the quality gate by storing only pending first-pass rows.
- Improves recall for multilingual and region-specific annual report formats without implementing NACE, ESG, scoring, second-pass verification, or arbitration.

## 2026-04-30: Normalization, Validation, Verification, and Arbitration Layer

### What changed

- Added post-extraction normalization for currency, scale, numeric values, dash/blank values, fiscal periods, and page references.
- Added deterministic validation for evidence completeness, currency/scale/period consistency, metric basis, duplicate segment names, total reconciliation, declared segment coverage, consolidated income statement false positives, and geography/product/business segmentation handling.
- Added row confidence scoring from extraction confidence, evidence, normalization, validation, page relevance, and reconciliation.
- Added strict second-pass verification and arbitration schemas/prompts using the existing LLM provider interface.
- Wired the extraction service so rows are normalized and validated before persistence, verification runs only for uncertain cases with a configured provider, and arbitration runs only when validation or verification fails with a configured provider.
- Added review-oriented statuses: `needs_review` and `ready_for_review` for rows and documents, while preserving approved/edited/rejected export gating.
- Added `docs/VALIDATION_AND_RECONCILIATION.md` and tests for normalization, validation, reconciliation, fake verification, arbitration triggers, and integrated issue persistence.

### Why this design was chosen

The project requires a hybrid pipeline where deterministic code protects the database from raw LLM output. The new layer keeps first-pass extraction intact and adds a clear post-processing boundary: normalize first, validate deterministically, compute confidence, then call LLM verification/arbitration only when there is uncertainty.

Verification and arbitration results are stored as `ValidationIssue` records instead of adding new tables. This is sufficient for a local prototype, keeps the API shape stable, and avoids a schema migration until product requirements need richer review workflows.

### Alternatives rejected

- Using a single stronger LLM call for every document: rejected because the pipeline should be deterministic-first and avoid unnecessary model calls.
- Treating dash-only cells as zero by default: rejected because annual-report dashes often mean blank/not applicable rather than numeric zero.
- Persisting invalid expense, asset, profit, EBIT, EBITDA, or tax rows for review: rejected because those are outside the core revenue segment extraction contract unless explicitly configured.
- Adding NACE, ESG, scoring, or export in this change: rejected because the prompt explicitly excludes those features.
- Adding a new persistence table for verifier/arbitration JSON: rejected for now because existing validation issues can store review-visible rationale without changing the database contract.

### Tradeoffs remaining

- Declared segment coverage is heuristic and depends on parsed table structure.
- Total reconciliation handles explicit totals and common reconciliation rows, but complex issuer-specific bridges may still need human review.
- Verification and arbitration are optional and require configured providers; tests use fake providers only.
- Confidence is a prototype score for review prioritization, not a final Fitch scoring model.
- Existing SQLite databases are not migrated for historical status values; old `pending` rows still block export until reviewed.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor scripts tests
```

Manual verification:

```bash
python scripts/ingest_pdf.py /path/to/annual-report.pdf --company-name "Example Corp"
python scripts/extract_revenue_segments.py doc_... --provider fake
```

Inspect the JSON output for `ready_for_review` or `needs_review` rows and validation issues such as normalization warnings, metric rejections, reconciliation mismatches, or verifier rationale.

### How this supports the Fitch project requirements

- Keeps deterministic parsing/retrieval before LLM extraction.
- Uses strict schemas for first-pass extraction, verification, and arbitration outputs.
- Normalizes and validates LLM rows in Python before persistence.
- Preserves raw values and stores normalization/validation warnings.
- Blocks invalid core non-revenue metrics from being stored as revenue segment rows.
- Keeps human review as the final quality gate and keeps export blocked until rows are approved, edited, or rejected.
- Does not implement NACE, ESG, scoring, or final export.

## 2026-04-30: Human Review Workflow and Streamlit UI

### What changed

- Expanded `ReviewService` into the review workflow boundary for fetching document review state, editing rows, approving/rejecting rows, adding manual rows, adding reviewer notes, marking validation issues, and approving documents.
- Added `validation_issue_reviews` so validation issues remain immutable while reviewers can mark issues as `acknowledged` or `resolved`.
- Tightened export gating so export records require document approval, not only reviewed row statuses.
- Added API-facing review state schemas and Streamlit review helpers.
- Added `streamlit_app.py` for upload/select, analysis, pipeline status, summary cards, editable segment rows, evidence, validation review, manual row addition, document approval, and gated CSV/JSON download controls.
- Added backend and UI-helper tests for review events, document approval gates, manual row addition, issue acknowledgement/resolution, and export blocking.

### Why this design was chosen

The project currently has repository and service objects but no HTTP routing layer. A service-first review workflow preserves the existing frontend/API contract and gives both future FastAPI routes and the new Streamlit UI the same business logic. Validation issue review state is stored beside immutable validation issues to preserve auditability.

### Alternatives rejected

- Mutating validation issue rows directly: rejected because issue text and severity are pipeline evidence and should stay auditable.
- Adding FastAPI routes in this change: rejected because the local prototype needs a working Streamlit review UI now, and route code would duplicate the service boundary without an existing server.
- Storing reviewer notes on segment rows: rejected because notes are review actions and belong in `ReviewEvent`.
- Allowing export after row approval alone: rejected because the document-level gate must also account for validation issues and reconciliation acknowledgement.
- Implementing NACE, ESG, scoring, or XLSX generation: rejected because this prompt is focused on human review and explicitly excludes NACE/ESG/scoring.

### Tradeoffs remaining

- Streamlit renders bbox JSON and leaves page-image bbox highlighting as a documented extension point.
- CSV/JSON downloads are available from the UI after approval; XLSX generation still needs a small export module and dependency/product decision.
- There is no HTTP route layer yet, so the API contract is documented through Pydantic response/request shapes and service methods.
- Existing SQLite databases are upgraded only by creating the new review-state table; no historical issue review rows are backfilled.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor streamlit_app.py scripts tests
```

Manual verification:

```bash
streamlit run streamlit_app.py
```

Upload or select a document, run analysis, edit a row, approve/reject rows, acknowledge or resolve validation issues, then approve the document. Export controls should remain disabled until document approval succeeds.

### How this supports the Fitch project requirements

- Makes human review the final quality gate before export.
- Lets analysts inspect evidence, correct fields, reject false positives, add missing segments, and add notes.
- Preserves original evidence and raw extraction history through immutable evidence rows and review event logging.
- Blocks approval when rows are unaddressed, required fields are missing, blocking validation issues are unresolved, or reconciliation mismatches lack reviewer acknowledgement.
- Keeps the hybrid extraction pipeline intact and does not add NACE, ESG, or scoring work.

## 2026-04-30: Ground Truth Comparison Fixes

### What changed

- Added deterministic same-page context inference for missing row currency and scale during normalization.
- Added `SAR` / Saudi Riyal support to currency normalization.
- Adjusted metric validation so a row with an explicitly valid revenue metric is not blocked just because broad table evidence also contains words such as `loss`.
- Allowed revenue reconciliation labels such as `Hedging gains (losses)` when they appear with a valid revenue metric.
- Updated the first-pass prompt to explicitly include row-oriented revenue reconciliation rows that bridge segment rows to total revenues.
- Fixed the ground-truth comparison helper so normalized page references such as `p. 88` compare correctly to numeric CSV pages.
- Fixed Streamlit SQLite startup by using a connection configured for Streamlit thread reuse.

### Why this design was chosen

The failures were not caused by a missing review feature. They were extraction quality gaps: row-level model output can omit currency even when the parsed page contains the unit, and validation was too aggressive for financial-services tables where revenue rows coexist with income/loss columns. The fix keeps deterministic parsing and normalization as the first line of defense instead of relying on another prompt-only pass.

### Alternatives rejected

- Hardcoding document-specific currency or segment fixes: rejected because the prototype should generalize across the ground-truth set.
- Disabling invalid metric validation entirely: rejected because non-revenue rows still need to be blocked.
- Treating all labels containing `loss` as valid reconciliation rows: rejected because only explicit revenue reconciliation labels with a valid revenue metric should pass.
- Changing the ground-truth CSV: rejected because the comparator should understand the app's normalized page-reference format.

### Tradeoffs remaining

- Page-context inference uses same-page text and may still miss unit labels if a PDF parser omits them.
- Prompt changes improve model recall for hedging/reconciliation rows but still depend on provider behavior.
- The local comparison run in this environment defaults to the fake provider; Anthropic-backed comparison requires the user's configured environment variables.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor compare_extractions_to_ground_truth.py streamlit_app.py scripts tests
./.venv/bin/python compare_extractions_to_ground_truth.py
```

For Anthropic-backed comparison, run the last command with the same `FITCH_EXTRACTION_PROVIDER` and API credentials used for extraction.

### How this supports the Fitch project requirements

- Improves deterministic normalization after strict LLM extraction.
- Keeps false-positive blocking for non-revenue metrics while allowing valid revenue reconciliation rows.
- Preserves human review status and evidence handling unchanged.
- Strengthens the local ground-truth regression harness used to measure extraction quality.

## 2026-04-30: Reviewed Document Export Service

### What changed

- Added `fitch_extractor/exporting/` with an `ExportService` that writes CSV, XLSX, and JSON audit exports.
- Added a backend-facing `export_reviewed_document` handler and `DocumentExportResponse` schema.
- Updated Streamlit export controls to create local files through the export service and show the latest persisted export path/timestamp.
- Added export tests for CSV columns, JSON audit content, review-gate blocking, rejected-row exclusion, XLSX creation, and persisted export records.
- Added `docs/EXPORTS.md` and updated API/testing docs.

### Why this design was chosen

Export generation is kept in a focused service instead of the Streamlit UI so the same behavior can be reused by future HTTP routes or CLI commands. The service reads persisted document, segment, evidence, validation, and review records and writes predictable local files under `exports/{document_id}/`.

CSV/XLSX are treated as final structured outputs for approved rows only. JSON is the audit output and therefore includes rejected rows, evidence, validation issues, review events, timestamp, and pipeline config summary.

### Alternatives rejected

- Keeping ad hoc Streamlit download generation: rejected because it did not create durable files or complete audit exports.
- Adding a web framework solely for an export route: rejected because the repository still has no HTTP layer and a route-adjacent handler is enough for the current contract.
- Adding an XLSX dependency: rejected because the required workbook is simple enough to generate as a minimal Office Open XML package with the Python standard library.
- Updating document status to `exported`: rejected because the approval status remains the quality gate and changing it would block future exports under the current readiness rule.
- Implementing NACE, ESG, or scoring values in export columns: rejected because those workflows are not implemented yet.

### Tradeoffs remaining

- The XLSX writer is intentionally minimal and does not add formatting, formulas, or charts.
- The service creates one `ExportRecord` per generated format rather than a separate bundle table.
- Original extracted values are preserved through review events when edits occurred; the current schema does not keep a separate immutable original row snapshot.
- Export timestamps use runtime UTC time, while `ExportRecord.created_at` continues to use the repository clock.

### How to test or verify the change

Run:

```bash
python -m unittest tests.test_export_service
python -m unittest discover -s tests
python -m compileall fitch_extractor streamlit_app.py scripts tests
```

Manual verification in Streamlit:

```bash
streamlit run streamlit_app.py
```

Review rows, approve/reject each row, approve the document, then click `Create export files`. Confirm `exports/{document_id}/revenue_segments.csv`, `revenue_segments.xlsx`, and `audit_export.json` exist and that the UI shows the latest export path and timestamp.

### How this supports the Fitch project requirements

- Blocks final export until human review and document approval are complete.
- Excludes rejected rows from main CSV/XLSX outputs while preserving them in the JSON audit export.
- Preserves evidence, validation issues, review events, and export timestamp for auditability.
- Uses stable required columns with blank NACE/ESG/scoring extension columns for later phases.
- Keeps the pipeline hybrid and production-minded by exporting only normalized, validated, reviewed rows from persistence rather than prompting an LLM for final files.

## 2026-04-30: NACE Rev.2 Segment Mapping Extension

### What changed

- Copied the NACE Rev.2 outline CSV into `reference/NACE_Rev2_Outline.csv`.
- Added `fitch_extractor/nace/` for reference loading, deterministic candidate retrieval, optional LLM reranking, and segment mapping orchestration.
- Added `NaceSelection` plus SQLite storage in `segment_nace_selections`.
- Extended repository/review services to persist top-three candidates, accept candidates, and record reviewer overrides as `ReviewEvent` rows.
- Added NACE candidates and selections to review-state/API-facing schemas.
- Updated Streamlit to run NACE mapping, display candidates, accept a candidate, and save manual overrides.
- Populated selected NACE fields in CSV/XLSX exports and added candidates/selections to the JSON audit export.
- Added focused NACE tests and updated data, API, export, testing, and mapping docs.

### Why this design was chosen

The project requires a hybrid pipeline. NACE mapping is deterministic retrieval first, with LLM reranking limited to a small candidate set. The reference CSV is loaded locally and normalized into explicit `NaceNode` records so LLM outputs can be checked against known codes.

SQLite already had a `nace_candidates` table, so the extension reuses that storage for ranked candidates and adds one small selection table for reviewer state. Review overrides use the existing immutable review-event audit trail instead of a separate audit system.

### Alternatives rejected

- Prompt-only NACE classification: rejected because it would allow hallucinated codes and would not satisfy deterministic retrieval requirements.
- A vector database: rejected because normalized text, fuzzy matching, and keyword overlap are enough for the local prototype.
- Hardcoding the CSV source path: rejected because application code must not depend on a machine-specific absolute path.
- Making NACE required for export: rejected because NACE is enrichment and must not block core revenue extraction/export unless later configured.
- Storing only a single code: rejected because reviewers need transparent ranked alternatives and rationale.

### Tradeoffs remaining

- Deterministic matching is lexical and may miss semantic matches where segment wording is far from NACE labels.
- Reviewer manual override validates basic shape but does not yet require selecting from the full reference set in `ReviewService`.
- The LLM reranker improves ordering only within deterministic candidates; it cannot recover a code that retrieval failed to include.
- Existing SQLite files receive the new table through `CREATE TABLE IF NOT EXISTS`, not a full migration framework.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest tests.test_nace_mapping
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor streamlit_app.py tests
```

Manual UI verification:

```bash
streamlit run streamlit_app.py
```

Select or upload a document, run NACE mapping, choose a segment row, inspect the NACE candidates, accept one or save an override, then create exports after normal review approval.

### How this supports the Fitch project requirements

- Keeps NACE mapping behind deterministic parsing/retrieval rather than a single prompt.
- Uses strict LLM output validation and rejects invented codes outside the reference candidate set.
- Stores transparent top-three candidates with code, label, level, rank, score, and rationale.
- Supports human review and reviewer override as the final quality gate.
- Adds NACE fields to CSV/XLSX/JSON exports without making NACE block core revenue extraction.
- Documents the stable CSV path and exact detected reference schema.

## 2026-04-30: Explicit Opus Arbitration Path

### What changed

- Made arbitration explicitly default to a separately configured Opus model.
- Kept first-pass extraction and second-pass verification on their configured non-Opus models unless explicitly overridden.
- Added CLI controls for `--verification-model`, `--arbitration-model`, `--disable-verification`, and `--disable-arbitration`.
- Renamed persisted arbitration issue types to `llm_opus_arbitration_*` so review surfaces can distinguish stronger-model arbitration from normal verifier output.
- Updated the arbitration prompt to identify the pass as Claude Opus or equivalent stronger-model arbitration.
- Fixed USD code normalization so clean USD rows do not incorrectly trigger Opus arbitration.
- Added tests proving clean documents do not call arbitration and uncertain documents call the configured Opus arbitration model.

### Why this design was chosen

The previous arbitration implementation was generic and reused the extraction model by default, which did not satisfy the requirement for a configurable stronger Opus arbitration path. Keeping Opus as a separate model setting preserves the cost-control rule: arbitration is available for hard cases, but the normal extraction path does not pay for it on every document.

### Alternatives rejected

- Running Opus for every document: rejected because the prompt requires arbitration only when deterministic validation or verification fails.
- Replacing the first-pass extraction model with Opus: rejected because the project design uses cheaper deterministic and first-pass stages before escalation.
- Storing Opus arbitration as generic verifier issues: rejected because reviewers need to know when a stronger arbitration pass influenced the review state.

### Tradeoffs remaining

- The Opus model name is still configurable because provider model catalogs change over time.
- Opus arbitration results remain review evidence, not automatic approval; human review remains the final quality gate.
- The same provider object is used by the CLI for extraction, verification, and arbitration, with different model names per request. A future HTTP layer may expose separate provider routing if needed.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest tests.test_verification_arbitration
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor streamlit_app.py scripts tests
```

Manual verification:

```bash
.venv/bin/python scripts/extract_revenue_segments.py doc_... \
  --provider anthropic \
  --arbitration-model claude-opus-4-7
```

Then inspect validation issues for `llm_opus_arbitration_result` only on documents with validation or verification failures.

### How this supports the Fitch project requirements

- Uses Opus only for difficult cases instead of every document.
- Keeps deterministic normalization and validation before model escalation.
- Stores arbitration rationale for review.
- Keeps the provider optional and fakeable in tests.
- Maintains human review as the final quality gate.

## 2026-04-30: OCR/Vision Fallback and Multilingual Robustness

### What changed

- Added `fitch_extractor/ingestion/fallbacks.py` with a page-text fallback provider interface, local Tesseract CLI OCR provider, callable vision/text adapters, settings from environment, and a fake provider for tests.
- Updated PDF parsing to detect low-text pages, run optional fallback only for those pages, store fallback text with `parser_sources` such as `ocr` or `vision`, and record fallback status in `blocks_json.text_fallback`.
- Added synthetic fallback text blocks so evidence lookup can persist fallback parser sources.
- Added lightweight page-language marking for common English, Spanish, French, German, Italian, and Portuguese revenue/segment pages.
- Expanded deterministic retrieval terms for common multilingual revenue, sales, turnover, segment, and total wording.
- Updated extraction prompts to preserve original segment labels, extract regardless of report language, and translate only explanatory notes when useful.
- Added a Streamlit warning when stored evidence came from OCR/vision fallback text.
- Added tests for low-text detection, fallback providers, fallback persistence, multilingual matching, non-English prompt construction, and fake OCR integration.

### Why this design was chosen

The project needs graceful handling for scanned or low-text pages without making OCR or vision mandatory. A small provider interface keeps the parser deterministic by default and lets the local prototype opt into OCR only when the user has an OCR command installed. The same interface can later wrap a vision/PDF model without changing parsed-page storage, evidence storage, retrieval, or review contracts.

Multilingual support remains retrieval and prompt guidance, not machine translation. This preserves official segment names and avoids adding brittle translation dependencies while still improving recall for common annual-report wording.

### Alternatives rejected

- Mandatory OCR dependencies: rejected because many local prototype runs will use digital PDFs and should not require OCR setup.
- Running OCR on every page: rejected because it is slower and unnecessary when PyMuPDF already extracts meaningful text.
- Prompt-only recovery for scanned pages: rejected because low-text pages need text before deterministic retrieval and evidence matching can work.
- Machine translation before extraction: rejected because it can lose official segment labels and add fragile dependencies.
- Adding new database columns for fallback metadata: rejected because existing `parser_sources`, `blocks_json`, and `segment_evidence.parser_source` already cover the needed contract.

### Tradeoffs remaining

- The local OCR provider depends on an installed command-line OCR tool and records a parser failure if the command is unavailable.
- The language marker is intentionally shallow and may return `unknown` for pages without strong revenue/segment terms.
- Fallback text blocks generally lack bounding boxes, so OCR/vision evidence is reviewable text but not word-perfect visual evidence.
- Vision/PDF model fallback is an interface, not an enabled provider, because live provider choice and cost controls remain deployment-specific.

### How to test or verify the change

Run:

```bash
.venv/bin/python -m unittest tests.test_pdf_ingestion tests.test_revenue_extraction
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall fitch_extractor streamlit_app.py scripts tests
```

Manual verification:

```bash
FITCH_ENABLE_PAGE_TEXT_FALLBACK=true \
FITCH_PAGE_TEXT_FALLBACK_PROVIDER=ocr \
FITCH_OCR_COMMAND=tesseract \
.venv/bin/python scripts/ingest_pdf.py /path/to/scanned-report.pdf --company-name "Example Corp"
```

Then open Streamlit, select the document, inspect evidence for rows extracted from low-text pages, and confirm the warning appears for `ocr` or `vision` evidence.

### How this supports the Fitch project requirements

- Keeps deterministic PDF parsing and retrieval before LLM extraction.
- Makes OCR/vision fallback opt-in and fakeable for local tests.
- Persists fallback source metadata for audit and review.
- Improves non-English recall while preserving original segment labels.
- Maintains strict LLM schemas, Python normalization/validation, and human review before export.
- Avoids mandatory OCR and machine-translation dependencies for the local prototype.
