# Parsing and Retrieval

## Scope

The ingestion layer parses annual report and 10-K PDFs into deterministic page records before any LLM extraction. LLM extraction is implemented separately in `revenue_segment_extractor/extraction/`; ingestion does not implement review UI, ESG extraction, NACE mapping, scoring, or final exports.

## Parser Responsibilities

`revenue_segment_extractor.ingestion.parse_pdf()` uses:

- PyMuPDF for page text, text blocks, page dimensions, bounding boxes, and page rendering support.
- pdfplumber for table detection, extracted table text, and cell-level table structure where available.

Each parsed page stores:

- 1-based `page_number`
- extracted `text`
- `blocks_json.page` with width, height, and rotation
- `blocks_json.blocks` with block text, bbox, and parser source
- `tables_json.tables` with pdfplumber rows and cells
- `language`, using a lightweight heuristic such as `en`, `es`, `fr`, `de`, `it`, `pt`, or `unknown`
- `parser_sources`
- `has_text`

Weak/no-text pages are marked `has_text = false` when PyMuPDF extracts fewer than 30 alphanumeric characters. Optional page text fallback can be enabled for those pages only. `blocks_json.text_fallback` records `not_needed`, `available_not_configured`, `applied`, `attempted_no_text`, or `failed`, along with provider/source metadata where available.

Fallback is disabled by default. Enable it with:

```bash
RSE_ENABLE_PAGE_TEXT_FALLBACK=true
RSE_PAGE_TEXT_FALLBACK_PROVIDER=ocr
RSE_OCR_COMMAND=tesseract
RSE_OCR_LANGUAGES=eng
```

The local OCR provider shells out to an installed OCR command and does not add a mandatory Python dependency. Tests use `FakePageTextFallbackProvider`. Vision/PDF providers can be supplied through the same provider interface and should set `parser_source = "vision"`.

## Language

The ingestion layer uses a dependency-free marker heuristic to mark page language when common segment/revenue terms are present. It intentionally returns `unknown` for pages without enough signal instead of pretending to run full language identification.

The matching logic normalizes text to ASCII for deterministic phrase matching and includes a curated multilingual set for revenue, sales, turnover, operating/reportable/business segments, and total terms. This improves recall for common non-English annual report wording while keeping LLM extraction responsible for semantic interpretation.

## Candidate Scoring

`score_pages()` assigns deterministic relevance using:

- segment and revenue disclosure terms
- table density
- numeric density
- currency and unit terms
- repeated business-line labels
- total and consolidation terms

`select_candidate_pages()` defaults to 15 pages. It includes adjacent pages when a high-scoring source page is found and the adjacent page has at least one deterministic signal of its own. This supports segment tables that continue across pages while avoiding whole-document LLM context.

Each candidate stores:

- `page_number`
- `relevance_score`
- `matched_signals_json`
- `reason`

## Evidence References

`locate_evidence_snippet()` searches parsed PyMuPDF text blocks for an exact or token-overlap snippet match. It returns approximate page-level evidence references:

```json
{
  "page_number": 42,
  "snippet_text": "Revenue by segment and external revenue, USD millions",
  "bbox": {"x0": 72.0, "y0": 96.0, "x1": 330.0, "y1": 111.0},
  "parser_source": "pymupdf",
  "match_type": "exact_block",
  "block_index": 1
}
```

The bbox is block-level, not word-perfect proof. OCR/vision fallback blocks may not have bboxes, so review surfaces warn analysts when evidence came from `ocr`, `vision`, or `text_fallback`.

## CLI Usage

Initialize dependencies and ingest a PDF:

```bash
python -m pip install -r requirements.txt
python scripts/ingest_pdf.py /path/to/annual-report.pdf --company-name "Example Corp"
```

Optional flags:

- `--document-name`
- `--fiscal-period`
- `--currency`
- `--scale`
- `--candidate-limit`
- `--database`

The command initializes the SQLite schema if needed, creates a `Document`, stores `ParsedPage` records, stores ranked `PageCandidate` records, and prints a JSON summary.

## Verification

Run:

```bash
python -m unittest discover -s tests
```

The PDF ingestion tests generate small fixture PDFs at runtime and verify text extraction, table extraction, candidate scoring, multilingual matching, adjacent page inclusion, bbox structure, no-text page handling, fallback provider behavior, page rendering, and persistence.
