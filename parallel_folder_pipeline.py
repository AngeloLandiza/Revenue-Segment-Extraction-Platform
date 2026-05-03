#!/usr/bin/env python3
"""Run ingest + revenue + NACE + ESG for every PDF in a folder using parallel worker processes."""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from revenue_segment_extractor.extraction import (
    ExtractionSettings,
    EsgExtractionService,
    LLMProviderError,
    RevenueExtractionService,
    create_provider,
)
from revenue_segment_extractor.ingestion import PdfIngestionService
from revenue_segment_extractor.nace import NaceMappingService
from revenue_segment_extractor.persistence import (
    DEFAULT_DATABASE_PATH,
    SQLiteRepository,
    connect_database,
    initialize_database,
)

METADATA_FIELDS = ("company_name", "fiscal_period", "currency", "scale")
METADATA_EXAMPLES = {
    "company_name": "Example: Allianz SE",
    "fiscal_period": "Example: FY2025",
    "currency": "Example: EUR",
    "scale": "Example: millions",
}


def _pdf_paths(folder: Path, *, recursive: bool) -> tuple[Path, ...]:
    if recursive:
        raw = sorted(folder.rglob("*.pdf"))
        raw += sorted(folder.rglob("*.PDF"))
    else:
        raw = sorted(folder.glob("*.pdf"))
        raw += sorted(folder.glob("*.PDF"))

    uniq: dict[str, Path] = {}
    for path in raw:
        if path.is_file():
            uniq[str(path.resolve())] = path

    return tuple(uniq[s] for s in sorted(uniq))


def _process_one_pdf(payload: dict[str, Any]) -> dict[str, Any]:
    pdf_path_str = str(payload["pdf"])
    database_path_str = str(payload["database"])
    provider_name = str(payload["provider"])
    candidate_limit = int(payload["candidate_limit"])
    metadata = payload.get("metadata", {})
    pdf_path = Path(pdf_path_str)

    logger = logging.getLogger(__name__)

    conn = connect_database(database_path_str)
    try:
        conn.execute("PRAGMA busy_timeout = 120000")
        initialize_database(conn)
        repository = SQLiteRepository(conn)

        try:
            ingestion = PdfIngestionService(repository).ingest_pdf(
                pdf_path=pdf_path,
                company_name=metadata.get("company_name"),
                fiscal_period=metadata.get("fiscal_period"),
                currency=metadata.get("currency"),
                scale=metadata.get("scale"),
                candidate_limit=candidate_limit,
            )
            document_id = ingestion.document.id
        except ValueError as exc:
            if _is_manual_metadata_error(str(exc)):
                return _manual_metadata_result(
                    pdf_path_str,
                    missing_fields=METADATA_FIELDS,
                    error=str(exc),
                )
            logger.warning("%s ingest failed: %s", pdf_path, exc)
            return {
                "pdf": pdf_path_str,
                "document_id": None,
                "ok": False,
                "phase": "ingest",
                "error": str(exc),
            }
        except OSError as exc:
            logger.warning("%s ingest I/O error: %s", pdf_path, exc)
            return {
                "pdf": pdf_path_str,
                "document_id": None,
                "ok": False,
                "phase": "ingest",
                "error": str(exc),
            }

        missing_metadata = _missing_document_metadata(ingestion.document)
        if missing_metadata:
            repository.delete_document(document_id)
            return _manual_metadata_result(
                pdf_path_str,
                missing_fields=missing_metadata,
                error=(
                    "Metadata could not be auto-detected with high confidence: "
                    + ", ".join(missing_metadata)
                ),
            )

        settings = ExtractionSettings.from_env()
        try:
            provider = create_provider(provider_name)
            RevenueExtractionService(
                repository,
                provider,
                settings,
                verification_provider=provider,
                arbitration_provider=provider,
            ).extract_document(document_id)
        except (LLMProviderError, OSError, ValueError) as exc:
            logger.exception("%s revenue extraction failed (doc=%s)", pdf_path, document_id)
            return {
                "pdf": pdf_path_str,
                "document_id": document_id,
                "ok": False,
                "phase": "revenue",
                "error": str(exc),
            }

        try:
            NaceMappingService(
                repository,
                provider=provider,
                model=settings.model,
            ).map_document(document_id)
        except (FileNotFoundError, ValueError, LLMProviderError) as exc:
            logger.warning("%s NACE skipped (doc=%s): %s", pdf_path, document_id, exc)

        try:
            EsgExtractionService(repository, provider, settings).extract_document(document_id)
        except (LLMProviderError, OSError, ValueError) as exc:
            logger.exception("%s ESG extraction failed (doc=%s)", pdf_path, document_id)
            return {
                "pdf": pdf_path_str,
                "document_id": document_id,
                "ok": False,
                "phase": "esg",
                "error": str(exc),
            }

        return {"pdf": pdf_path_str, "document_id": document_id, "ok": True}
    finally:
        conn.close()


def _missing_document_metadata(document: Any) -> tuple[str, ...]:
    missing: list[str] = []
    for field in METADATA_FIELDS:
        value = getattr(document, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return tuple(missing)


def _manual_metadata_result(
    pdf_path: str,
    *,
    missing_fields: tuple[str, ...],
    error: str,
) -> dict[str, Any]:
    return {
        "pdf": pdf_path,
        "document_id": None,
        "ok": False,
        "phase": "metadata",
        "manual_input_required": True,
        "missing_fields": tuple(missing_fields),
        "error": error,
    }


def _is_manual_metadata_error(error: str) -> bool:
    return "company_name could not be auto-detected" in error


def _with_manual_metadata(payload: dict[str, Any], metadata: dict[str, str]) -> dict[str, Any]:
    merged_metadata = dict(payload.get("metadata", {}))
    merged_metadata.update(metadata)
    retry_payload = dict(payload)
    retry_payload["metadata"] = merged_metadata
    return retry_payload


def _prompt_for_manual_metadata(result: dict[str, Any]) -> dict[str, str]:
    pdf = result.get("pdf", "?")
    fields = tuple(result.get("missing_fields", ()))
    print("\nManual input required", flush=True)
    print(f"PDF: {pdf}", flush=True)
    print(f"Reason: {result.get('error', 'metadata missing')}", flush=True)
    answers: dict[str, str] = {}
    for field in fields:
        label = field.replace("_", " ").title()
        example = METADATA_EXAMPLES.get(field, "Enter value")
        while True:
            value = input(f"{label} ({example}): ").strip()
            if value:
                answers[field] = value
                break
            print("Value is required so this PDF can continue.", flush=True)
    return answers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest PDFs from a folder and run the full automatic pipeline "
            "(revenue extraction → NACE → ESG) with a bounded pool of concurrent workers."
        )
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Directory containing annual-report PDF files.",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=4,
        help="Maximum concurrent PDF pipelines (each uses its own DB connection). Default: 4.",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Also search subfolders for PDFs.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (shared across workers). Default: {DEFAULT_DATABASE_PATH}",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "Provider key passed to create_provider() "
            "(default: RSE_EXTRACTION_PROVIDER or project default)."
        ),
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=15,
        help="Maximum candidate pages per ingest.",
    )

    ns = parser.parse_args(argv)

    folder = ns.folder.expanduser().resolve()
    if not folder.is_dir():
        print(f"error: folder not found or not a directory: {folder}", file=sys.stderr)
        return 2

    database_path_str = str(ns.database.expanduser().resolve())
    settings = ExtractionSettings.from_env()
    provider_key = ns.provider if ns.provider is not None else settings.provider_name

    pdfs = _pdf_paths(folder, recursive=ns.recursive)
    if not pdfs:
        print(f"No PDF files under {folder} (recursive={ns.recursive}).")
        return 0

    max_workers = max(1, int(ns.workers))
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s [%(processName)s] %(message)s",
    )

    print(
        f"Queueing {len(pdfs)} PDF(s) · workers={max_workers} · DB={database_path_str} · "
        f"provider={provider_key!r}",
        flush=True,
    )

    payloads: list[dict[str, Any]] = [
        {
            "pdf": str(path.resolve()),
            "database": database_path_str,
            "provider": provider_key,
            "candidate_limit": int(ns.candidate_limit),
            "metadata": {},
        }
        for path in pdfs
    ]

    ok_count = 0
    failures: list[dict[str, Any]] = []
    pending_payloads = payloads

    while pending_payloads:
        manual_requests: list[tuple[dict[str, Any], dict[str, Any]]] = []

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_payloads = {
                executor.submit(_process_one_pdf, payload): payload for payload in pending_payloads
            }

            for future in as_completed(future_payloads):
                payload = future_payloads[future]
                try:
                    result = future.result()
                except Exception:  # noqa: BLE001
                    print("Worker crashed:", file=sys.stderr)
                    traceback.print_exc()
                    failures.append(
                        {
                            "pdf": payload.get("pdf", "?"),
                            "document_id": None,
                            "ok": False,
                            "phase": "worker",
                            "error": "Worker crashed",
                        }
                    )
                    continue

                pdf = result.get("pdf", "?")
                if result.get("ok"):
                    doc_id = result.get("document_id", "")
                    print(f"OK  {pdf}  document_id={doc_id}", flush=True)
                    ok_count += 1
                elif result.get("manual_input_required"):
                    print(f"WAIT {pdf}  queued for manual metadata input", flush=True)
                    manual_requests.append((payload, result))
                else:
                    phase = result.get("phase", "?")
                    err = result.get("error", "?")
                    print(f"ERR {pdf}  phase={phase}  {err}", flush=True)
                    failures.append(result)

        if not manual_requests:
            break

        retry_payloads: list[dict[str, Any]] = []
        for payload, result in manual_requests:
            try:
                metadata = _prompt_for_manual_metadata(result)
            except EOFError:
                failures.append(
                    {
                        "pdf": result.get("pdf", "?"),
                        "document_id": None,
                        "ok": False,
                        "phase": "metadata",
                        "error": "Console input ended before required metadata was provided.",
                    }
                )
                continue
            retry_payloads.append(_with_manual_metadata(payload, metadata))

        pending_payloads = retry_payloads

    print(f"Done · success={ok_count}/{len(pdfs)} · failed={len(failures)}", flush=True)

    return 0 if len(failures) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
