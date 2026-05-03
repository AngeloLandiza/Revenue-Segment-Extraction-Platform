# Prompt Templates

All LLM prompts are built in `revenue_segment_extractor/extraction/prompts.py`. Each prompt includes a version string, strict JSON-only instruction, document context, and a Pydantic-generated schema.

## `first_pass_revenue_segments_v1`

Used by `RevenueExtractionService` for first-pass segment row extraction.

Template includes:

- extract revenue, turnover, net sales, external revenue, total income, or clearly revenue-equivalent segment metrics;
- preserve original segment labels and raw values;
- prefer financial statement note segment disclosures;
- use the latest/current reporting period only;
- avoid expenses, losses, assets, profit, EBIT, EBITDA, tax, ESG, EU taxonomy, energy-use, debt, and non-revenue tables;
- keep dash-only cells as null values with raw dash evidence;
- return page reference, section reference, and concise evidence text;
- match `RevenueExtractionOutput`.

## `candidate_page_discovery_v1`

Used when deterministic candidate pages do not produce usable extraction rows.

Template includes:

- identify pages likely to contain revenue-equivalent segment tables;
- support multilingual and regional reporting terms;
- exclude balance sheet, risk exposure, solvency, ESG/climate, employee, accounting policy, and consolidated line-item pages;
- prefer pages with explicit tables;
- return at most the configured number of pages;
- match `CandidateDiscoveryOutput`.

## `second_pass_verification_v1`

Used when deterministic validation finds uncertainty.

Template includes:

- verify extracted rows against page snippets;
- confirm only revenue or revenue-equivalent segment rows;
- reject expenses, losses, assets, profit, EBIT, EBITDA, tax, costs, and consolidated line items;
- avoid treating dash-only cells as numeric zero unless explicitly defined;
- report suspected errors, missing rows, correction suggestions, and rationale;
- match `RevenueVerificationOutput`.

## `revenue_arbitration_v1`

Used for difficult cases after validation and optional verification.

Template includes:

- arbitrate rows using the same project rules;
- confirm, reject, or suggest corrections with evidence-grounded rationale;
- mark `requires_human_review` when ambiguity remains;
- match `RevenueArbitrationOutput`.

## `esg_extraction_v1`

Used by `EsgExtractionService`.

Template includes:

- extract only ESG actions, risks, targets, controversies, violations, incidents, impacts, or programs with explicit evidence;
- use controlled factor type and polarity values;
- mark company-wide factors as company-wide unless evidence explicitly links them to a listed segment or business activity;
- do not attach company-wide ESG context to all segments;
- exclude ESG indexes, table-of-contents pages, generic governance boilerplate, and cross-reference text unless materially relevant;
- match `EsgExtractionOutput`.

## NACE LLM Prompts

NACE mapping prompts are built in `revenue_segment_extractor/nace/service.py` and reranking prompts in `revenue_segment_extractor/nace/rerank.py`.

Templates include:

- segment name, company/document context, row evidence, nearby page context, and a deterministic candidate list;
- a strict instruction to choose only from supplied NACE candidates;
- explicit `mapped`, `not_applicable`, or `needs_review` decisions;
- rejection of totals, eliminations, roll-ups, reported rows, reclassifications, hedging rows, and reconciliation-only rows as operating activities;
- reviewer override preservation through persistence logic.
