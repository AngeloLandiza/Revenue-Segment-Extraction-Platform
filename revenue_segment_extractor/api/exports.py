from __future__ import annotations

from pathlib import Path

from revenue_segment_extractor.api.schemas import DocumentExportResponse
from revenue_segment_extractor.exporting import ExportService
from revenue_segment_extractor.persistence.repository import SQLiteRepository


def export_reviewed_document(
    repository: SQLiteRepository,
    document_id: str,
    export_root: str | Path = "exports",
) -> DocumentExportResponse:
    bundle = ExportService(repository, export_root).export_document(document_id)
    return DocumentExportResponse(
        document_id=bundle.document_id,
        output_dir=str(bundle.output_dir),
        csv_path=str(bundle.csv_path),
        json_path=str(bundle.json_path),
        xlsx_path=str(bundle.xlsx_path),
        exported_at=bundle.exported_at,
        records=bundle.records,
    )
