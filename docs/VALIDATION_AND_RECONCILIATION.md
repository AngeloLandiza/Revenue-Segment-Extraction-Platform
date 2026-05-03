# Validation and Reconciliation

## Scope

The post-extraction layer runs after first-pass LLM extraction and before review-ready rows are persisted. It normalizes raw values, applies deterministic validation, computes confidence, and only then optionally calls second-pass verification or arbitration for uncertain cases.

This layer does not implement NACE mapping, ESG extraction, scoring, or final export writing.

## Normalization

Implemented in `revenue_segment_extractor/extraction/normalization.py`.

The normalizer preserves raw row values and stores warnings for ambiguous or failed normalization. It handles:

- Currency symbols and codes such as `$`, `US$`, `USD`, `EUR`, `€`, `GBP`, `£`, `CHF`, `DKK`, `SEK`, `NOK`, `CAD`, `AUD`, `JPY`, `CNY`, `RMB`, and `HKD`.
- Scale terms: `actuals`, `ones`, `units`, `thousands`, `$000`, `millions`, `USDm`, `billions`, and common `mn`/`bn` labels.
- Parenthesized negatives, leading negatives, and trailing dash negatives.
- Thousands separators, decimal values, and mixed comma/dot separators.
- Dash-only, em dash, hyphen-only, and blank values as missing values, not zero.
- Fiscal period labels normalized to forms such as `FY2025` or `Q1 2025`.
- Page references normalized to `p. N` or `pp. N-M`.

If a dash must mean zero, callers must explicitly pass context that allows it. The default is always non-numeric missing.

## Deterministic Checks

Implemented in `revenue_segment_extractor/extraction/validation.py`.

Checks include:

- Evidence completeness: page reference and evidence text.
- Currency consistency across rows and against document context.
- Scale consistency across rows and against document context.
- Time period consistency across rows and against document context.
- Metric basis correctness. Revenue, revenues, net sales, turnover, external revenue, external income, total income, and configured revenue-equivalent metrics are valid. Expenses, losses, assets, profit, EBIT, EBITDA, tax, cost of sales, and finance income are rejected for core revenue rows unless validation is explicitly configured to allow non-revenue metrics.
- Duplicate segment candidates for the same normalized segment and period.
- Total reconciliation against explicit total rows or document `reported_total`.
- Declared segment coverage gaps when a parsed table names a segment that was not extracted.
- Consolidated income statement false positives such as cost of sales, gross profit, finance income, tax, expenses, assets, profit, EBIT, or EBITDA.
- Geography, product, business line, and operating division rows when the report presents them as revenue or business activity segmentation.

Blocking validation issues prevent invalid core rows from being persisted. Non-blocking issues persist with the row and set the row status to `needs_review`.

## Reconciliation

Reconciliation compares the sum of non-total rows to the best available target total:

1. Explicit segment-table total row, if present.
2. Document-level `reported_total`, if no explicit total row is available.

Total rows are excluded from the segment sum so they are not double-counted. Reclassification or reported reconciliation rows are excluded when comparing to an explicit segment total.

Default tolerance is:

- absolute: `1`
- relative: `0.5%`

A mismatch creates `total_reconciliation_mismatch` and forces human review.

## Confidence

Implemented in `revenue_segment_extractor/extraction/confidence.py`.

Row confidence combines:

- first-pass extraction confidence,
- evidence completeness,
- normalization success,
- validation issues,
- page relevance score,
- reconciliation result.

The score is stored on `SegmentRow.confidence`.

## Verification and Opus Arbitration

Second-pass verification is implemented in `revenue_segment_extractor/extraction/verification.py`. It runs only when validation creates warning or error issues and a verification provider is configured.

LLM arbitration is implemented in `revenue_segment_extractor/extraction/arbitration.py`. It runs only when deterministic validation or second-pass verification fails, arbitration is enabled, and an arbitration provider is configured. The arbitration model defaults to the extraction model and is separately configurable through `RSE_ARBITRATION_MODEL`.

Both paths use strict Pydantic schemas and fakeable providers. Results and rationale are stored as `ValidationIssue` records. Arbitration keeps the existing `llm_opus_arbitration_*` issue type names for backwards compatibility and is skipped for clean documents.

## Verification

Run:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall revenue_segment_extractor scripts tests
```
