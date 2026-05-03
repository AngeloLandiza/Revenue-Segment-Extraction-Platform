# Revenue Segment Extraction Prototype

Production-minded local prototype for extracting revenue segment data from annual reports and
10-K PDFs. The app combines deterministic PDF parsing and evidence retrieval with schema-bound
LLM extraction, Python normalization, analyst review, NACE mapping, ESG factor extraction,
prototype scoring, and final CSV/XLSX/JSON export.

This repository is intended to be easy for industry reviewers to run, audit, and extend. It is
not a production system and does not make official ratings or sustainability scoring claims.

## Core Output

The review and export workflow centers on these fields:

- Company Name
- Document Name
- Segment Name
- Revenue Value
- Revenue Unit
- Currency
- Scale
- Time Period
- Page/Section Reference
- Evidence Text
- Review Status

Additional audit fields capture original and normalized segment names, NACE candidates and
selections, ESG factor summaries, prototype scoring inputs, reviewer notes, validation issues,
and evidence history.

## Why The Design Is Hybrid

Revenue segment extraction is risky when handled as one prompt over a full PDF. Annual reports
mix segment revenue with totals, expenses, assets, profit, reconciliation rows, prior-year
columns, ESG disclosures, and narrative text.

This prototype reduces that risk with a layered pipeline:

1. Parse text, layout, tables, page metadata, and evidence deterministically.
2. Rank likely revenue-segment pages before any LLM call.
3. Send small page bundles to the configured provider with strict JSON schemas.
4. Normalize revenue values, currency, scale, period labels, and page references in Python.
5. Validate rows for required fields, metric basis, duplicates, reconciliation, and evidence.
6. Keep NACE mapping, ESG extraction, and prototype scoring reviewable.
7. Block final export until every row is approved, edited, or rejected and the document is approved.

## Repository Layout

```text
revenue_segment_extractor/
  ingestion/       PDF parsing, metadata detection, evidence rendering, page retrieval
  extraction/      provider setup, prompts, schemas, normalization, validation, verification
  nace/            NACE Rev.2 reference retrieval and mapping
  persistence/     SQLite schema, repository methods, review workflow services
  exporting/       CSV, XLSX, and audit JSON export gate
  api/             Pydantic response schemas for stable contracts
  ui/              Review-table helpers used by Streamlit
scripts/           CLI entry points for database, ingestion, extraction, queueing, export
docs/              Architecture, pipeline, review, deployment, and evaluation documentation
tests/             Unit and smoke tests for pipeline, review, export, queueing, and contracts
reference/         NACE Rev.2 outline CSV
config/            Prototype scoring rules
```

## Requirements

- Python 3.11 or newer recommended.
- A local virtual environment.
- `ANTHROPIC_API_KEY` only when using `RSE_EXTRACTION_PROVIDER=anthropic`.
- No API key is required for deterministic fake-provider tests and smoke demos.

Dependencies are intentionally small and listed in `requirements.txt`:

- `streamlit`
- `anthropic`
- `pydantic`
- `PyMuPDF`
- `pdfplumber`

## Local Setup

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/manage_db.py
```

Use `.venv/bin/python` for CLI commands below. If you use another virtual environment, replace
that path with your environment's Python.

## Environment Variables

Copy the example file for a local Anthropic run:

```bash
cp .env.example .env.local
```

Then edit `.env.local` and set your private key.

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Required only for real Anthropic extraction. |
| `RSE_EXTRACTION_PROVIDER` | `anthropic` for real extraction or `fake` for deterministic smoke tests. Defaults to `anthropic`. |
| `RSE_EXTRACTION_MODEL` | Extraction, NACE, ESG, and default verifier model. |
| `RSE_VERIFICATION_MODEL` | Optional second-pass verifier model. |
| `RSE_ENABLE_SECOND_PASS_VERIFICATION` | Enables verifier pass. Defaults to `true`. |
| `RSE_ENABLE_ARBITRATION` | Enables optional arbitration pass. Defaults to `false`. |
| `RSE_ARBITRATION_MODEL` | Optional arbitration model override. Defaults to the extraction model when unset. |
| `RSE_EXTRACTION_PAGE_BUNDLE_SIZE` | Candidate pages per extraction prompt bundle. |

The app does not log API keys. Provider errors are redacted before persistence or display.

## Run The Streamlit Workbench

No-API smoke demo:

```bash
export RSE_EXTRACTION_PROVIDER=fake
.venv/bin/streamlit run streamlit_app.py
```

Anthropic-backed local demo:

```bash
scripts/run_streamlit_anthropic.sh
```

The script loads `.env.local` when present, initializes the SQLite database, and starts
Streamlit.

## Process A Document

1. Open the Streamlit UI.
2. Upload an annual report or 10-K PDF.
3. Enter company name, fiscal period, currency, and scale when known.
4. Choose `anthropic - real LLM extraction` or `fake - deterministic local smoke test`.
5. Click `Queue extraction`.
6. Click `Process next queued document` in the sidebar, or run the worker:

```bash
.venv/bin/python scripts/process_queue.py --all
```

Queued processing keeps multi-session work visible and avoids overlapping extraction jobs against
the same SQLite database.

## CLI Workflow

Initialize or reset the local database:

```bash
.venv/bin/python scripts/manage_db.py
.venv/bin/python scripts/manage_db.py --reset
```

Ingest a PDF:

```bash
.venv/bin/python scripts/ingest_pdf.py path/to/report.pdf \
  --company-name "Example Co." \
  --fiscal-period FY2025 \
  --currency USD \
  --scale millions
```

Run deterministic smoke extraction:

```bash
.venv/bin/python scripts/extract_revenue_segments.py doc_... \
  --provider fake \
  --disable-verification \
  --disable-arbitration
```

Run real extraction:

```bash
.venv/bin/python scripts/extract_revenue_segments.py doc_... --provider anthropic
```

Export an approved document:

```bash
.venv/bin/python scripts/export_document.py doc_...
```

## Review And Export Gate

Analysts use the Streamlit review workbench to:

- Inspect extracted revenue rows and source evidence.
- Edit incorrect revenue values, currency, scale, period, page references, or notes.
- Reject rows that are not revenue segments.
- Resolve or acknowledge validation issues.
- Accept or override NACE candidates.
- Review company-wide and segment-linked ESG factors.
- Compute demonstration-only prototype scores.
- Approve the document only after row-level review is complete.

Final export is blocked until the document is approved and every segment row has a final review
status. Rejected rows are excluded from `revenue_segments.csv` and `revenue_segments.xlsx`; the
audit JSON retains rejected rows, evidence, validation issues, review events, NACE, ESG, and
scoring context.

## Deployment Notes

### GitHub Repository

The repository root `README.md` is the primary project landing page on GitHub. Keep the root
README focused on setup, architecture, review gates, testing, and deployment so reviewers can
understand the platform directly from the repository home page.

### Streamlit Community Cloud

Use `streamlit_app.py` as the app entry point. Add private values such as `ANTHROPIC_API_KEY`
through Streamlit Community Cloud secrets or environment configuration, not in the repository.
The included `requirements.txt` is sufficient for the app runtime.

For detailed setup, see `docs/STREAMLIT_COMMUNITY_CLOUD.md`.

## Testing

Run the full test suite:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
```

Run compile checks:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q \
  revenue_segment_extractor streamlit_app.py scripts tests parallel_folder_pipeline.py \
  compare_extractions_to_ground_truth.py test_claude_api.py
```

The repository does not currently define a dedicated formatter, linter, or static type-check
command. Keep changes small, typed, and covered by focused tests.

## Evaluation

Prepare labeled gold CSV/JSON files, then run:

```bash
.venv/bin/python -m revenue_segment_extractor.evaluate \
  --gold 'data/gold/*.csv' \
  --pred exports
```

The evaluator writes `evaluation_summary.md`, `evaluation_results.csv`, and
`failure_analysis.md` to `reports/`.

## Key Documentation

- `docs/README.md`: organized documentation index.
- `docs/PIPELINE_WALKTHROUGH.md`: end-to-end technical pipeline.
- `docs/DATA_CONTRACT.md`: data fields and export contract.
- `docs/REVIEW_WORKFLOW.md`: analyst review and approval gate.
- `docs/VALIDATION_AND_RECONCILIATION.md`: validation rules and reconciliation behavior.
- `docs/NACE_MAPPING.md`: NACE mapping workflow.
- `docs/ESG_EXTRACTION.md`: ESG factor extraction and review.
- `docs/STREAMLIT_COMMUNITY_CLOUD.md`: Streamlit deployment setup.
- `docs/ARCHITECTURE_DECISIONS.md`: decision log.

## Security And Limitations

- Do not commit `.env.local`, `.env`, SQLite databases, uploaded PDFs, generated evidence
  previews, exports, or reports.
- This is a local class/project prototype, not a production system.
- Extraction quality depends on PDF text/table quality and model access.
- Scanned PDFs may require OCR fallback configuration and extra review.
- NACE and ESG linkage can be ambiguous and must remain reviewable.
- Prototype scores are demonstration-only and are not official ratings or sustainability outputs.
- No final quality claim should be made without running the evaluator on a labeled gold set.
