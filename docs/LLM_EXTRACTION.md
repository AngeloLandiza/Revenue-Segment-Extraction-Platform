# LLM Extraction

## Scope

First-pass revenue segment extraction runs after deterministic PDF ingestion and page candidate ranking. It extracts structured revenue segment rows from candidate page bundles only.

The extraction package also includes optional second-pass verification and arbitration for uncertain cases. It does not implement NACE mapping, ESG extraction, scoring, final export writing, an HTTP API, or a UI.

## Provider Interface

Providers implement `LLMProvider.complete_json()` in `revenue_segment_extractor/extraction/providers.py`.

Implemented providers:

- `fake`: deterministic local provider used by tests and safe manual runs.
- `anthropic`: real Anthropic provider, enabled only when selected and `ANTHROPIC_API_KEY` is present.

Business logic depends on the provider protocol, not direct vendor API calls.

The CLI passes the selected provider into first-pass extraction, second-pass verification, and arbitration. Verification/arbitration still run only when their settings are enabled and the document has validation or verification failures.

## Configuration

Environment variables:

- `RSE_EXTRACTION_PROVIDER`: `fake` or `anthropic`; defaults to `anthropic`.
- `RSE_EXTRACTION_MODEL`: extraction model name; defaults to `claude-sonnet-4-6`.
- `RSE_VERIFICATION_MODEL`: optional model for second-pass verification; defaults to `RSE_EXTRACTION_MODEL`.
- `RSE_ARBITRATION_MODEL`: optional model for difficult arbitration; defaults to the extraction model.
- `RSE_EXTRACTION_PAGE_BUNDLE_SIZE`: maximum adjacent candidate pages per prompt; defaults to `2`.
- `RSE_EXTRACTION_MAX_TOKENS`: provider output token limit; defaults to `16000`.
- `RSE_EXTRACTION_TEMPERATURE`: provider temperature; defaults to `0`.
- `RSE_ENABLE_SECOND_PASS_VERIFICATION`: boolean; defaults to `true`.
- `RSE_ENABLE_ARBITRATION`: boolean; defaults to `false`.
- `ANTHROPIC_API_KEY`: required only for Anthropic mode.

API keys are read from the environment and are not logged or persisted.

## Prompting

The first-pass prompt builder is `build_first_pass_extraction_prompt()` with prompt version:

```text
first_pass_revenue_segments_v1
```

When deterministic candidate windows produce no accepted rows, the service can run an LLM page-discovery fallback over compact page summaries with prompt version:

```text
candidate_page_discovery_v1
```

The fallback returns page numbers only. Those pages still go through the normal first-pass extraction prompt, strict schema validation, Python normalization/validation, deduplication, and review-status persistence.

Uncertain normalized rows can trigger second-pass verification with prompt version:

```text
second_pass_verification_v1
```

Difficult cases can trigger LLM arbitration with prompt version:

```text
revenue_arbitration_v1
```

Verification and arbitration prompts receive page snippets, normalized extracted rows, validation issues, and the strict project rules. They return strict JSON only and are fakeable in tests. Arbitration is not called for clean documents.

The prompt instructs the model to:

- extract only revenue, turnover, net sales, segment revenue, or external revenue tied to business, reportable, or operating segments
- prefer total segment revenue, revenue, revenues, total income, or total operating income over external/customer-only revenue when both appear in the same current-period segment table
- handle any document language or regional reporting format by using semantic meaning, table structure, and nearby notes rather than English labels alone
- avoid expenses, losses, assets, EBITDA, EBIT, profit, operating income, employee counts, and non-revenue metrics unless explicitly revenue-equivalent
- distinguish true operating/business/reportable segments from consolidated income statement line items
- extract totals only when clearly relevant to the segment table
- extract every current-period column in a primary segmentation or segment information revenue row, including eliminations, totals, reclassification-to-reported columns, and reported columns
- return one primary current-period segment table per document instead of duplicating the same segment/value set from later detailed notes or reconciliation-only subtables
- use exact table column headers as `segment_name`
- extract only the latest/current fiscal or reporting period when a table presents multiple years
- prefer financial statement note segment disclosures over management discussion summaries when both are present
- distinguish true operating/business/reportable segments from product or geographical segmentation
- reject generic consolidated income statement line items such as `Revenue`, `Revenues`, `Sales`, `Turnover`, `Net revenue`, or `Net sales` when they are not actual segment names
- ignore ESG, EU taxonomy, climate-impact, energy-intensity, energy-use, emissions, fuel-consumption, debt, financing, asset, or green-financing tables unless they are explicitly the business/reportable/operating segment revenue table
- preserve raw values exactly
- infer currency and scale only from explicit nearby evidence
- return `null` for unknown unsupported fields
- include page, section, and evidence text
- produce valid JSON only

## Strict Output Schema

Provider output is validated against `RevenueExtractionOutput`.

Top-level fields:

- `company_name`
- `document_name`
- `fiscal_period`
- `reported_total`
- `currency`
- `scale`
- `rows`
- `extraction_warnings`

Row fields:

- `segment_name`
- `revenue_raw`
- `revenue_value`
- `currency`
- `scale`
- `period_label`
- `page_ref`
- `section_ref`
- `metric_basis`
- `evidence_text`
- `confidence`
- `extraction_notes`

Extra keys, missing keys, invalid JSON, empty segment names, and empty evidence text fail validation. The extraction service stores these failures as `ValidationIssue` records instead of crashing the app.

Before schema validation, the service extracts one complete JSON object from the provider response. Plain JSON, Markdown-fenced JSON, and JSON surrounded by short prose are accepted. Empty responses, responses with no JSON object, and incomplete JSON are rejected as `llm_output_validation` issues.

Second-pass verification is validated against `RevenueVerificationOutput`, which includes:

- `confirmed_rows`
- `suspected_errors`
- `missing_rows`
- `correction_suggestions`
- `rationale`

Arbitration is validated against `RevenueArbitrationOutput`, which includes:

- `accepted_rows`
- `rejected_rows`
- `missing_rows`
- `correction_suggestions`
- `requires_human_review`
- `rationale`

## Persistence

Valid first-pass rows are normalized, validated, scored, and stored in `segment_rows` with:

- `status = ready_for_review` when no deterministic issue is present
- `status = needs_review` when normalization, validation, verification, arbitration, or reconciliation raises review issues
- `extraction_method = <provider>:first_pass_revenue_segments_v1`
- normalized values calculated in Python from preserved raw values and explicit scale

Evidence is stored in `segment_evidence`. The service first tries to locate the evidence text in deterministic parsed page blocks. If no block match is found, it stores the model evidence text with no bbox and marks the parser source as `llm`.

Rows with blocking deterministic issues, such as expense/profit/asset/EBIT/EBITDA/tax metrics or consolidated income statement line items, are not persisted as core revenue segment rows. Their rejection is stored as a validation issue.

Verification and arbitration results and rationale are stored as `ValidationIssue` records. Arbitration keeps the existing `llm_opus_arbitration_*` issue types for backwards compatibility so review screens can distinguish arbitration from normal verifier output.

## Candidate Filtering

Extraction consumes stored candidate pages, but it does not send every candidate page to the provider. Before prompt construction, the service keeps pages with explicit revenue-segment anchors such as:

- `operating segments`
- `reportable segments`
- `segment reporting`
- `external revenue`
- `segment revenue`
- `revenue by segment`
- `net sales by segment`
- `disaggregation of revenue`

Weak terms such as `note` are not enough by themselves. To avoid hard-coding language lists, extraction can also keep a dense numeric table candidate when deterministic parsing shows table structure and enough numeric cells. The LLM prompt then performs the semantic revenue-segment decision for the document language and format, and Python validation still rejects unsupported rows before persistence.

When financial statement segment-note pages are available, extraction prefers those pages over MD&A summary pages and product/geographical segmentation pages. Product and geography revenue pages remain eligible when they are the available business activity segmentation evidence; sustainability, energy-use, taxonomy, financing, debt, and asset tables remain out of scope unless they are explicitly revenue segment tables.

Table-of-contents/index pages and accounting-standard update pages are excluded from extraction prompts even when they mention segment reporting. Segment-note intro pages can include the following parsed page when the next page appears to contain the revenue table.

If the deterministic windows produce no accepted rows and no provider/schema error occurred, LLM candidate discovery scans compact page summaries across the parsed document. This is a language-agnostic recall fallback for documents whose eligible table uses unfamiliar labels or appears outside the deterministic top-ranked pages. It does not add language-specific keyword lists to retrieval scoring and it does not bypass row validation.

Provider rows are also checked after schema validation. Rows are skipped and recorded as validation issues when:

- their `page_ref` is outside the prompt bundle
- their evidence appears to come from a non-segment ESG/taxonomy/energy-use style disclosure
- the segment label is only a generic consolidated revenue line item rather than a segment or segment-table total
- their `period_label` identifies a prior year while a later year is present in the accepted document rows
- they come from secondary segment tables after a stronger primary current-period segment table has been selected

For column-oriented primary segmentation tables, the expected first-pass behavior is one row per current-period table column in the revenue/external income line. Dash cells should still produce rows with the raw dash preserved and `revenue_value = null`.

Latest-period filtering is language-agnostic: it looks for four-digit years in accepted row period labels after strict LLM schema validation. Rows with no detectable year are kept for review to avoid dropping current-period rows when a provider cannot label the period.

For column-oriented tables, extraction also performs deterministic metric alignment after LLM validation. If the model selected an external revenue/income row but the same parsed page clearly contains a preferred total `Revenue` or `Total income` row with matching segment columns, Python replaces the row values with that preferred metric and keeps the evidence on the same page. This protects reports that disclose both external and intra-group revenue.

## Deduplication

Deduplication is conservative. A row is skipped only when all of these match another row:

- same normalized segment name
- same normalized value
- same fiscal period
- same page or nearby page
- same normalized evidence text

Rows with different evidence are preserved for human review.

## CLI Usage

Ingest first:

```bash
python scripts/ingest_pdf.py /path/to/annual-report.pdf --company-name "Example Corp"
```

Run fake extraction:

```bash
python scripts/extract_revenue_segments.py doc_... --provider fake
```

Run Anthropic extraction:

```bash
export RSE_EXTRACTION_PROVIDER=anthropic
export RSE_EXTRACTION_MODEL=claude-sonnet-4-6
export RSE_ARBITRATION_MODEL=claude-sonnet-4-6
export ANTHROPIC_API_KEY=...
python scripts/extract_revenue_segments.py doc_...
```

Use a different extraction model by setting `RSE_EXTRACTION_MODEL` or passing `--model`. Use a different arbitration model by setting `RSE_ARBITRATION_MODEL` or passing `--arbitration-model`.

## Verification

Run:

```bash
python -m unittest discover -s tests
```

The extraction tests do not call Anthropic. They use the fake provider and explicit invalid JSON fixtures.
