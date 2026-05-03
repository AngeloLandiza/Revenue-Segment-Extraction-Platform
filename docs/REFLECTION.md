# Reflection

## What Was Straightforward To Extract With AI?

Clear segment tables with explicit headers such as `Segment`, `External revenue`, `Revenue`, `Net sales`, `Turnover`, currency, scale, and current fiscal period were the most straightforward. The LLM is useful for interpreting table context, multilingual equivalents, and narrative evidence around the table.

## What Required The Most Iteration?

The hardest iteration was separating true revenue segment rows from nearby financial statement content: expenses, profit, assets, consolidated line items, totals, reconciliations, prior-year columns, and ESG/EU taxonomy revenue-like tables.

## What Segment Disclosures Are Most Challenging?

Challenging disclosures include scanned PDFs, tables split across pages, column-oriented segment tables, banks and insurers with revenue-equivalent metrics, non-English reports, multiple segment bases, and tables that include eliminations or reclassification bridges.

## Where Is Human Review Still Necessary?

Human review is still necessary for final evidence judgment, ambiguous metrics, reconciliation exceptions, NACE overrides, ESG linkage, manual row additions, and final approval. The app intentionally blocks final export until review statuses are complete.

## Confidence In Final Reviewed Outputs

Confidence is highest after every row has page evidence, normalized currency/scale/period, no unresolved blocking validation issues, reviewed NACE/ESG decisions, and a document-level approval event. Unreviewed LLM output should be treated as draft only.
