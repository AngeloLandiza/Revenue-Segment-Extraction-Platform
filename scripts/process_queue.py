from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from revenue_segment_extractor.persistence import (
    DEFAULT_DATABASE_PATH,
    SQLiteRepository,
    connect_database,
    initialize_database,
)
from revenue_segment_extractor.queueing import DocumentQueueService


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued document extraction jobs.")
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="SQLite database path. Defaults to data/revenue_segment_extractor.sqlite3.",
    )
    parser.add_argument(
        "--worker-id",
        default="terminal-worker",
        help="Worker identifier stored on claimed jobs.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all pending jobs instead of only the next job.",
    )
    args = parser.parse_args()

    connection = connect_database(args.database)
    processed: list[dict[str, object]] = []
    try:
        initialize_database(connection)
        service = DocumentQueueService(SQLiteRepository(connection))
        while True:
            result = service.process_next(worker_id=args.worker_id)
            if result is None:
                break
            processed.append(
                {
                    "job_id": result.job.id,
                    "document_id": result.job.document_id,
                    "status": result.job.status,
                    "error_message": result.error_message,
                }
            )
            if not args.all:
                break
    finally:
        connection.close()

    print(json.dumps({"processed": processed}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
