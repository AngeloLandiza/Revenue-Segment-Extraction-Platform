from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fitch_extractor.exporting import ExportService
from fitch_extractor.persistence import (
    DEFAULT_DATABASE_PATH,
    SQLiteRepository,
    connect_database,
    initialize_database,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export an approved Fitch document to CSV, XLSX, and audit JSON."
    )
    parser.add_argument("document_id", help="Approved document ID to export.")
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="SQLite database path. Defaults to data/fitch_extractor.sqlite3.",
    )
    parser.add_argument(
        "--output-dir",
        default="exports",
        help="Export root directory. Defaults to exports.",
    )
    args = parser.parse_args()

    connection = connect_database(args.database)
    try:
        initialize_database(connection)
        bundle = ExportService(SQLiteRepository(connection), args.output_dir).export_document(
            args.document_id
        )
        print(
            json.dumps(
                {
                    "document_id": bundle.document_id,
                    "output_dir": str(bundle.output_dir),
                    "csv_path": str(bundle.csv_path),
                    "xlsx_path": str(bundle.xlsx_path),
                    "json_path": str(bundle.json_path),
                    "exported_at": bundle.exported_at.isoformat(),
                },
                indent=2,
                sort_keys=True,
            )
        )
    except (KeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    finally:
        connection.close()


if __name__ == "__main__":
    main()
