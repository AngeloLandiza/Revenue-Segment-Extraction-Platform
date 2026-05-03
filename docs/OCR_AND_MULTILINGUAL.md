# OCR and Multilingual Robustness

## What Fallback Does

PDF parsing first uses PyMuPDF text and pdfplumber tables. If a page has fewer than 30 alphanumeric characters, it is treated as low text and can use an optional page-text fallback provider.

Fallback providers return extracted text plus a parser source:

- `ocr` for local OCR output.
- `vision` for a future LLM vision/PDF provider.
- `text_fallback` for the legacy callable hook.
- `fake` providers in tests can use `ocr` or `vision` source labels.

When fallback succeeds, the page text is replaced with fallback text, the source is added to `parser_sources`, and `blocks_json.text_fallback.status` is set to `applied`. A synthetic text block is also stored so evidence lookup can persist `parser_source = "ocr"` or `parser_source = "vision"`.

## When It Triggers

Fallback only runs on low-text pages. It is disabled by default because OCR and vision providers can add cost, latency, and operational dependencies.

Enable local OCR with environment configuration:

```bash
FITCH_ENABLE_PAGE_TEXT_FALLBACK=true
FITCH_PAGE_TEXT_FALLBACK_PROVIDER=ocr
FITCH_OCR_COMMAND=tesseract
FITCH_OCR_LANGUAGES=eng
```

The OCR provider uses an installed command-line OCR tool. The project does not require OCR libraries during normal setup. If OCR is enabled but the command is missing or fails, parsing continues and records `blocks_json.text_fallback.status = "failed"` with the error.

## Vision Provider Interface

The parser accepts any provider implementing:

```python
extract_text(pdf_path: Path, page_number: int) -> PageTextFallbackResult | None
```

That allows a later LLM vision/PDF provider to be plugged in without changing persistence or retrieval contracts. A vision provider should return `parser_source = "vision"` and remain opt-in.

## Multilingual Handling

Retrieval includes a curated multilingual phrase set for:

- revenue, sales, turnover, and related sales measures
- operating, business, and reportable segments
- total and consolidated total wording
- common unit words such as millions/millionen/millones/milhoes

The page parser also marks likely page language with a small dependency-free heuristic: `en`, `es`, `fr`, `de`, `it`, `pt`, or `unknown`. This is only a routing/review hint, not a full translation layer.

LLM prompts instruct the model to extract revenue-segment values regardless of report language, preserve official segment names exactly, and translate only explanatory notes when needed. Segment labels must not be replaced with translated-only labels.

## What Remains Manual

Fallback text is not treated as final truth. The UI warns when evidence came from OCR/vision fallback text, and human review remains the final quality gate before export. Analysts still need to verify scanned-page values against the original PDF when OCR confidence is not available or when table layout is ambiguous.

## Why This Is Enough for a Local Prototype

The implementation improves graceful degradation without requiring heavy dependencies or live vision calls. Low-text pages no longer have to be discarded when a local or fake provider is configured, multilingual pages get better deterministic retrieval coverage, and every fallback-derived row still passes through strict schema validation, normalization, deterministic validation, and human review.
