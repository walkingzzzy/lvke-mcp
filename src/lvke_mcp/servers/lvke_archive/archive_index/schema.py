"""SQLite schema for the archive index.

This is the canonical source of truth for table structure consumed by
``mcp_servers/lvke_archive/storage.py`` after the v1.1 rewrite.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


DDL = """
CREATE TABLE IF NOT EXISTS reports (
    report_id      TEXT PRIMARY KEY,
    title          TEXT,
    corpus_origin  TEXT,
    industry       TEXT,
    project_type   TEXT,
    report_type    TEXT,
    year           INTEGER,
    region         TEXT,
    scale_bucket   TEXT,
    source_path    TEXT,
    char_len       INTEGER,
    quality_flag   TEXT,
    brief          TEXT,
    indexed_at     TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id           TEXT PRIMARY KEY,
    report_id          TEXT,
    chapter_no         INTEGER,
    chapter_title      TEXT,
    level              INTEGER,
    content            TEXT,
    content_redacted   TEXT,
    char_len           INTEGER,
    parent_chunk_id    TEXT,
    FOREIGN KEY(report_id) REFERENCES reports(report_id)
);

CREATE TABLE IF NOT EXISTS indicators (
    report_id              TEXT PRIMARY KEY,
    total_investment       REAL,
    construction_invest    REAL,
    working_capital        REAL,
    equity_ratio           REAL,
    project_irr            REAL,
    capital_irr            REAL,
    payback_years          REAL,
    scale_metric           TEXT,
    confidence             REAL,
    FOREIGN KEY(report_id) REFERENCES reports(report_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_chapter ON chunks(report_id, chapter_no);
CREATE INDEX IF NOT EXISTS idx_chunks_chapter_only ON chunks(chapter_no);
CREATE INDEX IF NOT EXISTS idx_reports_industry ON reports(industry, corpus_origin);
CREATE INDEX IF NOT EXISTS idx_reports_year ON reports(year);
CREATE INDEX IF NOT EXISTS idx_reports_corpus ON reports(corpus_origin);
"""


def open_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the metadata sqlite database with schema applied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    conn.row_factory = sqlite3.Row
    return conn
