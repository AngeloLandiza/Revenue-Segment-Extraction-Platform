from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

from fitch_extractor.persistence import (
    ReviewService,
    SQLiteRepository,
    connect_database,
    initialize_database,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliSmokeTest(unittest.TestCase):
    def test_cli_happy_path_ingest_extract_review_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "fitch.sqlite3"
            pdf_path = temp_path / "sample-10k.pdf"
            export_root = temp_path / "exports"
            _write_sample_pdf(pdf_path)

            _run_script("scripts/manage_db.py", "--path", str(db_path))
            ingestion = _run_json_script(
                "scripts/ingest_pdf.py",
                str(pdf_path),
                "--company-name",
                "Example Fitch Co.",
                "--fiscal-period",
                "FY2025",
                "--currency",
                "USD",
                "--scale",
                "millions",
                "--database",
                str(db_path),
            )
            document_id = ingestion["document"]["id"]

            extraction = _run_json_script(
                "scripts/extract_revenue_segments.py",
                document_id,
                "--provider",
                "fake",
                "--disable-verification",
                "--disable-arbitration",
                "--database",
                str(db_path),
            )
            self.assertEqual(2, extraction["persisted_row_count"])

            connection = connect_database(db_path)
            try:
                initialize_database(connection)
                repo = SQLiteRepository(connection)
                review = ReviewService(repo)
                for row in repo.list_segment_rows(document_id):
                    review.approve_segment_row(
                        document_id=document_id,
                        segment_id=row.id,
                        reviewer="cli-smoke@example.com",
                    )
                review.approve_document(
                    document_id=document_id,
                    reviewer="cli-smoke@example.com",
                )
            finally:
                connection.close()

            export = _run_json_script(
                "scripts/export_document.py",
                document_id,
                "--database",
                str(db_path),
                "--output-dir",
                str(export_root),
            )
            csv_path = Path(export["csv_path"])
            json_path = Path(export["json_path"])
            xlsx_path = Path(export["xlsx_path"])

            self.assertTrue(csv_path.exists())
            self.assertTrue(json_path.exists())
            self.assertTrue(xlsx_path.exists())
            with csv_path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(["Commercial", "Consumer"], [row["segment_name"] for row in rows])

    def test_export_cli_blocks_unapproved_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "fitch.sqlite3"
            connection = connect_database(db_path)
            try:
                initialize_database(connection)
                document = SQLiteRepository(connection).create_document(
                    company_name="Example Fitch Co.",
                    document_name="annual-report.pdf",
                    source_path="annual-report.pdf",
                    fiscal_period="FY2025",
                )
            finally:
                connection.close()

            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts/export_document.py"),
                    document.id,
                    "--database",
                    str(db_path),
                    "--output-dir",
                    str(Path(temp_dir) / "exports"),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(2, result.returncode)
            self.assertIn("Cannot export before document approval", result.stderr)


def _run_script(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / script), *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )


def _run_json_script(script: str, *args: str) -> dict:
    result = _run_script(script, *args)
    return json.loads(result.stdout)


def _write_sample_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text(
        (72, 72),
        "Example Fitch Co. Annual Report\nFinancial statements\nNote 4 - Operating Segments",
    )
    page.insert_text((72, 120), "Revenue by segment and external revenue, USD millions")
    _draw_table(
        page,
        x=72,
        y=155,
        column_widths=[150, 150, 150],
        row_height=28,
        rows=[
            ["Segment", "External revenue", "Total"],
            ["Commercial", "$120", "$120"],
            ["Consumer", "$180", "$180"],
            ["Total", "$300", "$300"],
        ],
    )
    document.save(path)
    document.close()


def _draw_table(
    page: fitz.Page,
    *,
    x: float,
    y: float,
    column_widths: list[float],
    row_height: float,
    rows: list[list[str]],
) -> None:
    table_width = sum(column_widths)
    table_height = row_height * len(rows)

    for row_index in range(len(rows) + 1):
        y_pos = y + row_index * row_height
        page.draw_line((x, y_pos), (x + table_width, y_pos), width=0.8)

    x_pos = x
    page.draw_line((x_pos, y), (x_pos, y + table_height), width=0.8)
    for width in column_widths:
        x_pos += width
        page.draw_line((x_pos, y), (x_pos, y + table_height), width=0.8)

    for row_index, row in enumerate(rows):
        cell_x = x
        for column_index, cell_text in enumerate(row):
            cell_rect = fitz.Rect(
                cell_x + 4,
                y + row_index * row_height + 7,
                cell_x + column_widths[column_index] - 4,
                y + (row_index + 1) * row_height - 4,
            )
            page.insert_textbox(cell_rect, cell_text, fontsize=9)
            cell_x += column_widths[column_index]
