from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = 6
DEFAULT_DATABASE_PATH = Path("data/revenue_segment_extractor.sqlite3")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    document_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    fiscal_period TEXT,
    status TEXT NOT NULL,
    reported_total TEXT,
    currency TEXT,
    scale TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    analysis_notes TEXT
);

CREATE TABLE IF NOT EXISTS document_queue_jobs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    model TEXT,
    worker_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_document_queue_jobs_status_created
    ON document_queue_jobs(status, created_at, id);

CREATE TABLE IF NOT EXISTS parsed_pages (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    blocks_json TEXT NOT NULL,
    tables_json TEXT NOT NULL,
    language TEXT,
    parser_sources TEXT NOT NULL,
    has_text INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, page_number),
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS page_candidates (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    relevance_score REAL NOT NULL,
    matched_signals_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS segment_rows (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    segment_name TEXT NOT NULL,
    revenue_raw TEXT,
    revenue_value TEXT,
    currency TEXT,
    scale TEXT,
    period_label TEXT,
    normalized_value TEXT,
    page_ref TEXT,
    section_ref TEXT,
    metric_basis TEXT,
    confidence REAL,
    status TEXT NOT NULL,
    extraction_method TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_type TEXT,
    segment_type TEXT,
    segment_name_original TEXT,
    segment_name_normalized TEXT,
    language TEXT,
    needs_review INTEGER,
    classification_rationale TEXT,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS segment_evidence (
    id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    snippet_text TEXT NOT NULL,
    bbox_json TEXT,
    parser_source TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    evidence_original TEXT,
    evidence_translation TEXT,
    language TEXT,
    FOREIGN KEY(segment_id) REFERENCES segment_rows(id) ON DELETE CASCADE,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS validation_issues (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    segment_id TEXT,
    severity TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY(segment_id) REFERENCES segment_rows(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS validation_issue_reviews (
    issue_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(issue_id) REFERENCES validation_issues(id) ON DELETE CASCADE,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS nace_candidates (
    id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL,
    nace_code TEXT NOT NULL,
    nace_label TEXT NOT NULL,
    nace_level INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    match_score REAL NOT NULL,
    rationale TEXT,
    FOREIGN KEY(segment_id) REFERENCES segment_rows(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS segment_nace_selections (
    segment_id TEXT PRIMARY KEY,
    nace_code TEXT NOT NULL,
    nace_label TEXT NOT NULL,
    nace_level INTEGER NOT NULL,
    match_score REAL,
    rationale TEXT,
    source TEXT NOT NULL,
    reviewer TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(segment_id) REFERENCES segment_rows(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS esg_factors (
    id TEXT PRIMARY KEY,
    segment_id TEXT,
    document_id TEXT NOT NULL,
    factor_type TEXT NOT NULL,
    polarity TEXT NOT NULL,
    description TEXT NOT NULL,
    page_ref TEXT,
    evidence_text TEXT NOT NULL,
    confidence REAL,
    is_company_wide INTEGER NOT NULL,
    segment_link_type TEXT,
    esg_category TEXT,
    score_relevant INTEGER,
    impact_mechanism TEXT,
    evidence_source TEXT,
    cluster_key TEXT,
    FOREIGN KEY(segment_id) REFERENCES segment_rows(id) ON DELETE SET NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS segment_scores (
    id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL,
    base_score REAL NOT NULL,
    adjustment_score REAL NOT NULL,
    final_score REAL NOT NULL,
    weight_share REAL,
    rationale TEXT,
    FOREIGN KEY(segment_id) REFERENCES segment_rows(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_scores (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    weighted_average_score REAL NOT NULL,
    included_weight_share REAL NOT NULL,
    included_segment_count INTEGER NOT NULL,
    denominator_value TEXT,
    scale_min REAL NOT NULL,
    scale_max REAL NOT NULL,
    score_direction TEXT NOT NULL,
    rationale TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_events (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    segment_id TEXT,
    reviewer TEXT NOT NULL,
    action TEXT NOT NULL,
    field_changed TEXT,
    old_value TEXT,
    new_value TEXT,
    note TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY(segment_id) REFERENCES segment_rows(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS export_records (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    format TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
INSERT OR IGNORE INTO schema_migrations(version) VALUES (2);
INSERT OR IGNORE INTO schema_migrations(version) VALUES (3);
INSERT OR IGNORE INTO schema_migrations(version) VALUES (4);
INSERT OR IGNORE INTO schema_migrations(version) VALUES (5);
INSERT OR IGNORE INTO schema_migrations(version) VALUES (6);
"""


DROP_SQL = """
DROP TABLE IF EXISTS export_records;
DROP TABLE IF EXISTS review_events;
DROP TABLE IF EXISTS company_scores;
DROP TABLE IF EXISTS segment_scores;
DROP TABLE IF EXISTS esg_factors;
DROP TABLE IF EXISTS segment_nace_selections;
DROP TABLE IF EXISTS nace_candidates;
DROP TABLE IF EXISTS validation_issue_reviews;
DROP TABLE IF EXISTS validation_issues;
DROP TABLE IF EXISTS segment_evidence;
DROP TABLE IF EXISTS segment_rows;
DROP TABLE IF EXISTS page_candidates;
DROP TABLE IF EXISTS parsed_pages;
DROP TABLE IF EXISTS document_queue_jobs;
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS schema_migrations;
"""


def connect_database(
    path: str | Path = ":memory:",
    *,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path, check_same_thread=check_same_thread)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    _ensure_column(connection, "segment_rows", "row_type", "TEXT")
    _ensure_column(connection, "segment_rows", "segment_type", "TEXT")
    _ensure_column(connection, "segment_rows", "segment_name_original", "TEXT")
    _ensure_column(connection, "segment_rows", "segment_name_normalized", "TEXT")
    _ensure_column(connection, "segment_rows", "language", "TEXT")
    _ensure_column(connection, "segment_rows", "needs_review", "INTEGER")
    _ensure_column(connection, "segment_rows", "classification_rationale", "TEXT")
    _ensure_column(connection, "segment_evidence", "evidence_original", "TEXT")
    _ensure_column(connection, "segment_evidence", "evidence_translation", "TEXT")
    _ensure_column(connection, "segment_evidence", "language", "TEXT")
    _ensure_column(connection, "esg_factors", "segment_link_type", "TEXT")
    _ensure_column(connection, "esg_factors", "esg_category", "TEXT")
    _ensure_column(connection, "esg_factors", "score_relevant", "INTEGER")
    _ensure_column(connection, "esg_factors", "impact_mechanism", "TEXT")
    _ensure_column(connection, "esg_factors", "evidence_source", "TEXT")
    _ensure_column(connection, "esg_factors", "cluster_key", "TEXT")
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()


def reset_database(connection: sqlite3.Connection) -> None:
    connection.executescript(DROP_SQL)
    initialize_database(connection)


def initialize_database_file(path: str | Path = DEFAULT_DATABASE_PATH) -> Path:
    database_path = Path(path)
    connection = connect_database(database_path)
    try:
        initialize_database(connection)
    finally:
        connection.close()
    return database_path


def reset_database_file(path: str | Path = DEFAULT_DATABASE_PATH) -> Path:
    database_path = Path(path)
    connection = connect_database(database_path)
    try:
        reset_database(connection)
    finally:
        connection.close()
    return database_path


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )
