# Pipeline Presentation Guide

This project extracts revenue segments from annual-report PDFs, links them to NACE industries and ESG factors, lets an analyst review the results, then computes a prototype ESG-aware segment score and exports the final data. The main design idea is a hybrid pipeline: deterministic parsing and validation handle what should be exact, while the LLM is used only where documents are messy and language-heavy.

## End-To-End Workflow

1. **Upload or ingest a PDF.** A user uploads a PDF in Streamlit or runs the CLI/folder pipeline. The document is stored in SQLite with company name, fiscal period, currency, scale, and source path.
2. **Parse the PDF.** `PyMuPDF` extracts page text and layout blocks, while `pdfplumber` extracts tables. This gives the pipeline both narrative text and tabular revenue data.
3. **Find likely revenue pages.** Deterministic page scoring looks for terms like "operating segments," "reportable segments," "external revenue," currency/unit words, numeric density, and table density. Only high-signal pages are sent forward.
4. **Extract revenue rows with an LLM.** The configured provider, usually Anthropic, receives small page bundles and must return strict JSON. The prompt asks for revenue segment rows, evidence, page references, currency, scale, period, confidence, and warnings.
5. **Normalize and validate.** Pydantic rejects malformed JSON. Python then normalizes values, units, currency, period labels, page references, and flags duplicates, prior-year rows, totals, non-revenue metrics, missing evidence, and reconciliation problems.
6. **Verify uncertain cases.** If validation finds risk signals, the optional second-pass verifier can re-check rows. Arbitration is available for harder conflicts, but defaults off unless enabled.
7. **Map segments to NACE.** Each reviewable segment is matched against `reference/NACE_Rev2_Outline.csv`. The system retrieves deterministic candidates, adds domain hints, optionally reranks/classifies with the LLM, and stores the top candidates for review.
8. **Extract ESG factors.** ESG retrieval scans pages for emissions, renewable investment, fossil-fuel exposure, controversies, safety incidents, water risk, labor issues, governance, and similar signals. The LLM extracts strict ESG JSON, and Python links a factor to a segment only when the evidence supports it.
9. **Human review gate.** Analysts approve, edit, or reject revenue rows, NACE selections, ESG factors, and validation issues in Streamlit. Export is blocked until review is complete.
10. **Score and export.** Approved or edited rows can receive prototype segment scores. Final exports include CSV/XLSX for approved rows and JSON audit output with evidence, rejected rows, validation issues, and review history.

## Why These Libraries And Frameworks

- **PyMuPDF:** fast, reliable PDF text and page-layout extraction. It helps preserve page-level evidence and works well for annual reports.
- **pdfplumber:** stronger table extraction, which matters because revenue segments are usually in financial tables.
- **Pydantic:** validates LLM output against strict schemas so bad JSON or missing fields are caught before persistence.
- **SQLite:** simple local persistence for a prototype. It supports repeatable demos, review state, audit trails, and parallel workers without needing a deployed database.
- **Streamlit:** fast way to build an analyst review UI with upload, tables, edits, validation messages, NACE, ESG, scoring, and export controls.
- **Anthropic provider abstraction:** the pipeline can use real model calls for extraction or a fake deterministic provider for tests and demos without credentials.
- **Standard-library scoring config:** scoring rules are JSON-compatible YAML, so the prototype avoids extra dependencies and keeps the scoring model transparent.

## Why The Hybrid Strategy Works

A single "read this PDF and extract everything" prompt is risky because annual reports mix segment revenue with profit, assets, expenses, totals, prior years, reconciliations, and ESG narrative. This pipeline reduces that risk by narrowing the problem before the LLM sees it.

Deterministic code handles page retrieval, normalization, deduplication, validation, review gates, and export rules. The LLM handles the parts that are language-dependent: interpreting messy disclosures, identifying segment names, reading table context, mapping NACE candidates, and summarizing ESG factors. This split is easier to audit, cheaper to run, and safer than relying on the model alone.

## ESG Factors: How We Get Them

ESG extraction starts after revenue segments exist. The system scans parsed pages for controlled ESG signals such as emissions targets, decarbonization plans, renewable investment, fossil-fuel exposure, coal phaseout, controversies, regulatory violations, safety incidents, labor issues, biodiversity, water risk, and governance policies.

Pages get boosted if they are near known segment pages or appear in MD&A, business review, or risk sections. Generic indexes, tables of contents, cross-reference pages, and boilerplate governance text are filtered where possible. The LLM then returns ESG factors with factor type, polarity, description, evidence, page reference, confidence, and linkage rationale.

The important rule is conservative linking: company-wide ESG is not copied to every segment. A factor is segment-linked only when the evidence explicitly names the segment or clearly ties the ESG issue to that segment's business activity, asset type, product line, geography, or named project.

## Scoring Calculation

The scoring model is a prototype demo score, not an official ratings score, sustainability score, credit rating, or investment recommendation.

The scale is **1 to 5**, where **1 is better/lower impact** and **5 is worse/higher impact**.

For each approved or edited non-total segment:

```text
base score = configured NACE score
ESG adjustment = sum(approved segment-linked ESG adjustments)
final segment score = capped(base score + ESG adjustment, 1, 5)
```

The base score comes from NACE in this order: reviewer-selected NACE code, top NACE candidate, exact code rule, division rule, section rule, then default score `3.0`. This makes the score explainable even when the exact NACE code is not available.

ESG adjustments are transparent and rule-based. Positive ESG lowers the score, negative ESG raises it. For example, positive polarity is `-0.25`, negative polarity is `+0.25`, renewable investment is `-0.25`, decarbonization plan is `-0.20`, fossil-fuel exposure is `+0.25`, and controversies or regulatory violations are `+0.30`. Only approved or edited segment-linked ESG factors count.

The company score is revenue-weighted:

```text
company score = sum(segment final score * segment revenue share) / sum(included revenue share)
```

The revenue denominator is chosen in this order: document reported total, reviewed total row, or sum of reviewed non-total segment rows. Rejected rows and total rows are excluded to avoid double-counting.

## Why These Models Were Chosen

The default extraction model is `claude-sonnet-4-6` because the workflow needs strong structured extraction, long-context reading, and careful reasoning over financial tables without making every call as expensive as a top-tier arbitration model. Temperature is set to `0.0` to make outputs more consistent.

The optional arbitration model defaults to the extraction model and arbitration is disabled by default. This avoids accidental high-cost or unsupported model calls while keeping a separately configurable hard-case arbitration path. The provider layer also includes fake mode, which is not for quality claims, but is useful for deterministic tests and demos.

## Future Scaling

- Replace local SQLite with Postgres for multi-user review and stronger concurrency.
- Add a production queue system for large PDF batches and retry handling.
- Add OCR or vision fallback for scanned or low-text pages.
- Use cloud object storage for PDFs, rendered pages, exports, and audit artifacts.
- Add role-based review, assignment, and reviewer signoff.
- Evaluate against a larger labeled gold set and tune page retrieval, NACE mapping, ESG linking, and score rules.
- Add monitoring for extraction failures, model cost, latency, validation issue rates, and reviewer override rates.

## Common Questions And Answers

**Why not just use one LLM prompt for the whole PDF?**  
Because full annual reports contain too much irrelevant and conflicting information. Retrieval first makes the model cheaper, more accurate, and easier to audit.

**How do you know the extracted rows are revenue segments?**  
The prompt asks specifically for revenue rows, but the real protection is after extraction: schema validation, metric filtering, period filtering, duplicate detection, total reconciliation, evidence checks, and human review.

**Why is human review required?**  
Financial disclosures can be ambiguous. Review makes the output defensible because every final row has evidence, status, and an audit trail.

**How are ESG factors connected to segments?**  
Only with explicit evidence. If the factor is general company policy or the link is uncertain, it stays company-wide and does not affect segment scoring.

**Why use NACE?**  
NACE gives a standardized industry classification, so different company segment names can be compared using a common taxonomy.

**Is the score an official ESG or credit score?**  
No. It is a transparent prototype score showing how segment industry risk and reviewed ESG evidence could be combined.

**Why use revenue weighting?**  
Revenue weighting prevents a tiny segment from affecting the company score as much as the main business. Larger revenue segments contribute more.

**What happens if metadata cannot be detected?**  
The system does not guess. In the folder pipeline, it queues the PDF for manual company, fiscal period, currency, and scale input, then retries that document.

**What happens with scanned PDFs?**  
Low-text pages are detected, and the code has an OCR/vision fallback extension point. A production version should configure OCR for scanned annual reports.

**Why is this pipeline a strong design?**  
It is modular, testable, auditable, and conservative. Each stage has a clear job, errors are surfaced as validation issues, and final export is blocked until review is complete.

## Two-Minute Talk Track

- Our pipeline turns annual-report PDFs into reviewed revenue segment data with NACE, ESG, scoring, and export.
- First, we parse PDFs using PyMuPDF for page text/layout and pdfplumber for tables.
- Then deterministic retrieval ranks the pages most likely to contain segment revenue, so the model only sees relevant evidence instead of the whole report.
- The LLM extracts strict JSON revenue rows, and Pydantic plus Python validation catch malformed output, duplicates, prior-year rows, non-revenue metrics, unsupported evidence, and reconciliation issues.
- Next, we map each segment to NACE so custom company segment names become standardized industry categories.
- We also extract ESG factors from high-signal ESG pages, but we only link ESG to a segment when the evidence clearly supports that link.
- Analysts review every row, NACE choice, ESG factor, and validation issue in Streamlit. Export is blocked until rows are approved, edited, or rejected.
- The prototype score starts with a NACE-based base score on a 1-to-5 scale, where 1 is better and 5 is worse.
- Approved segment-linked ESG factors adjust that score: positive evidence lowers it, negative evidence raises it, and the result is capped between 1 and 5.
- The company score is a revenue-weighted average, so larger segments matter more and total rows are excluded to avoid double-counting.
- We chose this design because a single LLM prompt is too risky for financial PDFs. The hybrid approach is more accurate, cheaper, explainable, and auditable.
- Future improvements would include Postgres, job queues, OCR, cloud storage, larger gold-set evaluation, reviewer assignment, and production monitoring.
