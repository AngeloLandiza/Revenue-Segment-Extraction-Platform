from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from revenue_segment_extractor.persistence import (
    DEFAULT_DATABASE_PATH,
    initialize_database_file,
    reset_database_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize or reset the local SQLite database.")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_DATABASE_PATH),
        help="SQLite database path. Defaults to data/revenue_segment_extractor.sqlite3.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all local tables.",
    )
    args = parser.parse_args()

    database_path = Path(args.path)
    if args.reset:
        reset_database_file(database_path)
        print(f"Reset database at {database_path}")
    else:
        initialize_database_file(database_path)
        print(f"Initialized database at {database_path}")


if __name__ == "__main__":
    main()
