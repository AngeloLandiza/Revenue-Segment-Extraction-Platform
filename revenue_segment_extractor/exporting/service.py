from __future__ import annotations

import csv
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from revenue_segment_extractor.models import (
    DOCUMENT_STATUS_APPROVED,
    SEGMENT_STATUS_REJECTED,
    CompanyScore,
    Document,
    EsgFactor,
    ExportRecord,
    NaceCandidate,
    NaceSelection,
    ReviewEvent,
    SegmentEvidence,
    SegmentRow,
    SegmentScore,
    ValidationIssue,
    ValidationIssueReview,
)
from revenue_segment_extractor.persistence.repository import SQLiteRepository
from revenue_segment_extractor.persistence.review import (
    ESG_STATUS_APPROVED,
    ESG_STATUS_EDITED,
    ESG_STATUS_REJECTED,
)
from revenue_segment_extractor.scoring import ScoringService


EXPORT_COLUMNS = [
    "company_name",
    "document_name",
    "fiscal_period",
    "segment_name",
    "segment_name_original",
    "segment_name_normalized",
    "row_type",
    "segment_type",
    "language",
    "revenue_value",
    "revenue_raw",
    "currency",
    "scale",
    "revenue_unit",
    "normalized_value",
    "period_label",
    "page_ref",
    "section_ref",
    "evidence_text",
    "metric_basis",
    "confidence",
    "review_status",
    "reviewer_note",
    "nace_code",
    "nace_label",
    "nace_level",
    "nace_rank",
    "nace_match_score",
    "nace_rationale",
    "nace_confidence",
    "nace_needs_review",
    "esg_factor_summary",
    "scoring_model_label",
    "base_score",
    "esg_adjustment_score",
    "segment_score",
    "weight_share",
    "score_rationale",
    "company_score",
]

PIPELINE_VERSION = "revenue_segment_export_v1"
DEFAULT_EXPORT_ROOT = Path("exports")


@dataclass(frozen=True)
class ExportBundle:
    document_id: str
    output_dir: Path
    csv_path: Path
    json_path: Path
    xlsx_path: Path
    records: tuple[ExportRecord, ...]
    exported_at: datetime


@dataclass(frozen=True)
class _SelectedNaceExport:
    code: str = ""
    label: str = ""
    level: str = ""
    rank: str = ""
    match_score: str = ""
    rationale: str = ""


class ExportService:
    def __init__(
        self,
        repository: SQLiteRepository,
        export_root: str | Path = DEFAULT_EXPORT_ROOT,
    ) -> None:
        self.repository = repository
        self.export_root = Path(export_root)

    def export_document(self, document_id: str) -> ExportBundle:
        document = self._require_export_ready_document(document_id)
        rows = self.repository.list_segment_rows(document_id)
        evidence_by_segment = {
            row.id: self.repository.list_segment_evidence(row.id) for row in rows
        }
        validation_issues = self.repository.list_validation_issues(document_id)
        validation_reviews = self.repository.list_validation_issue_reviews(document_id)
        review_events = self.repository.list_review_events(document_id)
        esg_factors = self.repository.list_esg_factors(document_id)
        nace_candidates_by_segment = {
            row.id: self.repository.list_nace_candidates(row.id) for row in rows
        }
        nace_selection_by_segment = {
            selection.segment_id: selection
            for selection in self.repository.list_nace_selections(document_id)
        }
        scoring_result = ScoringService(self.repository).score_document(document_id)
        segment_score_by_segment = {
            score.segment_id: score for score in scoring_result.segment_scores
        }
        exported_at = datetime.now(UTC)

        output_dir = self.export_root / document_id
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = output_dir / "revenue_segments.csv"
        json_path = output_dir / "audit_export.json"
        xlsx_path = output_dir / "revenue_segments.xlsx"

        csv_rows = _csv_export_rows(
            document=document,
            rows=rows,
            evidence_by_segment=evidence_by_segment,
            review_events=review_events,
            nace_candidates_by_segment=nace_candidates_by_segment,
            nace_selection_by_segment=nace_selection_by_segment,
            esg_factors=esg_factors,
            segment_score_by_segment=segment_score_by_segment,
            company_score=scoring_result.company_score,
        )
        _write_csv(csv_path, csv_rows)
        _write_xlsx(xlsx_path, csv_rows)
        _write_json(
            json_path,
            _audit_export(
                document=document,
                rows=rows,
                evidence_by_segment=evidence_by_segment,
                validation_issues=validation_issues,
                validation_reviews=validation_reviews,
                review_events=review_events,
                nace_candidates_by_segment=nace_candidates_by_segment,
                nace_selection_by_segment=nace_selection_by_segment,
                esg_factors=esg_factors,
                segment_score_by_segment=segment_score_by_segment,
                company_score=scoring_result.company_score,
                exported_at=exported_at,
            ),
        )

        records = (
            self.repository.create_export_record(
                document_id=document_id,
                format="csv",
                path=str(csv_path),
            ),
            self.repository.create_export_record(
                document_id=document_id,
                format="xlsx",
                path=str(xlsx_path),
            ),
            self.repository.create_export_record(
                document_id=document_id,
                format="json",
                path=str(json_path),
            ),
        )
        return ExportBundle(
            document_id=document_id,
            output_dir=output_dir,
            csv_path=csv_path,
            json_path=json_path,
            xlsx_path=xlsx_path,
            records=records,
            exported_at=exported_at,
        )

    def _require_export_ready_document(self, document_id: str) -> Document:
        document = self.repository.get_document(document_id)
        if document is None:
            raise KeyError(f"Document not found: {document_id}")
        if document.status != DOCUMENT_STATUS_APPROVED:
            raise ValueError("Cannot export before document approval")
        if not self.repository.is_document_export_ready(document_id):
            raise ValueError("Cannot export while segment rows still need review")
        return document


def _csv_export_rows(
    *,
    document: Document,
    rows: list[SegmentRow],
    evidence_by_segment: dict[str, list[SegmentEvidence]],
    review_events: list[ReviewEvent],
    nace_candidates_by_segment: dict[str, list[NaceCandidate]],
    nace_selection_by_segment: dict[str, NaceSelection],
    esg_factors: list[EsgFactor],
    segment_score_by_segment: dict[str, SegmentScore],
    company_score: CompanyScore | None,
) -> list[dict[str, str]]:
    latest_notes = _latest_segment_notes(review_events)
    esg_statuses = _latest_esg_statuses(review_events)
    esg_summaries = _esg_summaries_by_segment(esg_factors, esg_statuses)
    export_rows: list[dict[str, str]] = []
    for row in rows:
        if row.status == SEGMENT_STATUS_REJECTED:
            continue
        selected_nace = _selected_nace(
            row.id,
            nace_candidates_by_segment=nace_candidates_by_segment,
            nace_selection_by_segment=nace_selection_by_segment,
        )
        segment_score = segment_score_by_segment.get(row.id)
        export_rows.append(
            {
                "company_name": document.company_name,
                "document_name": document.document_name,
                "fiscal_period": document.fiscal_period or "",
                "segment_name": row.segment_name,
                "segment_name_original": row.segment_name_original or row.segment_name,
                "segment_name_normalized": row.segment_name_normalized or "",
                "row_type": row.row_type or "",
                "segment_type": row.segment_type or "",
                "language": row.language or "",
                "revenue_value": _decimal_to_text(row.revenue_value),
                "revenue_raw": row.revenue_raw or "",
                "currency": row.currency or "",
                "scale": row.scale or "",
                "revenue_unit": _revenue_unit(row.currency, row.scale),
                "normalized_value": _decimal_to_text(row.normalized_value),
                "period_label": row.period_label or "",
                "page_ref": row.page_ref or "",
                "section_ref": row.section_ref or "",
                "evidence_text": _evidence_text(evidence_by_segment.get(row.id, [])),
                "metric_basis": row.metric_basis or "",
                "confidence": "" if row.confidence is None else str(row.confidence),
                "review_status": row.status,
                "reviewer_note": latest_notes.get(row.id, ""),
                "nace_code": selected_nace.code,
                "nace_label": selected_nace.label,
                "nace_level": selected_nace.level,
                "nace_rank": selected_nace.rank,
                "nace_match_score": selected_nace.match_score,
                "nace_rationale": selected_nace.rationale,
                "nace_confidence": selected_nace.match_score,
                "nace_needs_review": str(bool(row.needs_review)).lower()
                if row.needs_review is not None
                else "",
                "esg_factor_summary": esg_summaries.get(row.id, ""),
                "scoring_model_label": _score_model_label(segment_score, company_score),
                "base_score": _score_float(segment_score.base_score if segment_score else None),
                "esg_adjustment_score": _score_float(
                    segment_score.adjustment_score if segment_score else None
                ),
                "segment_score": _score_float(segment_score.final_score if segment_score else None),
                "weight_share": _score_float(segment_score.weight_share if segment_score else None),
                "score_rationale": _score_rationale(segment_score),
                "company_score": _score_float(
                    company_score.weighted_average_score if company_score else None
                ),
            }
        )
    return export_rows


def _audit_export(
    *,
    document: Document,
    rows: list[SegmentRow],
    evidence_by_segment: dict[str, list[SegmentEvidence]],
    validation_issues: list[ValidationIssue],
    validation_reviews: list[ValidationIssueReview],
    review_events: list[ReviewEvent],
    nace_candidates_by_segment: dict[str, list[NaceCandidate]],
    nace_selection_by_segment: dict[str, NaceSelection],
    esg_factors: list[EsgFactor],
    segment_score_by_segment: dict[str, SegmentScore],
    company_score: CompanyScore | None,
    exported_at: datetime,
) -> dict[str, Any]:
    esg_statuses = _latest_esg_statuses(review_events)
    return {
        "document": document.to_dict(),
        "segment_rows": [
            {
                "current_values": row.to_dict(),
                "evidence": [
                    evidence.to_dict() for evidence in evidence_by_segment.get(row.id, [])
                ],
                "nace_candidates": [
                    candidate.to_dict() for candidate in nace_candidates_by_segment.get(row.id, [])
                ],
                "nace_selection": (
                    nace_selection_by_segment[row.id].to_dict()
                    if row.id in nace_selection_by_segment
                    else None
                ),
                "review_events": [
                    event.to_dict() for event in review_events if event.segment_id == row.id
                ],
                "esg_factors": [
                    _esg_factor_audit_record(factor, esg_statuses)
                    for factor in esg_factors
                    if factor.segment_id == row.id
                ],
                "segment_score": (
                    segment_score_by_segment[row.id].to_dict()
                    if row.id in segment_score_by_segment
                    else None
                ),
            }
            for row in rows
        ],
        "company_score": company_score.to_dict() if company_score else None,
        "esg_factors": [
            _esg_factor_audit_record(factor, esg_statuses) for factor in esg_factors
        ],
        "evidence": [
            evidence.to_dict()
            for row in rows
            for evidence in evidence_by_segment.get(row.id, [])
        ],
        "validation_issues": [issue.to_dict() for issue in validation_issues],
        "validation_issue_reviews": [review.to_dict() for review in validation_reviews],
        "review_events": [event.to_dict() for event in review_events],
        "export_timestamp": exported_at.isoformat(),
        "pipeline": {
            "version": PIPELINE_VERSION,
            "config_summary": {
                "main_export_excludes_rejected_rows": True,
                "audit_export_includes_rejected_rows": True,
                "optional_future_columns": [
                    "segment_name_original",
                    "segment_name_normalized",
                    "row_type",
                    "segment_type",
                    "language",
                    "nace_code",
                    "nace_label",
                    "nace_level",
                    "nace_rank",
                    "nace_match_score",
                    "nace_rationale",
                    "nace_confidence",
                    "nace_needs_review",
                    "esg_factor_summary",
                    "scoring_model_label",
                    "base_score",
                    "esg_adjustment_score",
                    "segment_score",
                    "weight_share",
                    "score_rationale",
                    "company_score",
                ],
                "esg_summary_includes_statuses": [
                    ESG_STATUS_APPROVED,
                    ESG_STATUS_EDITED,
                ],
            },
        },
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=EXPORT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_xlsx(path: Path, rows: list[dict[str, str]]) -> None:
    worksheet = _worksheet_xml(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml())
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml())
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml())
        archive.writestr("xl/styles.xml", _styles_xml())
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def _worksheet_xml(rows: list[dict[str, str]]) -> bytes:
    worksheet = Element(
        "worksheet",
        {
            "xmlns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "xmlns:r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        },
    )
    sheet_data = SubElement(worksheet, "sheetData")
    _append_xlsx_row(sheet_data, 1, EXPORT_COLUMNS)
    for index, row in enumerate(rows, start=2):
        _append_xlsx_row(sheet_data, index, [row[column] for column in EXPORT_COLUMNS])
    return _xml_bytes(worksheet)


def _append_xlsx_row(parent: Element, row_index: int, values: list[str]) -> None:
    row_element = SubElement(parent, "row", {"r": str(row_index)})
    for column_index, value in enumerate(values, start=1):
        cell_ref = f"{_xlsx_column_name(column_index)}{row_index}"
        cell = SubElement(row_element, "c", {"r": cell_ref, "t": "inlineStr"})
        inline = SubElement(cell, "is")
        text = SubElement(inline, "t")
        text.text = value


def _content_types_xml() -> bytes:
    types = Element(
        "Types",
        {"xmlns": "http://schemas.openxmlformats.org/package/2006/content-types"},
    )
    SubElement(
        types,
        "Default",
        {
            "Extension": "rels",
            "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
        },
    )
    SubElement(types, "Default", {"Extension": "xml", "ContentType": "application/xml"})
    SubElement(
        types,
        "Override",
        {
            "PartName": "/xl/workbook.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        },
    )
    SubElement(
        types,
        "Override",
        {
            "PartName": "/xl/worksheets/sheet1.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
        },
    )
    SubElement(
        types,
        "Override",
        {
            "PartName": "/xl/styles.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
        },
    )
    return _xml_bytes(types)


def _root_rels_xml() -> bytes:
    relationships = Element(
        "Relationships",
        {"xmlns": "http://schemas.openxmlformats.org/package/2006/relationships"},
    )
    SubElement(
        relationships,
        "Relationship",
        {
            "Id": "rId1",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
            "Target": "xl/workbook.xml",
        },
    )
    return _xml_bytes(relationships)


def _workbook_xml() -> bytes:
    workbook = Element(
        "workbook",
        {
            "xmlns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "xmlns:r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        },
    )
    sheets = SubElement(workbook, "sheets")
    SubElement(
        sheets,
        "sheet",
        {"name": "Revenue Segments", "sheetId": "1", "r:id": "rId1"},
    )
    return _xml_bytes(workbook)


def _workbook_rels_xml() -> bytes:
    relationships = Element(
        "Relationships",
        {"xmlns": "http://schemas.openxmlformats.org/package/2006/relationships"},
    )
    SubElement(
        relationships,
        "Relationship",
        {
            "Id": "rId1",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
            "Target": "worksheets/sheet1.xml",
        },
    )
    SubElement(
        relationships,
        "Relationship",
        {
            "Id": "rId2",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
            "Target": "styles.xml",
        },
    )
    return _xml_bytes(relationships)


def _styles_xml() -> bytes:
    style_sheet = Element(
        "styleSheet",
        {"xmlns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"},
    )
    fonts = SubElement(style_sheet, "fonts", {"count": "1"})
    SubElement(fonts, "font")
    fills = SubElement(style_sheet, "fills", {"count": "1"})
    SubElement(fills, "fill")
    borders = SubElement(style_sheet, "borders", {"count": "1"})
    SubElement(borders, "border")
    cell_style_xfs = SubElement(style_sheet, "cellStyleXfs", {"count": "1"})
    SubElement(
        cell_style_xfs,
        "xf",
        {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"},
    )
    cell_xfs = SubElement(style_sheet, "cellXfs", {"count": "1"})
    SubElement(
        cell_xfs,
        "xf",
        {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0", "xfId": "0"},
    )
    return _xml_bytes(style_sheet)


def _xml_bytes(element: Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + tostring(
        element,
        encoding="utf-8",
        xml_declaration=False,
    )


def _xlsx_column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _latest_segment_notes(events: list[ReviewEvent]) -> dict[str, str]:
    notes: dict[str, str] = {}
    for event in events:
        if event.segment_id and event.note:
            notes[event.segment_id] = event.note
    return notes


def _latest_esg_statuses(events: list[ReviewEvent]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for event in events:
        if not event.field_changed or not event.field_changed.startswith("esg_factor:"):
            continue
        parts = event.field_changed.split(":")
        if len(parts) == 3 and parts[2] == "status" and event.new_value:
            statuses[parts[1]] = event.new_value
    return statuses


def _esg_summaries_by_segment(
    factors: list[EsgFactor],
    statuses: dict[str, str],
) -> dict[str, str]:
    summaries: dict[str, list[str]] = {}
    for factor in factors:
        if factor.segment_id is None:
            continue
        status = statuses.get(factor.id)
        if status not in {ESG_STATUS_APPROVED, ESG_STATUS_EDITED}:
            continue
        summaries.setdefault(factor.segment_id, []).append(
            f"{factor.factor_type} ({factor.polarity}): {factor.description}"
        )
    return {segment_id: " | ".join(items) for segment_id, items in summaries.items()}


def _esg_factor_audit_record(
    factor: EsgFactor,
    statuses: dict[str, str],
) -> dict[str, Any]:
    record = factor.to_dict()
    review_status = statuses.get(factor.id, "pending")
    record["review_status"] = review_status
    record["included_in_main_export_summary"] = (
        review_status in {ESG_STATUS_APPROVED, ESG_STATUS_EDITED}
        and not factor.is_company_wide
    )
    if review_status == ESG_STATUS_REJECTED:
        record["excluded_reason"] = "rejected"
    elif factor.is_company_wide:
        record["excluded_reason"] = "company_wide"
    elif review_status not in {ESG_STATUS_APPROVED, ESG_STATUS_EDITED}:
        record["excluded_reason"] = "pending_review"
    else:
        record["excluded_reason"] = None
    return record


def _evidence_text(evidence_items: list[SegmentEvidence]) -> str:
    return " ".join(
        item.snippet_text.strip() for item in evidence_items if item.snippet_text.strip()
    )


def _revenue_unit(currency: str | None, scale: str | None) -> str:
    parts = [part for part in (currency, scale) if part]
    return " ".join(parts)


def _selected_nace(
    segment_id: str,
    *,
    nace_candidates_by_segment: dict[str, list[NaceCandidate]],
    nace_selection_by_segment: dict[str, NaceSelection],
) -> _SelectedNaceExport:
    selection = nace_selection_by_segment.get(segment_id)
    if selection is not None:
        rank = _selection_rank(selection, nace_candidates_by_segment.get(segment_id, []))
        return _SelectedNaceExport(
            code=selection.nace_code,
            label=selection.nace_label,
            level=str(selection.nace_level),
            rank=rank,
            match_score="" if selection.match_score is None else str(selection.match_score),
            rationale=selection.rationale or "",
        )

    candidates = nace_candidates_by_segment.get(segment_id, [])
    if not candidates:
        return _SelectedNaceExport()
    top_candidate = candidates[0]
    return _SelectedNaceExport(
        code=top_candidate.nace_code,
        label=top_candidate.nace_label,
        level=str(top_candidate.nace_level),
        rank=str(top_candidate.rank),
        match_score=str(top_candidate.match_score),
        rationale=top_candidate.rationale or "",
    )


def _selection_rank(selection: NaceSelection, candidates: list[NaceCandidate]) -> str:
    for candidate in candidates:
        if candidate.nace_code == selection.nace_code:
            return str(candidate.rank)
    return ""


def _score_float(value: float | None) -> str:
    return "" if value is None else str(value)


def _score_model_label(
    segment_score: SegmentScore | None,
    company_score: CompanyScore | None,
) -> str:
    rationale = _score_rationale_json(segment_score) or _score_rationale_json(company_score)
    if not rationale:
        return ""
    return str(rationale.get("model_label", ""))


def _score_rationale(score: SegmentScore | None) -> str:
    if score is None:
        return ""
    rationale = _score_rationale_json(score)
    if not rationale:
        return score.rationale or ""
    base = rationale.get("base_score_rationale", "")
    adjustments = rationale.get("esg_adjustments", [])
    adjustment_text = "; ".join(
        str(item.get("rationale", "")) for item in adjustments if item.get("rationale")
    )
    return " | ".join(part for part in (base, adjustment_text) if part)


def _score_rationale_json(score: SegmentScore | CompanyScore | None) -> dict[str, Any] | None:
    if score is None or not score.rationale:
        return None
    try:
        loaded = json.loads(score.rationale)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _decimal_to_text(value: Decimal | None) -> str:
    return str(value) if value is not None else ""
