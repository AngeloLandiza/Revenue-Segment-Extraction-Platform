from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from revenue_segment_extractor.extraction import (
    ExtractionSettings,
    LLMProviderError,
    RevenueExtractionService,
    create_provider,
)
from revenue_segment_extractor.persistence import (
    DEFAULT_DATABASE_PATH,
    SQLiteRepository,
    connect_database,
    initialize_database,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run first-pass revenue segment extraction for an ingested document."
    )
    parser.add_argument("document_id", help="Document ID created by scripts/ingest_pdf.py.")
    parser.add_argument(
        "--provider",
        help="Extraction provider: fake or anthropic. Defaults to RSE_EXTRACTION_PROVIDER or anthropic.",
    )
    parser.add_argument(
        "--model",
        help="Model name. Defaults to RSE_EXTRACTION_MODEL or the configured fallback.",
    )
    parser.add_argument(
        "--verification-model",
        help="Second-pass verification model. Defaults to RSE_VERIFICATION_MODEL or the extraction model.",
    )
    parser.add_argument(
        "--arbitration-model",
        help="Arbitration model. Defaults to RSE_ARBITRATION_MODEL or the extraction model.",
    )
    parser.add_argument(
        "--disable-verification",
        action="store_true",
        help="Disable second-pass verification for this run.",
    )
    parser.add_argument(
        "--disable-arbitration",
        action="store_true",
        help="Disable arbitration for this run.",
    )
    parser.add_argument(
        "--page-bundle-size",
        type=int,
        help="Maximum adjacent candidate pages per LLM prompt bundle.",
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        help="Optional number of ranked candidate pages to extract from.",
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="SQLite database path. Defaults to data/revenue_segment_extractor.sqlite3.",
    )
    args = parser.parse_args()

    settings = ExtractionSettings.from_env()
    if args.provider:
        settings = replace(settings, provider_name=args.provider)
    if args.model:
        settings = replace(settings, model=args.model)
    if args.verification_model:
        settings = replace(settings, verification_model=args.verification_model)
    if args.arbitration_model:
        settings = replace(settings, arbitration_model=args.arbitration_model)
    if args.disable_verification:
        settings = replace(settings, enable_second_pass_verification=False)
    if args.disable_arbitration:
        settings = replace(settings, enable_arbitration=False)
    if args.page_bundle_size:
        settings = replace(settings, page_bundle_size=args.page_bundle_size)

    try:
        provider = create_provider(settings.provider_name)
    except (ValueError, LLMProviderError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    connection = connect_database(args.database)
    try:
        initialize_database(connection)
        summary = RevenueExtractionService(
            SQLiteRepository(connection),
            provider,
            settings,
            verification_provider=provider,
            arbitration_provider=provider,
        ).extract_document(
            args.document_id,
            candidate_limit=args.candidate_limit,
            page_bundle_size=settings.page_bundle_size,
        )
        print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
