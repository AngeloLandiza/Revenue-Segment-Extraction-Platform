# Performance Analysis

## Current Measured Status

The repository includes automated coverage for ingestion, deterministic candidate selection, fake-provider extraction, normalization, validation, review gating, NACE mapping, ESG extraction, scoring, exports, and evaluation report generation.

No labeled production gold set is included in this checkout, so extraction success rate should be reported from `python -m fitch_extractor.evaluate` after running reviewed exports against class-provided or manually labeled gold files.

## Metrics To Report

- Extraction success rate: evaluator precision, recall, F1, and exact value accuracy.
- Reconciliation results: reconciliation pass rate and validation issue counts from audit exports.
- Failure modes: counts in `failure_analysis.md`.
- Reviewer effort: reviewer edit rate and validation issue review counts.
- Known limitations: false positives, missing rows, wrong scale/currency, OCR failures, non-English issues, NACE ambiguity, ESG over-linking.

## Regression Coverage

Automated tests cover the main known failure modes:

- expenses, losses, assets, profit, EBIT, and EBITDA are not accepted as revenue segment rows;
- duplicate rows are deduplicated;
- dash-only values are preserved as missing values and flagged;
- total rows are excluded from scoring/reconciliation double-counting where appropriate;
- company-wide ESG factors are not attached to every segment;
- rejected rows are excluded from final CSV/XLSX;
- unapproved documents cannot export.

## Known Failure Modes

- PDFs with poor table extraction may need OCR or manual row entry.
- Reports that use non-standard revenue-equivalent metrics need careful reviewer validation.
- Segment tables with multiple current-period revenue definitions can require arbitration.
- NACE codes are approximate without reviewer confirmation.
- ESG factors can be materially relevant but still too broad to assign to a segment.

## Reviewer Effort

Reviewer effort is expected to be concentrated on evidence inspection, validation issue resolution, NACE accept/override decisions, ESG linkage review, and final approval. The audit export preserves review events so effort can be measured per document.
