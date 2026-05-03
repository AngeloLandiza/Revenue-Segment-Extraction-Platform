# Pipeline Walkthrough

This document explains the Fitch revenue-segment extraction pipeline end to end. It is written as an implementation guide for the local prototype, not as a marketing overview.

The core design is hybrid:

1. Parse and retrieve evidence deterministically first.
2. Send only narrowed evidence windows to LLMs.
3. Validate every LLM response against strict schemas.
4. Normalize and reconcile values in Python.
5. Require human review before final export.

## 1. Configuration Is Loaded

1. Runtime settings are read from environment variables in `fitch_extractor/extraction/config.py`.
2. The extraction provider is selected with `FITCH_EXTRACTION_PROVIDER`.
   - `anthropic` calls the Anthropic API.
   - `fake` returns deterministic local fixture output for smoke tests.
3. The extraction model is selected with `FITCH_EXTRACTION_MODEL`.
4. Second-pass verification defaults to the extraction model unless `FITCH_VERIFICATION_MODEL` is set.
5. Arbitration is disabled by default. If `FITCH_ENABLE_ARBITRATION=true`, the arbitration model defaults to the extraction model unless `FITCH_ARBITRATION_MODEL` is explicitly set.
6. Page bundle size, max output tokens, and temperature are also environment-driven.
7. API keys are read from environment variables and are not stored in SQLite, exports, validation issues, or logs.

## 2. A Document Enters The System

1. The analyst uploads a PDF in Streamlit or ingests one through `scripts/ingest_pdf.py`.
2. A `Document` row is created with:
   - company name
   - document name
   - local source path
   - fiscal period hint
   - currency hint
   - scale hint
   - status
3. If the analyst provides company, period, currency, or scale, those values are treated as document-level hints.
4. If the analyst leaves metadata blank, the parser tries conservative auto-detection only when early-page evidence is clear.

## 3. PDF Text And Layout Are Parsed

1. `parse_pdf()` opens the PDF with PyMuPDF and pdfplumber.
2. PyMuPDF extracts page-level text and text blocks.
3. pdfplumber extracts table structures where available.
4. Each page becomes a parsed page record with:
   - 1-based `page_number`
   - full extracted `text`
   - text blocks with block-level bounding boxes
   - table rows and cells
   - parser source metadata
   - page dimensions
   - language hint
   - `has_text`
5. A page is treated as weak/no-text when PyMuPDF finds fewer than 30 alphanumeric characters.
6. Optional OCR or vision fallback can be applied only to weak/no-text pages. Fallback is disabled by default.
7. Fallback status is recorded so reviewers can see whether evidence came from native text, OCR, vision, or fallback text.

## 4. Lightweight Language Hints Are Assigned

1. The parser uses deterministic marker terms to infer language hints such as `en`, `es`, `fr`, `de`, `it`, `pt`, or `unknown`.
2. This is not full language detection.
3. It looks for common segment and revenue words such as:
   - English: operating segments, reportable segments, revenue, sales, turnover
   - Spanish: segmentos operativos, ingresos, ventas
   - French: secteurs operationnels, chiffre d affaires, produits
   - German: operative segmente, umsatz, erloese
4. If there is not enough signal, the page stays `unknown`.
5. LLM extraction is still responsible for semantic interpretation across languages.

## 5. Deterministic Page Keyword Search Runs Before LLM Calls

1. The pipeline does not send the whole PDF to the LLM.
2. `score_pages()` performs deterministic page retrieval first.
3. It searches each parsed page for revenue-segment signals, including terms such as:
   - operating segments
   - reportable segments
   - business segments
   - segment reporting
   - external revenue
   - segment revenue
   - revenue by segment
   - net sales by segment
   - turnover by segment
   - disaggregation of revenue
4. This is keyword search in the practical sense: normalized page text is scanned for curated terms and term families.
5. Keyword matches are not treated as final truth. They only decide which pages deserve closer inspection.
6. The scoring also uses structural signals:
   - table density
   - numeric density
   - currency terms
   - unit terms such as millions, thousands, USDm, EURm
   - repeated business-line labels
   - total or consolidation terms
7. Each candidate page stores a relevance score and a JSON explanation of matched signals.

## 6. Candidate Pages Are Selected And Expanded

1. `select_candidate_pages()` ranks pages by deterministic relevance score.
2. The default ingestion candidate limit is intentionally small enough to avoid whole-document prompting.
3. Adjacent pages can be included when a high-scoring page appears to continue onto the next or previous page.
4. Adjacent pages still need their own deterministic signal; the pipeline does not blindly include long page ranges.
5. The selected pages are persisted in `page_candidates`.
6. This gives the analyst and downstream services an auditable record of why a page was considered.

## 7. Extraction Candidate Filtering Narrows Further

1. Revenue extraction consumes stored candidate pages but filters them again before prompting.
2. The extraction service keeps pages with explicit revenue-segment anchors.
3. Weak generic terms such as `note` are not enough by themselves.
4. Table-of-contents and index pages are excluded even if they mention segment reporting.
5. Accounting-standard update pages are excluded when they are not actual segment revenue disclosures.
6. Sustainability, taxonomy, emissions, energy-use, financing, debt, and asset tables are excluded unless they are explicitly business/reportable/operating segment revenue tables.
7. If a segment-note intro page is selected, the following parsed page can be included when it appears to contain the actual revenue table.
8. Financial statement segment-note pages are preferred over management discussion summary pages when both are available.

## 8. Candidate Pages Are Bundled For The First LLM Pass

1. Candidate pages are grouped into small bundles.
2. The bundle size defaults to `2` pages and is controlled by `FITCH_EXTRACTION_PAGE_BUNDLE_SIZE`.
3. Each bundle prompt includes:
   - document metadata
   - selected page text
   - selected page table data
   - strict extraction instructions
   - the required JSON schema
4. The LLM is instructed to return JSON only.
5. The prompt version for this pass is `first_pass_revenue_segments_v1`.

## 9. The First LLM Pass Extracts Revenue Rows Only

1. The first LLM pass is allowed to extract only revenue or revenue-equivalent segment metrics.
2. Allowed metric concepts include:
   - revenue
   - revenues
   - net sales
   - turnover
   - external revenue
   - external income
   - total income
   - total operating income
3. The model is told to reject:
   - expenses
   - losses
   - assets
   - liabilities
   - profit
   - EBIT
   - EBITDA
   - tax
   - employee counts
   - emissions or energy metrics
4. The model must preserve raw values exactly as shown in the source.
5. The model must include page reference, section reference, and evidence text.
6. The model should infer currency and scale only from explicit nearby evidence.
7. Unknown fields must be returned as `null`, not guessed.

## 10. LLM Output Is Parsed And Schema-Validated

1. The service first extracts one JSON object from the provider response.
2. Plain JSON, fenced JSON, and JSON surrounded by short prose are tolerated.
3. Empty responses, incomplete JSON, and responses with no JSON object are rejected.
4. The JSON is validated with Pydantic against `RevenueExtractionOutput`.
5. Required row fields include:
   - segment name
   - raw revenue
   - numeric revenue value
   - currency
   - scale
   - period label
   - page reference
   - section reference
   - metric basis
   - evidence text
   - confidence
6. Extra keys and missing required keys fail validation.
7. Validation failures are persisted as validation issues instead of crashing the app.

## 11. Provider Rows Are Checked Against The Prompt Bundle

1. After schema validation, rows are still checked deterministically.
2. A row is skipped when its page reference is outside the prompted page bundle.
3. A row is skipped when evidence appears to come from non-segment ESG, taxonomy, energy-use, asset, debt, or financing content.
4. A row is skipped when the segment label is only a generic consolidated revenue line item and not a segment or segment-table total.
5. Rows from prior-year columns are removed when a later/current reporting year is also extracted.
6. Rows from secondary segment tables are skipped after the strongest primary current-period segment table is selected.

## 12. Python Normalization Converts Raw Values Safely

1. Normalization runs in Python, not in the LLM.
2. Currency symbols and codes are normalized, including common USD, EUR, GBP, CHF, CAD, AUD, JPY, CNY, and related variants.
3. Scale terms are normalized, including:
   - actuals
   - units
   - thousands
   - millions
   - billions
   - `$000`
   - `USDm`
   - `mn`
   - `bn`
4. Numeric parsing handles:
   - thousands separators
   - decimal values
   - comma/dot variants
   - parenthesized negatives
   - leading negatives
   - trailing dash negatives
5. Dash-only and blank values are treated as missing, not zero.
6. Period labels are normalized to forms such as `FY2025`.
7. Page references are normalized to forms such as `p. 12` or `pp. 12-13`.
8. The original raw value is preserved for auditability.

## 13. Deterministic Validation Runs Before Persistence

1. The validation layer checks whether normalized rows are usable revenue-segment rows.
2. It checks required evidence fields.
3. It checks currency, scale, and period consistency across rows and against document hints.
4. It rejects unsupported metric bases such as expense, loss, asset, profit, EBIT, EBITDA, tax, finance income, or cost of sales.
5. It detects duplicate segment candidates for the same segment and period.
6. It detects consolidated income statement false positives.
7. It checks declared segment coverage gaps when parsed tables show named segments that were not extracted.
8. Blocking validation issues prevent bad rows from becoming core segment rows.
9. Non-blocking validation issues keep the row but mark it `needs_review`.

## 14. Reconciliation Compares Segment Sums To Totals

1. Reconciliation selects the best available total target.
2. The preferred target is an explicit segment-table total row.
3. If no explicit segment-table total exists, the service can compare against `documents.reported_total`.
4. Total rows are excluded from the segment sum to avoid double-counting.
5. Reclassification or reported-only reconciliation rows are excluded when comparing to an explicit segment total.
6. The default tolerance is:
   - absolute tolerance: `1`
   - relative tolerance: `0.5%`
7. A mismatch creates a `total_reconciliation_mismatch` issue.
8. Reconciliation mismatches force human review.

## 15. Confidence Is Computed From Multiple Signals

1. Row confidence is computed in Python.
2. Inputs include:
   - LLM confidence
   - evidence completeness
   - normalization success
   - validation issues
   - page relevance score
   - reconciliation result
3. The score is stored on the segment row.
4. Confidence is a review aid, not an automatic approval.

## 16. Optional Second-Pass Verification Runs For Uncertain Rows

1. Second-pass verification is enabled by default.
2. It runs only when deterministic validation creates warning or error issues and a verification provider is configured.
3. The verifier receives:
   - page snippets
   - normalized extracted rows
   - validation issues
   - strict JSON schema
4. The prompt version is `second_pass_verification_v1`.
5. The verifier can return confirmed rows, suspected errors, missing rows, correction suggestions, and rationale.
6. Its output is schema-validated.
7. Results are stored as validation issues for analyst review.

## 17. Optional Arbitration Runs Only For Hard Cases

1. Arbitration is disabled by default.
2. It runs only when:
   - `FITCH_ENABLE_ARBITRATION=true`
   - an arbitration provider is configured
   - deterministic validation or verification leaves unresolved uncertainty
3. The arbitration model defaults to the extraction model unless `FITCH_ARBITRATION_MODEL` is explicitly set.
4. The prompt version is `revenue_arbitration_v1`.
5. Arbitration can accept rows, reject rows, identify missing rows, suggest corrections, and require human review.
6. Its output is schema-validated.
7. Results are stored as validation issues.
8. Arbitration never replaces the final human review gate.

## 18. Evidence Text Is Matched Back To PDF Blocks

1. For accepted revenue rows, the service tries to locate the evidence text in parsed page blocks.
2. `locate_evidence_snippet()` performs normalized exact matching first.
3. If exact matching fails, it uses token-overlap matching.
4. A match stores:
   - page number
   - evidence snippet
   - parser source
   - match type
   - block index
   - block-level bounding box
5. If no parsed block match is found, the LLM evidence text is still stored, but bbox is `null` and parser source is `llm`.
6. Bounding boxes are block-level references, not word-perfect proof.

## 19. Segment Rows And Evidence Are Persisted

1. Valid rows are stored in `segment_rows`.
2. Evidence is stored separately in `segment_evidence`.
3. Rows with no review issues are usually stored as `ready_for_review`.
4. Rows with review issues are stored as `needs_review`.
5. Each row stores extraction method such as `<provider>:first_pass_revenue_segments_v1`.
6. Original evidence rows are not overwritten during review.
7. Analyst edits are recorded as review events.

## 20. LLM Candidate Discovery Can Run As A Recall Fallback

1. If deterministic candidate windows produce no accepted rows and no provider/schema error occurred, the service can run LLM candidate-page discovery.
2. This fallback scans compact page summaries, not the whole raw document.
3. The prompt version is `candidate_page_discovery_v1`.
4. It returns page numbers and reasons only.
5. Discovered pages still go through the normal first-pass extraction, schema validation, normalization, validation, and review workflow.
6. This improves recall without bypassing deterministic safeguards.

## 21. NACE Candidate Mapping Runs After Revenue Rows Exist

1. NACE mapping is downstream of revenue extraction.
2. `NaceMappingService` loads `reference/NACE_Rev2_Outline.csv`.
3. Each NACE node preserves section, division, group, class, hierarchy paths, labels, and source row metadata.
4. For each reviewable segment row, the service combines:
   - company name
   - document name
   - segment name
   - segment evidence snippets
   - nearby parsed report context
5. Deterministic candidate retrieval uses:
   - normalized token overlap
   - label and hierarchy-path matching
   - fuzzy label similarity
   - keyword-overlap rationale
   - specificity bonuses for more granular NACE levels
   - domain hints for obvious utilities, power, network, cloud, hosting, and energy-distribution contexts
6. This is another keyword and token search stage, but against the NACE reference and segment context rather than PDF pages.
7. The top candidates are sent to the LLM classifier with prompt version `nace_mapping_v2`.
8. The LLM must choose only from supplied candidates.
9. Invented NACE codes are rejected.
10. Totals, eliminations, roll-ups, reported-only rows, and reconciliation rows are marked not applicable.
11. Human reviewer selections and overrides are preserved on rerun.

## 22. ESG Candidate Retrieval Runs Separately

1. ESG extraction is downstream of parsed pages and known segment rows.
2. It does not replace revenue extraction.
3. Deterministic retrieval scans parsed pages for ESG terms such as:
   - emissions target
   - greenhouse gas
   - net zero
   - decarbonization
   - energy transition
   - renewable
   - solar
   - wind
   - hydrogen
   - battery storage
   - fossil fuel
   - coal
   - litigation
   - safety incident
   - labor
   - water risk
   - biodiversity
   - governance
4. ESG retrieval boosts pages near known segment evidence pages.
5. It also boosts pages in MD&A, business review, and risk sections.
6. Generic indexes, ESG indexes, SASB/GRI indexes, cross-reference pages, tables of contents, and boilerplate governance text are filtered where possible.
7. The selected ESG pages and known segment names are sent to the LLM with prompt version `esg_extraction_v1`.
8. The LLM must return strict ESG JSON with:
   - factor type
   - polarity
   - description
   - page reference
   - evidence text
   - confidence
   - segment linkage
   - company-wide flag
9. Python post-processing enforces conservative segment-link rules.
10. An ESG factor links to a segment only when evidence explicitly names the segment or clearly ties the factor to that segment's business activity, asset type, product line, geography, or named project.
11. Unclear ESG factors stay company-wide and are not copied to every segment.

## 23. ESG Evidence Highlighting Runs On Demand

1. ESG factors store page reference and evidence text.
2. They do not currently store persisted bbox JSON.
3. In the ESG review tab, the selected ESG factor's page reference is parsed into a page number.
4. The app finds that parsed page in SQLite.
5. It runs `locate_evidence_snippet()` against the ESG evidence text.
6. If a parsed block match is found, the same PDF page highlight renderer is used.
7. If no match is found, the analyst still sees the page reference and evidence text.
8. This keeps ESG highlighting minimal and avoids a database migration.

## 24. Review Is The Final Quality Gate

1. The Streamlit review UI is the analyst-facing quality gate.
2. Analysts review:
   - revenue segment rows
   - revenue values
   - currency
   - scale
   - period
   - page/section references
   - source evidence text
   - highlighted PDF page evidence when available
   - validation issues
   - NACE candidates and selections
   - ESG factors and evidence
   - prototype scores
3. Analysts can edit, approve, reject, unlink, relink, override, or add notes.
4. Review actions create immutable `ReviewEvent` records.
5. Original extracted evidence is not overwritten.
6. Final export remains blocked until every segment row is approved, edited, or rejected and the document is approved.

## 25. Prototype Scoring Runs Only On Reviewed Data

1. Scoring is a local demonstration feature, not an official Fitch score.
2. Scoring uses reviewed or edited non-total segment rows.
3. Rejected and pending rows are excluded.
4. The base score comes from reviewed NACE mapping or fallback candidate hierarchy.
5. Approved or edited segment-linked ESG factors adjust the segment score.
6. Company-wide ESG factors do not adjust every segment automatically.
7. Segment scores are revenue-weighted using:
   - document reported total, when available
   - reviewed total row, when available
   - sum of reviewed non-total rows
8. Scoring output is labeled as prototype/demo only.

## 26. Export Runs Only After Approval

1. `ExportService.export_document()` enforces the final review gate.
2. Export is blocked unless:
   - the document is approved
   - every segment row is approved, edited, or rejected
3. Final files are written to `exports/{document_id}/`.
4. Generated files are:
   - `revenue_segments.csv`
   - `revenue_segments.xlsx`
   - `audit_export.json`
5. Rejected rows are excluded from CSV/XLSX.
6. Rejected rows remain in the audit JSON.
7. The audit JSON also includes evidence, NACE candidates, ESG factors, scores, validation issues, review state, and review events.

## 27. Workflow Usage Metrics Are Displayed

1. Streamlit wraps the LLM provider during analysis.
2. The wrapper records each LLM request.
3. The UI shows:
   - workflow time before review
   - LLM call count
   - input/output tokens
   - total tokens
   - estimated total cost
4. Provider-reported token usage is used when available.
5. If provider usage is not available, token counts are estimated from text length.
6. Cost estimates use Anthropic Opus/Sonnet family rates and do not include prompt caching, batch discounts, or long-context premiums.
7. Usage metrics are session-scoped Streamlit telemetry, not part of final exports.

## 28. What The Analyst Sees

1. The analyst sees workflow progress and document summary cards.
2. The segment table shows each extracted row and its page reference.
3. The Evidence section shows stored evidence snippets and page labels.
4. When bbox is available, the original PDF page is rendered with the evidence block highlighted.
5. The ESG tab shows segment-linked and company-wide ESG factors separately.
6. The selected ESG factor shows evidence text and a highlighted source page when the text can be matched to parsed page blocks.
7. Validation issues explain why rows need review.
8. NACE candidates show ranked classification options.
9. Scoring remains clearly labeled as prototype/demo only.
10. Export controls remain unavailable until review gates pass.

## 29. Main Failure Modes And Safeguards

1. Bad PDF text extraction:
   - Mitigation: parser source metadata, fallback hooks, page evidence review.
2. Weak keyword retrieval:
   - Mitigation: adjacent page inclusion, dense table signals, LLM candidate-discovery fallback.
3. LLM malformed output:
   - Mitigation: JSON extraction and strict Pydantic schemas.
4. LLM extracts non-revenue rows:
   - Mitigation: metric validation and blocking issue creation.
5. LLM extracts prior-period rows:
   - Mitigation: latest-period filtering.
6. Duplicate rows:
   - Mitigation: conservative deduplication using segment name, value, period, page proximity, and evidence text.
7. Total mismatch:
   - Mitigation: reconciliation issue and human review.
8. Weak NACE mapping:
   - Mitigation: candidate-list-only LLM choice, reviewer override, and not-applicable handling.
9. ESG over-linking:
   - Mitigation: conservative segment-link rules and company-wide fallback.
10. Premature export:
   - Mitigation: approval gate blocks final CSV/XLSX/JSON export until review is complete.

## 30. Why The Pipeline Is Not Prompt-Only

1. The LLM never receives the full PDF by default.
2. Deterministic parsing controls page text, tables, page numbers, and bounding boxes.
3. Keyword and structure search select candidate pages before LLM extraction.
4. Strict schemas constrain LLM output shape.
5. Python normalization owns currency, scale, numeric values, and periods.
6. Python validation rejects unsupported rows.
7. Python reconciliation checks row sums against totals.
8. Review services enforce final approval.
9. Exports are blocked until the human quality gate is complete.

