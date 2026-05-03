from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path


NACE_REFERENCE_ENV_VAR = "RSE_NACE_REV2_CSV_PATH"
DEFAULT_NACE_REFERENCE_PATH = Path("reference/NACE_Rev2_Outline.csv")
SUPPORTED_LEVELS = {"section", "division", "group", "class"}
REQUIRED_COLUMNS = {
    "level_depth",
    "level",
    "node_code",
    "node_name",
    "node_key",
    "parent_key",
    "section_code",
    "section_name",
    "division_code",
    "division_name",
    "group_code",
    "group_name",
    "class_code",
    "class_name",
    "hierarchy_path_codes",
    "hierarchy_path_names",
    "source_row_number",
}


@dataclass(frozen=True)
class NaceNode:
    level_depth: int
    level: str
    code: str
    label: str
    node_key: str
    parent_key: str | None
    section_code: str | None
    section_name: str | None
    division_code: str | None
    division_name: str | None
    group_code: str | None
    group_name: str | None
    class_code: str | None
    class_name: str | None
    hierarchy_path_codes: tuple[str, ...]
    hierarchy_path_names: tuple[str, ...]
    source_row_number: int


def resolve_nace_reference_path(path: str | Path | None = None) -> Path:
    configured_path = path or os.getenv(NACE_REFERENCE_ENV_VAR) or DEFAULT_NACE_REFERENCE_PATH
    return Path(configured_path)


def load_nace_nodes(path: str | Path | None = None) -> tuple[NaceNode, ...]:
    reference_path = resolve_nace_reference_path(path)
    if not reference_path.exists():
        raise FileNotFoundError(
            f"NACE Rev.2 outline CSV not found at {reference_path}. "
            f"Set {NACE_REFERENCE_ENV_VAR} or place the CSV at {DEFAULT_NACE_REFERENCE_PATH}."
        )

    rows = _read_rows(reference_path)
    header_index = _find_header_row(rows)
    header = [cell.strip() for cell in rows[header_index]]
    missing = REQUIRED_COLUMNS - set(header)
    if missing:
        raise ValueError(f"NACE outline CSV missing required columns: {sorted(missing)}")

    nodes: list[NaceNode] = []
    for offset, raw_row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(cell.strip() for cell in raw_row):
            continue
        row = _row_dict(header, raw_row)
        level = row["level"].strip().lower()
        if level not in SUPPORTED_LEVELS:
            continue
        code = row["node_code"].strip()
        label = row["node_name"].strip()
        node_key = row["node_key"].strip()
        if not code or not label or not node_key:
            continue
        nodes.append(
            NaceNode(
                level_depth=_parse_int(row["level_depth"], "level_depth", offset),
                level=level,
                code=code,
                label=label,
                node_key=node_key,
                parent_key=row["parent_key"].strip() or None,
                section_code=row["section_code"].strip() or None,
                section_name=row["section_name"].strip() or None,
                division_code=row["division_code"].strip() or None,
                division_name=row["division_name"].strip() or None,
                group_code=row["group_code"].strip() or None,
                group_name=row["group_name"].strip() or None,
                class_code=row["class_code"].strip() or None,
                class_name=row["class_name"].strip() or None,
                hierarchy_path_codes=_split_path(row["hierarchy_path_codes"]),
                hierarchy_path_names=_split_path(row["hierarchy_path_names"]),
                source_row_number=_parse_int(
                    row["source_row_number"], "source_row_number", offset
                ),
            )
        )
    if not nodes:
        raise ValueError("NACE outline CSV did not contain supported hierarchy rows")
    return tuple(nodes)


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.reader(file))


def _find_header_row(rows: list[list[str]]) -> int:
    required_header_cells = {"level_depth", "level", "node_code", "node_name"}
    for index, row in enumerate(rows):
        cells = {cell.strip() for cell in row}
        if required_header_cells.issubset(cells):
            return index
    raise ValueError(
        "NACE outline CSV header row not found; expected columns include "
        "level_depth, level, node_code, and node_name."
    )


def _row_dict(header: list[str], row: list[str]) -> dict[str, str]:
    padded = row + [""] * max(0, len(header) - len(row))
    return dict(zip(header, padded, strict=False))


def _parse_int(value: str, column: str, row_number: int) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {column} at CSV row {row_number}: {value!r}") from exc


def _split_path(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split("|") if part.strip())
