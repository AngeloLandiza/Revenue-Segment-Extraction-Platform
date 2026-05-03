# Solution Description

## Workflow

1. `scripts/ingest_pdf.py` registers the PDF, parses text/tables with PyMuPDF and pdfplumber, stores parsed pages, and ranks candidate pages using deterministic revenue/segment signals.
2. `RevenueExtractionService` builds page bundles from ranked candidates and calls the configured provider with a versioned strict JSON prompt.
3. Pydantic schemas reject malformed LLM output. Python normalization converts raw values, currency, scale, period, and page references into stored `SegmentRow` records.
4. Deterministic validation flags missing fields, invalid metrics, duplicate rows, prior periods, total reconciliation gaps, page-evidence problems, and non-revenue line items.
5. Optional second-pass verification and arbitration run only when validation signals uncertainty.
6. NACE mapping retrieves deterministic NACE candidates, optionally classifies them through the configured provider, and leaves final acceptance or override to review.
7. ESG extraction uses deterministic ESG page retrieval, strict LLM output, and conservative segment linking. Company-wide ESG is not copied to all segments.
8. `ReviewService` logs edits, approvals, rejections, validation issue actions, NACE actions, ESG actions, and document approval.
9. `ExportService` blocks export until document approval and row review are complete, excludes rejected rows from the final CSV/XLSX, and writes a full audit JSON.
10. `fitch_extractor.evaluate` compares reviewed exports to labeled gold files and writes summary, row-level, and failure-analysis reports.

## Code, Prompts, And Tool Configuration

- PDF parsing: `fitch_extractor/ingestion/`.
- Revenue extraction service: `fitch_extractor/extraction/service.py`.
- Prompt templates: `fitch_extractor/extraction/prompts.py` and `docs/PROMPTS.md`.
- Strict schemas: `fitch_extractor/extraction/schemas.py`.
- Normalization and validation: `fitch_extractor/extraction/normalization.py` and `validation.py`.
- Review and export gates: `fitch_extractor/persistence/review.py` and `fitch_extractor/exporting/service.py`.
- NACE reference data: `reference/NACE_Rev2_Outline.csv`.
- Scoring config: `config/scoring_rules.yaml`.
- Provider selection: `FITCH_EXTRACTION_PROVIDER=anthropic` for real LLM calls or `fake` for deterministic smoke tests.

## Running On New Documents

```bash
.venv/bin/python scripts/manage_db.py
.venv/bin/python scripts/ingest_pdf.py path/to/report.pdf --company-name "Example Co." --fiscal-period FY2025 --currency USD --scale millions
.venv/bin/python scripts/extract_revenue_segments.py doc_... --provider anthropic
.venv/bin/streamlit run streamlit_app.py
```

Review the document in Streamlit, approve or reject every row, approve the document, then export:

```bash
.venv/bin/python scripts/export_document.py doc_...
```

## Value Provided

- Reducing risk: deterministic retrieval, schema validation, metric filters, total reconciliation, and export gates reduce common false positives such as expenses, assets, losses, profit rows, duplicate totals, and unsupported ESG linkage.
- Improving efficiency: analysts start from ranked candidate pages, extracted rows, evidence snippets, validation flags, and precomputed NACE/ESG candidates instead of reading an entire report manually.
- Increasing trust: every row carries page evidence, review status, validation history, and audit JSON output.
- Enabling scale: the pipeline is modular, provider-configurable, testable without API credentials, and designed to run on new PDFs through CLI or UI.
