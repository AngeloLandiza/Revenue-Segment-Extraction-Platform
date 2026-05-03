# Data Contract

## Status

The internal data model is implemented as frozen dataclasses in `fitch_extractor/models.py`.
SQLite persistence is implemented in `fitch_extractor/persistence/`.
API-facing Pydantic response schemas are implemented in `fitch_extractor/api/schemas.py`.
PDF parsing and deterministic candidate retrieval are implemented in `fitch_extractor/ingestion/`.
First-pass LLM revenue extraction, post-extraction normalization/validation, optional second-pass verification, and optional LLM arbitration are implemented in `fitch_extractor/extraction/`.
NACE Rev.2 reference loading, deterministic candidate retrieval, optional LLM reranking, candidate persistence, reviewer selection/override, Streamlit review display, and export population are implemented.
ESG candidate retrieval, strict-schema LLM extraction, segment-link enforcement, reviewer editing/linking/approval/rejection, and export population are implemented.
Prototype scoring is implemented in `fitch_extractor/scoring.py` with config-driven NACE base scores, reviewed ESG adjustments, revenue weighting, Streamlit display, persistence, and export population.

HTTP API routes are not implemented yet.

## Core Export Columns

Final CSV/XLSX/JSON exports must include these core columns:

1. Company Name
2. Document Name
3. Segment Name
4. Revenue Value
5. Revenue Unit
6. Currency
7. Scale
8. Time Period
9. Page/Section Reference
10. Evidence Text
11. Review Status

The export writer maps `SegmentRow`, `Document`, `SegmentEvidence`, review events, and optional NACE selection state into CSV, XLSX, and JSON audit outputs.

## Storage

Local persistence uses SQLite. The default database path is:

```text
data/fitch_extractor.sqlite3
```

Initialize:

```bash
python scripts/manage_db.py
```

Reset:

```bash
python scripts/manage_db.py --reset
```

## Implemented Entities

### Document

Represents an annual report or 10-K source document.

Fields:

- `id`
- `company_name`
- `document_name`
- `source_path`
- `fiscal_period`
- `status`
- `reported_total`
- `currency`
- `scale`
- `created_at`
- `updated_at`
- `analysis_notes`

SQLite table: `documents`

Supported document statuses:

- `new`
- `parsed`
- `extracted`
- `validated`
- `needs_review`
- `ready_for_review`
- `approved`
- `rejected`
- `exported`
- `failed`

### ParsedPage

Represents deterministic page-level parser output.

Fields:

- `id`
- `document_id`
- `page_number`
- `text`
- `blocks_json`
- `tables_json`
- `language`
- `parser_sources`
- `has_text`
- `created_at`

SQLite table: `parsed_pages`

`blocks_json` stores:

- `page`: page width, height, and rotation from PyMuPDF.
- `blocks`: text blocks with `block_index`, `block_type`, `text`, `bbox`, and `parser_source`.
- `text_fallback`: OCR/vision fallback status. Current active values are `not_needed`, `available_not_configured`, `attempted_no_text`, `applied`, and `failed`.

`tables_json` stores:

- `tables`: pdfplumber tables with `table_index`, `source`, optional `bbox`, `rows`, and `cells`.
- `errors`: non-fatal pdfplumber table extraction errors recorded at page level.

`has_text = false` means PyMuPDF extracted too little meaningful text for downstream LLM use. OCR is not implemented yet.

`language` is currently stored as `unknown`. A language detector has not been added because the project does not yet have a lightweight, reliable dependency for this step.

### PageCandidate

Represents a deterministic candidate page before LLM extraction.

Fields:

- `id`
- `document_id`
- `page_number`
- `relevance_score`
- `matched_signals_json`
- `reason`

SQLite table: `page_candidates`

`matched_signals_json` can include:

- `terms`
- `table_density`
- `numeric_density`
- `currency_unit_terms`
- `business_line_terms`
- `total_terms`
- `proximity`

`reason` is a human-readable summary explaining why the page was ranked.

### SegmentRow

Represents one extracted or edited revenue segment row. First-pass LLM extraction now persists normalized/validated rows with `status = ready_for_review` or `status = needs_review`.

Fields:

- `id`
- `document_id`
- `segment_name`
- `revenue_raw`
- `revenue_value`
- `currency`
- `scale`
- `period_label`
- `normalized_value`
- `page_ref`
- `section_ref`
- `metric_basis`
- `confidence`
- `status`
- `extraction_method`
- `created_at`
- `updated_at`

SQLite table: `segment_rows`

Supported row statuses:

- `pending`
- `parsed`
- `extracted`
- `validated`
- `needs_review`
- `ready_for_review`
- `approved`
- `edited`
- `rejected`
- `exported`
- `failed`

### SegmentEvidence

Represents source evidence supporting a segment row.

Fields:

- `id`
- `segment_id`
- `document_id`
- `page_number`
- `snippet_text`
- `bbox_json`
- `parser_source`
- `evidence_kind`

SQLite table: `segment_evidence`

### NaceCandidate

Represents one ranked NACE Rev.2 mapping candidate for a segment row.

Fields:

- `id`
- `segment_id`
- `nace_code`
- `nace_label`
- `nace_level`
- `rank`
- `match_score`
- `rationale`

SQLite table: `nace_candidates`

The mapping service stores at most the top five candidates per segment. `nace_level` is the hierarchy depth from the CSV: section = 1, division = 2, group = 3, class = 4.

### NaceSelection

Represents the selected NACE mapping for a segment row.

Fields:

- `segment_id`
- `nace_code`
- `nace_label`
- `nace_level`
- `match_score`
- `rationale`
- `source`
- `reviewer`
- `updated_at`

SQLite table: `segment_nace_selections`

`source = candidate` is the automatic top candidate. `source = reviewer_accept` means the reviewer accepted a stored candidate. `source = reviewer_override` means the reviewer manually selected a different NACE code/label. Overrides are auditable through `ReviewEvent` rows with `action = override_nace_code`.

### ValidationIssue

Represents validation output that should be visible during review.

Fields:

- `id`
- `document_id`
- `segment_id`
- `severity`
- `issue_type`
- `message`
- `created_at`

SQLite table: `validation_issues`

First-pass extraction and post-processing create validation issues for:

- provider failures
- invalid JSON or schema-invalid LLM output
- warnings returned by the extraction output
- normalization warnings for raw value, currency, scale, period, and page reference handling
- evidence completeness issues
- currency, scale, and time period inconsistencies
- invalid metric basis rows such as expenses, losses, assets, profit, EBIT, EBITDA, tax, cost of sales, or finance income
- generic consolidated revenue line items returned as if they were segment names
- conservative duplicate skips
- duplicate segment candidates retained for review
- total reconciliation mismatches
- potential missing declared segments
- second-pass verification suspected errors, missing rows, correction suggestions, and rationale
- LLM arbitration results and rationale when a configured arbitration provider runs
- missing parsed candidate pages
- prior-period rows skipped because a later year was also extracted
- secondary segment-table rows skipped because a stronger primary current-period table was selected
- extracted rows with null parsed revenue values

## Strict First-Pass LLM Output Schema

The provider must return valid JSON matching `RevenueExtractionOutput` in `fitch_extractor/extraction/schemas.py`.

Top-level fields:

- `company_name`
- `document_name`
- `fiscal_period`
- `reported_total`
- `currency`
- `scale`
- `rows`
- `extraction_warnings`

Each row includes:

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

Unknown values must be `null`; missing required keys or extra keys are validation errors. The service validates the output before persistence and stores validation issues instead of crashing on invalid output.

## Normalization, Validation, and Deduplication

Raw `revenue_raw` is preserved. `revenue_value` is normalized in Python from the raw value when possible, falling back to the structured model value only when raw parsing fails. `normalized_value` is calculated from the normalized numeric value and explicit scale when available:

- `actuals` / `ones`: multiplier `1`
- `thousands`: multiplier `1000`
- `millions`: multiplier `1000000`
- `billions`: multiplier `1000000000`

Dash-only, em dash, hyphen-only, and blank values are not treated as numeric zero by default. They are stored with `revenue_value = null`, `normalized_value = null`, and a review-visible warning unless table context explicitly defines dashes as zero.

Deterministic validation runs before persistence. Blocking non-revenue rows are rejected as core segment rows. Non-blocking issues set row status to `needs_review`; clean rows are stored as `ready_for_review`.

Conservative deduplication skips rows only when segment name, normalized value, fiscal period, same or nearby page, and normalized evidence text all match. Rows with different evidence remain available for review.

Second-pass verification and LLM arbitration are optional provider-backed checks for uncertain cases. Arbitration defaults to the extraction model, is disabled by default, is skipped for clean documents, and persists `llm_opus_arbitration_*` validation issues when it runs. The `llm_opus_arbitration_*` issue type prefix is retained for backwards compatibility.

## Extraction Candidate Eligibility

First-pass extraction stores all parsed pages and page candidates, but the LLM prompt receives only pages with explicit revenue-segment anchors or dense numeric table structure. The structural fallback is intentionally language-agnostic: it does not enumerate every possible segment or revenue translation, and it relies on the LLM prompt plus Python validation to decide whether the page actually contains business/reportable/operating segment revenue.

When financial statement segment-note pages are available, they are preferred over management discussion summary tables and product/geographical segmentation pages. Product and geography revenue/business activity segmentation remains valid when it is the report's presented revenue segmentation. Rows returned for pages outside the prompt bundle, or rows that appear to come from ESG, taxonomy, energy-use, emissions, financing, debt, or asset disclosures, are stored as validation issues instead of segment rows.

For primary segmentation tables, first-pass extraction should preserve the current-period table columns as reviewable rows. Reconciliation columns such as `Eliminations`, `Total`, `Reclassification to reported and incidental items`, and `Reported` are valid rows when they are part of the segment revenue table. Dash cells are stored with the raw dash and a null numeric value for review.

When accepted LLM rows include multiple detected years in `period_label`, first-pass extraction persists only rows for the latest detected year. Rows without a detectable year are retained for review because dropping them could remove valid current-period evidence from non-standard tables.

When accepted LLM rows select an external revenue/income row but the same parsed page clearly contains a preferred total revenue/income row for the same segment columns, deterministic post-processing can update `revenue_raw`, `revenue_value`, `metric_basis`, and evidence text before persistence. This keeps the stored row tied to the total current-period segment revenue/income metric instead of a customer-only submetric.

When multiple page groups contain complete current-period segment tables, first-pass extraction keeps the strongest primary table and records skipped secondary rows as validation issues. The stored rows are then set to `ready_for_review` or `needs_review`.

If parsed pages exist but no candidate page is eligible for extraction, the validation issue is a warning. This means the pipeline found no explicit segment revenue table; it is not treated as a technical extraction failure.

If deterministic extraction candidates produce no accepted rows, the extraction service may run an LLM candidate-discovery fallback over compact summaries of parsed pages. Discovery returns page numbers only; any rows from those pages must still satisfy the normal strict extraction schema and Python validation before persistence.

### EsgFactor

Represents a stored ESG factor candidate extracted from deterministic ESG candidate pages and strict-schema LLM output. Segment linkage is conservative: a factor is linked to `segment_id` only when evidence explicitly names the segment or clearly ties the ESG factor to that segment's business activity, asset type, product line, geography, or named project. Otherwise `segment_id = null` and `is_company_wide = true`.

Fields:

- `id`
- `segment_id`
- `document_id`
- `factor_type`
- `polarity`
- `description`
- `page_ref`
- `evidence_text`
- `confidence`
- `is_company_wide`

SQLite table: `esg_factors`

Supported `factor_type` values:

- `emissions_target`
- `decarbonization_plan`
- `renewable_investment`
- `fossil_fuel_exposure`
- `coal_phaseout`
- `controversy`
- `regulatory_violation`
- `safety_incident`
- `social_program`
- `labor_issue`
- `circular_economy`
- `biodiversity_impact`
- `water_risk`
- `governance_policy`
- `company_wide_policy`
- `other`

Supported `polarity` values:

- `positive`
- `negative`
- `neutral`
- `mixed`
- `unknown`

ESG review status is derived from `ReviewEvent` rows with `field_changed = esg_factor:{factor_id}:status`. Current review statuses are:

- `pending`
- `approved`
- `edited`
- `rejected`

ESG edit, unlink, relink, approve, and reject actions are audit events. The factor row stores current field values; old values remain in review events.

### SegmentScore

Represents stored segment-level prototype score output. This is a class/project demonstration score only, not an official Fitch Ratings or Sustainable Fitch score.

Fields:

- `id`
- `segment_id`
- `base_score`
- `adjustment_score`
- `final_score`
- `weight_share`
- `rationale`

SQLite table: `segment_scores`

`rationale` stores JSON with the model label, scale, NACE lookup rationale, ESG adjustment details, and revenue denominator source.

### CompanyScore

Represents the latest company-level prototype score summary for a document.

Fields:

- `id`
- `document_id`
- `weighted_average_score`
- `included_weight_share`
- `included_segment_count`
- `denominator_value`
- `scale_min`
- `scale_max`
- `score_direction`
- `rationale`
- `created_at`

SQLite table: `company_scores`

`rationale` stores JSON with the weighted-average formula, denominator source, and segment contribution details.

### ReviewEvent

Represents a human review action.

Fields:

- `id`
- `document_id`
- `segment_id`
- `reviewer`
- `action`
- `field_changed`
- `old_value`
- `new_value`
- `note`
- `timestamp`

SQLite table: `review_events`

### ExportRecord

Represents export metadata after reviewed CSV, XLSX, and JSON audit files are created.

Fields:

- `id`
- `document_id`
- `format`
- `path`
- `created_at`

SQLite table: `export_records`

## Review Gate

`SQLiteRepository.create_export_record()` enforces the review gate by default. It raises `ValueError` if any segment row for the document is not in one of these statuses:

- `approved`
- `edited`
- `rejected`

Rows with `pending` status block export metadata creation.
Rows with `needs_review` or `ready_for_review` also block export until a reviewer changes them to `approved`, `edited`, or `rejected`.
