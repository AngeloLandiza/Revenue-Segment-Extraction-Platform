from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from revenue_segment_extractor.ingestion import PdfIngestionService
from revenue_segment_extractor.persistence import (
    DEFAULT_DATABASE_PATH,
    SQLiteRepository,
    connect_database,
    initialize_database,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register and parse a source PDF into local SQLite storage."
    )
    parser.add_argument("pdf_path", help="Annual report or 10-K PDF path.")
    parser.add_argument(
        "--company-name",
        help="Company name for the document. If omitted, ingestion auto-detects only high-confidence names.",
    )
    parser.add_argument(
        "--document-name",
        help="Stored document name. Defaults to the PDF filename.",
    )
    parser.add_argument("--fiscal-period", help="Optional fiscal period label, such as FY2025.")
    parser.add_argument("--currency", help="Optional document-level currency hint.")
    parser.add_argument("--scale", help="Optional document-level scale hint.")
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=15,
        help="Maximum candidate pages to store. Defaults to 15.",
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="SQLite database path. Defaults to data/revenue_segment_extractor.sqlite3.",
    )
    args = parser.parse_args()

    connection = connect_database(args.database)
    try:
        initialize_database(connection)
        summary = PdfIngestionService(SQLiteRepository(connection)).ingest_pdf(
            pdf_path=args.pdf_path,
            company_name=args.company_name,
            document_name=args.document_name,
            fiscal_period=args.fiscal_period,
            currency=args.currency,
            scale=args.scale,
            candidate_limit=args.candidate_limit,
        )
        print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
