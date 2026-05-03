#!/usr/bin/env python3
"""Ingest PDFs, run revenue extraction, and compare persisted segments to sample CSV ground truth."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from revenue_segment_extractor.extraction import (
    ExtractionSettings,
    LLMProviderError,
    RevenueExtractionService,
    create_provider,
)
from revenue_segment_extractor.extraction.normalization import SCALE_MULTIPLIERS
from revenue_segment_extractor.ingestion import PdfIngestionService
from revenue_segment_extractor.models import SegmentRow
from revenue_segment_extractor.persistence import (
    DEFAULT_DATABASE_PATH,
    SQLiteRepository,
    connect_database,
    initialize_database,
)

DEFAULT_GROUND_TRUTH_CSV = (
    Path("/Users/angelolandiza/Downloads/sample_ground_truth - Sheet1.csv")
)
DEFAULT_PDF_PATHS: tuple[Path, ...] = (
    Path("/Users/angelolandiza/Downloads/Archive1 64-1427/82_Orsted Annual Report 2025.pdf"),
    Path(
        "/Users/angelolandiza/Downloads/Archive1 64-1427/"
        "726_Alliander_Annual_Report_2024 (4).pdf"
    ),
    Path("/Users/angelolandiza/Downloads/Archive1 64-1427/728_2025 Annual report.pdf"),
    Path("/Users/angelolandiza/Downloads/Archive1 64-1427/766_2024 annual report.pdf"),
    Path(
        "/Users/angelolandiza/Downloads/Archive5 3923-11242/"
        "11140_2025 Consolidated Financial Statements (Dec 2025).pdf"
    ),
)

SIMILARITY_FLOOR = 0.42


@dataclass(frozen=True)
class GroundTruthRow:
    doc: str
    pdf_page_no: int
    segment: str
    value_label: str
    value: Decimal
    reporting_period: str
    currency: str
    unit: str

    def absolute_amount(self) -> Decimal:
        key = self.unit.strip().casefold()
        mult = {
            "millions": Decimal("1000000"),
            "million": Decimal("1000000"),
            "thousands": Decimal("1000"),
            "thousand": Decimal("1000"),
            "ones": Decimal("1"),
        }.get(key)
        if mult is None:
            raise ValueError(f"Unknown ground-truth unit: {self.unit!r}")
        return self.value * mult


def normalize_segment_label(s: str) -> str:
    s = " ".join(s.casefold().strip().split())
    s = s.replace("bionergy", "bioenergy")
    s = s.replace("/ ", "/").replace(" /", "/")
    return s


def segment_match_score(gt_segment: str, extracted_segment: str) -> float:
    a = normalize_segment_label(gt_segment)
    b = normalize_segment_label(extracted_segment)
    base = SequenceMatcher(None, a, b).ratio()
    if "total" in a and "total" in b:
        base = max(base, 0.82)
    return base


def extracted_absolute_amount(seg: SegmentRow) -> Decimal | None:
    if seg.revenue_value is None:
        return None
    scale_key = (seg.scale or "ones").strip().casefold()
    mult = SCALE_MULTIPLIERS.get(scale_key)
    if mult is None:
        mult = Decimal("1")
    return seg.revenue_value * mult


def currency_matches(gt: str, ext: str | None) -> bool:
    if ext is None:
        return False
    return gt.strip().casefold() == ext.strip().casefold()


def page_ref_matches(page_ref: str | None, expected_page: int) -> bool:
    if not page_ref:
        return True
    match = re.search(r"\d+", page_ref)
    return bool(match and int(match.group(0)) == expected_page)


def company_name_from_pdf(path: Path) -> str:
    stem = path.stem
    if "_" in stem:
        return stem.split("_", 1)[1].strip()
    return stem.strip()


def load_ground_truth(csv_path: Path) -> dict[str, list[GroundTruthRow]]:
    by_doc: dict[str, list[GroundTruthRow]] = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            if not raw.get("doc"):
                continue
            row = GroundTruthRow(
                doc=raw["doc"].strip(),
                pdf_page_no=int(raw["pdf_page_no"]),
                segment=raw["segment"].strip(),
                value_label=raw["value_label"].strip(),
                value=Decimal(str(raw["value"]).replace(",", "")),
                reporting_period=raw["reporting_period"].strip(),
                currency=raw["currency"].strip(),
                unit=raw["unit"].strip(),
            )
            by_doc.setdefault(row.doc, []).append(row)
    return by_doc


def greedy_match(
    gt_rows: list[GroundTruthRow],
    ext_rows: list[SegmentRow],
) -> tuple[list[tuple[GroundTruthRow, SegmentRow, float]], list[int], list[int]]:
    weights: list[tuple[float, int, int]] = []
    for i, gt in enumerate(gt_rows):
        for j, ex in enumerate(ext_rows):
            weights.append((segment_match_score(gt.segment, ex.segment_name), i, j))
    weights.sort(key=lambda t: t[0], reverse=True)
    used_gt: set[int] = set()
    used_ext: set[int] = set()
    pairs: list[tuple[GroundTruthRow, SegmentRow, float]] = []
    for score, i, j in weights:
        if score < SIMILARITY_FLOOR:
            break
        if i in used_gt or j in used_ext:
            continue
        used_gt.add(i)
        used_ext.add(j)
        pairs.append((gt_rows[i], ext_rows[j], score))
    missing_gt = [i for i in range(len(gt_rows)) if i not in used_gt]
    extra_ext = [j for j in range(len(ext_rows)) if j not in used_ext]
    return pairs, missing_gt, extra_ext


def run_pipeline(
    pdf_paths: list[Path],
    csv_path: Path,
    database_path: Path,
) -> None:
    gt_by_doc = load_ground_truth(csv_path)

    connection = connect_database(str(database_path))
    try:
        initialize_database(connection)
        repo = SQLiteRepository(connection)
        ingestion = PdfIngestionService(repo)
        settings = ExtractionSettings.from_env()
        try:
            provider = create_provider(settings.provider_name)
        except (ValueError, LLMProviderError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc
        extractor = RevenueExtractionService(repo, provider, settings)

        for pdf_path in pdf_paths:
            pdf_path = pdf_path.expanduser().resolve()
            if not pdf_path.is_file():
                print(f"\n=== SKIP (missing file): {pdf_path}\n")
                continue

            doc_key = pdf_path.name
            gt_rows = gt_by_doc.get(doc_key)
            if gt_rows is None:
                print(f"\n=== WARNING: No ground-truth rows for document key {doc_key!r}\n")

            print(f"\n{'=' * 72}")
            print(f"Ingest + extract: {pdf_path.name}")
            print(f"{'=' * 72}")

            ingest_summary = ingestion.ingest_pdf(
                pdf_path=str(pdf_path),
                company_name=company_name_from_pdf(pdf_path),
            )
            doc_id = ingest_summary.document.id
            extract_summary = extractor.extract_document(doc_id)
            ext_rows = repo.list_segment_rows(doc_id)

            print(
                f"document_id={doc_id} | persisted_rows={extract_summary.persisted_row_count} "
                f"| bundles={extract_summary.bundle_count} | "
                f"validation_issues={extract_summary.validation_issue_count}"
            )

            if not gt_rows:
                print("(No CSV ground truth for this filename — skipping comparison.)")
                continue

            pairs, missing_gt_idx, extra_ext_idx = greedy_match(gt_rows, ext_rows)

            correct: list[str] = []
            wrong: list[str] = []
            for gt, seg, score in pairs:
                gt_abs = gt.absolute_amount()
                ext_abs = extracted_absolute_amount(seg)
                page_ok = page_ref_matches(seg.page_ref, gt.pdf_page_no)

                parts = [
                    f"segment '{gt.segment}' ↔ extracted '{seg.segment_name}' "
                    f"(similarity={score:.2f})",
                    f"GT page={gt.pdf_page_no}",
                ]
                if seg.page_ref:
                    parts.append(f"extracted page_ref={seg.page_ref}{'' if page_ok else ' ⚠ page mismatch'}")

                if ext_abs is None:
                    wrong.append(
                        "; ".join(parts)
                        + f" | WRONG: extracted revenue_value is null "
                        f"(GT absolute amount={gt_abs} {gt.currency})"
                    )
                    continue
                if not currency_matches(gt.currency, seg.currency):
                    wrong.append(
                        "; ".join(parts)
                        + f" | WRONG: currency extracted={seg.currency!r} vs GT={gt.currency!r}; "
                        f"amounts GT_abs={gt_abs} ext_abs={ext_abs}"
                    )
                    continue
                if gt_abs != ext_abs:
                    wrong.append(
                        "; ".join(parts)
                        + f" | WRONG: amount GT_abs={gt_abs} != extracted_abs={ext_abs} "
                        f"(extracted revenue_value={seg.revenue_value}, scale={seg.scale})"
                    )
                    continue
                note = "" if page_ok else " (value/currency OK; page differs)"
                correct.append("; ".join(parts) + f" | OK absolute amount={gt_abs} {gt.currency}{note}")

            for i in missing_gt_idx:
                gt = gt_rows[i]
                missing_line = (
                    f"MISSING: GT segment={gt.segment!r}, value_label={gt.value_label!r}, "
                    f"page={gt.pdf_page_no}, amount={gt.value} {gt.currency} ({gt.unit}), "
                    f"absolute={gt.absolute_amount()}"
                )
                wrong.append(missing_line)

            for j in extra_ext_idx:
                seg = ext_rows[j]
                ext_abs = extracted_absolute_amount(seg)
                extra_line = (
                    f"EXTRA (no GT match): extracted segment={seg.segment_name!r}, "
                    f"revenue_value={seg.revenue_value}, scale={seg.scale}, currency={seg.currency}, "
                    f"page_ref={seg.page_ref}, metric_basis={seg.metric_basis}, absolute≈{ext_abs}"
                )
                wrong.append(extra_line)

            print("\n--- Correct (matched segment + currency + absolute amount) ---")
            if correct:
                for line in sorted(correct):
                    print(line)
            else:
                print("(none)")

            print("\n--- Wrong / missing / extra ---")
            if wrong:
                for line in wrong:
                    print(line)
            else:
                print("(none)")
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest listed PDFs, run revenue extraction, and compare to ground-truth CSV. "
            "Requires RSE_EXTRACTION_PROVIDER / API credentials same as scripts/extract_revenue_segments.py."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_GROUND_TRUTH_CSV,
        help="Ground truth CSV path.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(DEFAULT_DATABASE_PATH),
        help="SQLite database path.",
    )
    parser.add_argument(
        "pdfs",
        nargs="*",
        type=Path,
        help="PDF paths (default: built-in list of five archive PDFs).",
    )
    args = parser.parse_args()
    pdfs = list(args.pdfs) if args.pdfs else list(DEFAULT_PDF_PATHS)
    run_pipeline(pdfs, args.csv.expanduser().resolve(), args.database.expanduser().resolve())


if __name__ == "__main__":
    main()
