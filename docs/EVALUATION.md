# Evaluation

The evaluation harness compares reviewed prediction exports against manually labeled gold files. It is intended for small, explicit validation sets only. Do not treat sample/reference data as ground truth unless the file was manually labeled and stored as a gold set.

## Gold Set Format

Gold files may be CSV or JSON. CSV is preferred for manual labeling.

Required CSV columns:

```text
document_name,company_name,fiscal_period,segment_name,revenue_value,currency,scale,page_ref
```

Optional CSV columns:

```text
nace_code,notes
```

Example:

```csv
document_name,company_name,fiscal_period,segment_name,revenue_value,currency,scale,page_ref,nace_code,notes
annual-report.pdf,Example Co,FY2025,Insurance,42,USD,millions,p. 12,65.12,
annual-report.pdf,Example Co,FY2025,Asset Management,8,USD,millions,p. 12,,OCR issue if missed
```

JSON gold files may either be a list of row objects or an object with a `rows` list using the same field names.

## Prediction Inputs

The evaluator accepts:

- `exports/{document_id}/audit_export.json`
- `exports/{document_id}/revenue_segments.csv`
- export directories containing either file
- globs that resolve to any of the above

When both files are present in an export directory, `audit_export.json` is preferred because it includes validation issues, review events, NACE selections, and timestamps.

## Matching

Rows are matched within the same `document_name` using:

- normalized segment-name similarity
- normalized revenue value after applying `scale`
- fiscal period normalization
- page reference when both gold and prediction include a parseable page number

Default thresholds:

- segment similarity floor: `0.68`
- weighted match score floor: `0.62`
- value relative tolerance: `0.005`
- value absolute tolerance: `1`

Thresholds are configurable from the CLI.

## Metrics

The report computes:

- precision
- recall
- F1
- exact value accuracy
- page reference accuracy
- reconciliation pass rate
- reviewer edit rate
- average validation issues per document
- average time per document when timestamps are available

Precision and recall count rows as correct when the matched segment has the expected normalized value, fiscal period, currency, and scale. Page reference quality is reported separately.

## Failure Taxonomy

Failures are classified into:

- `missing_segment`
- `false_positive_line_item`
- `wrong_value`
- `wrong_unit_scale`
- `wrong_period`
- `duplicate_segment`
- `total_handling_error`
- `table_parsing_error`
- `non_english_issue`
- `scanned_ocr_issue`
- `nace_ambiguity`
- `esg_over_linking`

The optional `notes` field can guide classification for issues that are not inferable from row values, such as OCR, non-English, NACE ambiguity, ESG over-linking, and table parsing errors.

## Run Evaluation

```bash
.venv/bin/python -m fitch_extractor.evaluate --gold 'data/gold/*.csv' --pred exports
```

Useful threshold overrides:

```bash
.venv/bin/python -m fitch_extractor.evaluate \
  --gold 'data/gold/*.csv' \
  --pred exports \
  --segment-threshold 0.72 \
  --match-threshold 0.66 \
  --value-relative-tolerance 0.001 \
  --reports-dir reports
```

## Generated Reports

The CLI writes:

- `reports/evaluation_summary.md`
- `reports/evaluation_results.csv`
- `reports/failure_analysis.md`

These reports are regenerated from the supplied inputs and do not contain hard-coded results.
