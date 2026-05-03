from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Protocol

from fitch_extractor.extraction.schemas import (
    EsgExtractionOutput,
    RevenueArbitrationOutput,
    RevenueExtractionOutput,
    RevenueVerificationOutput,
)


FIRST_PASS_PROMPT_VERSION = "first_pass_revenue_segments_v1"
CANDIDATE_DISCOVERY_PROMPT_VERSION = "candidate_page_discovery_v1"
SECOND_PASS_VERIFICATION_PROMPT_VERSION = "second_pass_verification_v1"
ARBITRATION_PROMPT_VERSION = "revenue_arbitration_v1"
ESG_EXTRACTION_PROMPT_VERSION = "esg_extraction_v1"


class PromptDocument(Protocol):
    company_name: str
    document_name: str
    fiscal_period: str | None
    reported_total: object
    currency: str | None
    scale: str | None


class PromptPage(Protocol):
    page_number: int
    text: str
    tables_json: dict


class PromptSegment(Protocol):
    id: str
    segment_name: str


def build_first_pass_extraction_prompt(
    *,
    document: PromptDocument,
    pages: Sequence[PromptPage],
) -> str:
    if not pages:
        raise ValueError("pages must not be empty")

    schema = json.dumps(RevenueExtractionOutput.model_json_schema(), indent=2, sort_keys=True)
    page_sections = "\n\n".join(_format_page(page) for page in pages)

    return "\n".join(
        [
            f"Prompt version: {FIRST_PASS_PROMPT_VERSION}",
            "",
            "You extract first-pass revenue segment rows from annual reports and 10-K filings.",
            "Return valid JSON only. Do not wrap the JSON in Markdown.",
            "",
            "Extraction rules:",
            "- The document may be in any language or regional reporting format. Use the semantic meaning of headings, table structure, and nearby notes to identify local equivalents of segment/reportable/operating, revenue/turnover/net sales/external income, and the current/latest reporting period.",
            "- Extract eligible values regardless of report language.",
            "- Preserve original official segment names exactly as shown. Do not translate segment labels unless you also preserve the original label in segment_name.",
            "- Translate explanatory notes only when needed to explain evidence or warnings; never replace official company segment labels with translated-only labels.",
            "- Extract only revenue, turnover, net sales, segment revenue, or external revenue values tied to business, reportable, or operating segments.",
            "- When a segment table includes both external/customer revenue and intra/inter-segment revenue, prefer the row that represents total segment revenue, total income, revenue, revenues, or total operating income for each segment. Use external/customer revenue only when there is no total revenue/income row for the same current-period segment table.",
            "- Avoid expenses, losses, assets, EBITDA, EBIT, profit, operating income, employee counts, and non-revenue metrics unless the document clearly uses a different revenue-equivalent metric.",
            "- Distinguish true operating, business, or reportable segments from product/geographical segmentation and consolidated income statement line items.",
            "- Extract totals only when they are clearly the reported segment total or total revenue relevant to the segment table.",
            "- Do not create a segment row where the segment_name is only a consolidated income statement line item such as Revenue, Revenues, Sales, Turnover, Net revenue, or Net sales.",
            "- For a primary segmentation or segment information table with segment columns, return one row for each current-period column in the revenue/external income line, including eliminations, segment totals, reclassification-to-reported columns, and reported columns when those columns are part of the segment reconciliation table.",
            "- For a row-oriented segment revenue table, include segment rows and revenue reconciliation rows that bridge to total revenues, such as hedging gains/losses, eliminations, reconciling items, and total revenues, when they are part of the current-period segment revenue table.",
            "- Use the exact segment/table column header as segment_name. Do not add explanatory qualifiers to labels such as Total, Reported, Eliminations, or Reclassification to reported and incidental items.",
            "- Extract only the latest/current fiscal or reporting period when a table presents multiple years. Do not return prior-year comparison columns when the current/latest period is present.",
            "- If a required segment-table column has a dash or explicit blank for the revenue/external income line, include the row, preserve revenue_raw exactly as '-' or the shown blank marker, and set revenue_value to null.",
            "- Prefer financial statement note segment disclosures over management discussion summaries when both are present.",
            "- Return one primary current-period segment table per document. Do not duplicate the same segment/value set from later detailed note pages or reconciliation-only subtables when an earlier complete segment revenue table is already present in the candidate bundle.",
            "- Do not extract ESG, EU taxonomy, climate-impact, energy-intensity, energy-use, emissions, fuel-consumption, debt, financing, asset, or green-financing tables unless they are explicitly the business/reportable/operating segment revenue table.",
            "- Preserve raw values exactly as shown in the evidence.",
            "- Infer currency and scale only from explicit nearby evidence such as a table title, heading, unit label, or same-page note.",
            "- Return null when a field is unknown or unsupported by nearby evidence.",
            "- Include page number, section, and concise evidence text for every row.",
            "- Produce JSON matching the schema exactly; do not add extra keys.",
            "",
            "Document context:",
            f"Company: {document.company_name}",
            f"Document: {document.document_name}",
            f"Fiscal period: {document.fiscal_period or 'unknown'}",
            f"Reported total: {document.reported_total if document.reported_total is not None else 'unknown'}",
            f"Document currency hint: {document.currency or 'unknown'}",
            f"Document scale hint: {document.scale or 'unknown'}",
            "",
            "Required JSON schema:",
            schema,
            "",
            "Candidate page bundle:",
            page_sections,
        ]
    )


def build_candidate_discovery_prompt(
    *,
    document: PromptDocument,
    pages: Sequence[PromptPage],
    max_pages: int,
) -> str:
    if not pages:
        raise ValueError("pages must not be empty")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    from fitch_extractor.extraction.schemas import CandidateDiscoveryOutput

    schema = json.dumps(CandidateDiscoveryOutput.model_json_schema(), indent=2, sort_keys=True)
    page_summaries = "\n\n".join(_format_page_summary(page) for page in pages)

    return "\n".join(
        [
            f"Prompt version: {CANDIDATE_DISCOVERY_PROMPT_VERSION}",
            "",
            "You identify candidate pages for first-pass revenue segment extraction.",
            "Return valid JSON only. Do not wrap the JSON in Markdown.",
            "",
            "Discovery rules:",
            "- The document may be in any language or regional reporting format.",
            "- Preserve original page terminology and official segment labels; translate only short explanatory reasoning if needed.",
            "- Select pages likely to contain revenue, turnover, sales, total income, business volume, or other clearly revenue-equivalent metrics split by operating segment, reportable segment, business line, geography, country, jurisdiction, or other management/reporting dimension.",
            "- Include regulated country-by-country or jurisdiction disclosure tables only when the measure is revenue-equivalent rather than assets, loans, deposits, risk exposure, capital, employees, tax, or profit alone.",
            "- Exclude pages that only contain balance sheet data, credit risk exposure, solvency/capital ratios, portfolio holdings, employee counts, ESG/climate metrics, accounting policy text, or consolidated income statement line items with no segment/dimension split.",
            "- Prefer pages with explicit tables over narrative-only pages.",
            f"- Return at most {max_pages} page(s), ordered from strongest to weakest evidence.",
            "- If no page is likely to contain eligible extraction data, return an empty selected_pages list and explain why in extraction_warnings.",
            "",
            "Document context:",
            f"Company: {document.company_name}",
            f"Document: {document.document_name}",
            f"Fiscal period: {document.fiscal_period or 'unknown'}",
            "",
            "Required JSON schema:",
            schema,
            "",
            "Page summaries:",
            page_summaries,
        ]
    )


def build_second_pass_verification_prompt(
    *,
    document: PromptDocument,
    pages: Sequence[PromptPage],
    rows: Sequence[dict],
    validation_issues: Sequence[dict],
) -> str:
    schema = json.dumps(RevenueVerificationOutput.model_json_schema(), indent=2, sort_keys=True)
    page_sections = "\n\n".join(_format_page_summary(page) for page in pages)
    return "\n".join(
        [
            f"Prompt version: {SECOND_PASS_VERIFICATION_PROMPT_VERSION}",
            "",
            "You verify extracted revenue segment rows from annual reports and 10-K filings.",
            "Return valid JSON only. Do not wrap the JSON in Markdown.",
            "",
            "Strict project rules:",
            "- The document may be in any language; preserve original official segment names while verifying values.",
            "- Translate explanatory notes only when needed and never replace an official segment label with a translated-only label.",
            "- Confirm only rows tied to revenue, turnover, net sales, segment revenue, external revenue, or clearly revenue-equivalent metrics.",
            "- Reject expenses, losses, assets, profit, EBIT, EBITDA, tax, cost of sales, finance income, or consolidated income statement line items as core revenue segment rows.",
            "- Segment rows may be operating divisions, business lines, product categories, or geographies when the report presents them as revenue or business activity segmentation.",
            "- Do not treat dash-only cells as numeric zero unless the table explicitly defines dashes as zero.",
            "- Every confirmed row needs page evidence and a concise rationale.",
            "- Report suspected errors, missing rows, correction suggestions, and rationale; do not invent unsupported values.",
            "",
            "Document context:",
            f"Company: {document.company_name}",
            f"Document: {document.document_name}",
            f"Fiscal period: {document.fiscal_period or 'unknown'}",
            f"Reported total: {document.reported_total if document.reported_total is not None else 'unknown'}",
            f"Document currency hint: {document.currency or 'unknown'}",
            f"Document scale hint: {document.scale or 'unknown'}",
            "",
            "Required JSON schema:",
            schema,
            "",
            "Extracted rows JSON:",
            json.dumps(list(rows), indent=2, sort_keys=True, default=str),
            "",
            "Validation issues JSON:",
            json.dumps(list(validation_issues), indent=2, sort_keys=True, default=str),
            "",
            "Page snippets:",
            page_sections or "[no page snippets]",
        ]
    )


def build_arbitration_prompt(
    *,
    document: PromptDocument,
    pages: Sequence[PromptPage],
    rows: Sequence[dict],
    validation_issues: Sequence[dict],
    verification_result: dict | None,
) -> str:
    schema = json.dumps(RevenueArbitrationOutput.model_json_schema(), indent=2, sort_keys=True)
    page_sections = "\n\n".join(_format_page(page) for page in pages)
    return "\n".join(
        [
            f"Prompt version: {ARBITRATION_PROMPT_VERSION}",
            "",
            "You arbitrate difficult revenue segment extraction cases using the configured arbitration model.",
            "Return valid JSON only. Do not wrap the JSON in Markdown.",
            "",
            "Arbitration rules:",
            "- This arbitration pass should be invoked only for rows/documents that deterministic validation or second-pass verification could not confidently clear.",
            "- The document may be in any language; preserve original official segment names while arbitrating values.",
            "- Translate explanatory notes only when needed and never replace an official segment label with a translated-only label.",
            "- Apply the same Fitch extraction rules: revenue/net sales/turnover/revenue-equivalent segment metrics only; no expenses, assets, losses, profit, EBIT, EBITDA, or tax rows.",
            "- Confirm, reject, or suggest corrections with evidence-grounded rationale.",
            "- Mark requires_human_review true when evidence remains ambiguous.",
            "",
            "Document context:",
            f"Company: {document.company_name}",
            f"Document: {document.document_name}",
            f"Fiscal period: {document.fiscal_period or 'unknown'}",
            f"Reported total: {document.reported_total if document.reported_total is not None else 'unknown'}",
            f"Document currency hint: {document.currency or 'unknown'}",
            f"Document scale hint: {document.scale or 'unknown'}",
            "",
            "Required JSON schema:",
            schema,
            "",
            "Extracted rows JSON:",
            json.dumps(list(rows), indent=2, sort_keys=True, default=str),
            "",
            "Validation issues JSON:",
            json.dumps(list(validation_issues), indent=2, sort_keys=True, default=str),
            "",
            "Second-pass verification JSON:",
            json.dumps(verification_result, indent=2, sort_keys=True, default=str),
            "",
            "Page text and tables:",
            page_sections or "[no page snippets]",
        ]
    )


def build_esg_extraction_prompt(
    *,
    document: PromptDocument,
    pages: Sequence[PromptPage],
    segments: Sequence[PromptSegment],
) -> str:
    if not pages:
        raise ValueError("pages must not be empty")

    schema = json.dumps(EsgExtractionOutput.model_json_schema(), indent=2, sort_keys=True)
    page_sections = "\n\n".join(_format_page(page) for page in pages)
    segment_context = [
        {"id": segment.id, "segment_name": segment.segment_name} for segment in segments
    ]

    return "\n".join(
        [
            f"Prompt version: {ESG_EXTRACTION_PROMPT_VERSION}",
            "",
            "You extract ESG factors from annual reports and 10-K filings for a Fitch revenue-segment prototype.",
            "Return valid JSON only. Do not wrap the JSON in Markdown.",
            "",
            "Core ESG rules:",
            "- Extract only ESG actions, risks, targets, controversies, violations, incidents, impacts, or programs supported by explicit evidence.",
            "- Classify factor_type using only the controlled vocabulary in the schema.",
            "- Classify polarity as positive, negative, neutral, mixed, or unknown.",
            "- Provide concise evidence_text, page_ref, confidence, and linkage_rationale for every factor.",
            "- Mark is_company_wide true when evidence is broad corporate context and not tied to a segment, business activity, asset type, product line, geography, or named project.",
            "- Mark is_company_wide false only when the evidence explicitly links the ESG factor to a listed segment, that segment's business activity, asset type, product line, geography, or named project.",
            "- Set segment_link_type to direct_segment_name, asset_or_project, activity_type, geography, company_wide, or unclear based on the strongest explicit link.",
            "- Set esg_category to E, S, G, or unknown. Set score_relevant true only when the evidence is segment-specific and material enough to affect scoring.",
            "- Use impact_mechanism to describe the distinct mechanism for later deduplication, such as decarbonization_supply_chain, safety_incident, water_stress, or regulatory_fine.",
            "- When segment-specific, set segment_name to the best matching listed segment name when possible and explain the explicit link in linkage_rationale.",
            "- If linkage is uncertain, classify the factor as company-wide instead of segment-specific.",
            "- Do not return ESG indexes, contents pages, generic governance statements, boilerplate policies, or sustainability-report cross-reference text unless it materially affects scoring and has explicit linkage evidence.",
            "- Do not invent sustainability claims or attach company-wide ESG context to all segments.",
            "",
            "Document context:",
            f"Company: {document.company_name}",
            f"Document: {document.document_name}",
            f"Fiscal period: {document.fiscal_period or 'unknown'}",
            "",
            "Segment context JSON:",
            json.dumps(segment_context, indent=2, sort_keys=True),
            "",
            "Required JSON schema:",
            schema,
            "",
            "Candidate ESG page bundle:",
            page_sections,
        ]
    )


def _format_page(page: PromptPage) -> str:
    section = _infer_section(page.text)
    tables = _format_tables(page.tables_json)
    text = page.text.strip() or "[no extracted text]"
    language = getattr(page, "language", None) or "unknown"
    parts = [
        f"Page {page.page_number}",
        f"Detected language: {language}",
        f"Section: {section or 'unknown'}",
        "Text:",
        text,
    ]
    if tables:
        parts.extend(["Tables:", tables])
    return "\n".join(parts)


def _format_page_summary(page: PromptPage) -> str:
    section = _infer_section(page.text)
    table_preview = _format_table_preview(page.tables_json)
    text = re.sub(r"\s+", " ", page.text.strip())[:900] or "[no extracted text]"
    language = getattr(page, "language", None) or "unknown"
    parts = [
        f"Page {page.page_number}",
        f"Detected language: {language}",
        f"Section: {section or 'unknown'}",
        f"Text excerpt: {text}",
    ]
    if table_preview:
        parts.append(f"Table preview:\n{table_preview}")
    return "\n".join(parts)


def _infer_section(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:160]
    return None


def _format_tables(tables_json: dict) -> str:
    tables = tables_json.get("tables", []) if isinstance(tables_json, dict) else []
    formatted_tables: list[str] = []
    for table_index, table in enumerate(tables):
        rows = table.get("rows", []) if isinstance(table, dict) else []
        if not rows:
            continue
        formatted_rows = [_format_table_row(row) for row in rows if row]
        if formatted_rows:
            formatted_tables.append(f"Table {table_index}\n" + "\n".join(formatted_rows))
    return "\n\n".join(formatted_tables)


def _format_table_preview(tables_json: dict) -> str:
    tables = tables_json.get("tables", []) if isinstance(tables_json, dict) else []
    previews: list[str] = []
    for table_index, table in enumerate(tables[:2]):
        rows = table.get("rows", []) if isinstance(table, dict) else []
        formatted_rows = [_format_table_row(row) for row in rows[:4] if row]
        if formatted_rows:
            previews.append(f"Table {table_index}\n" + "\n".join(formatted_rows))
    return "\n\n".join(previews)


def _format_table_row(row: list[object]) -> str:
    return "| " + " | ".join(str(cell).strip() for cell in row) + " |"
