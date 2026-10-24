"""Database schema, connection helpers, and utilities (Section 6)."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """\
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS roots (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  name TEXT UNIQUE NOT NULL,
  profile TEXT NOT NULL DEFAULT 'auto',
  llm_policy TEXT NOT NULL DEFAULT 'cloud',
  added_at TEXT
);

CREATE TABLE IF NOT EXISTS repos (
  id INTEGER PRIMARY KEY,
  root_id INTEGER NOT NULL,
  rel_path TEXT NOT NULL,
  name TEXT,
  remote_url TEXT, branch TEXT, head_commit TEXT, last_commit_at TEXT,
  languages TEXT DEFAULT '{}',
  summary TEXT,
  summary_json TEXT,
  summary_input_sha TEXT,
  missing INTEGER DEFAULT 0,
  UNIQUE(root_id, rel_path)
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  root_id INTEGER NOT NULL,
  rel_path TEXT NOT NULL,
  folder TEXT NOT NULL,
  filename TEXT NOT NULL,
  ext TEXT,
  kind TEXT,
  size INTEGER, mtime REAL, sha256 TEXT,
  source TEXT DEFAULT 'disk',
  cache_path TEXT,
  parent_id INTEGER,
  repo_id INTEGER,
  language TEXT,
  tier INTEGER DEFAULT 0,
  page_count INTEGER DEFAULT 0,
  is_scanned INTEGER DEFAULT 0,
  scan_pages TEXT DEFAULT '[]',
  text_chars INTEGER DEFAULT 0,
  flags TEXT DEFAULT '{}',
  email_meta TEXT,
  parse_status TEXT,
  parse_error TEXT,
  missing INTEGER DEFAULT 0,
  ingested_at TEXT,
  UNIQUE(root_id, rel_path)
);
CREATE INDEX IF NOT EXISTS ix_files_sha ON files(sha256);
CREATE INDEX IF NOT EXISTS ix_files_folder ON files(root_id, folder);
CREATE INDEX IF NOT EXISTS ix_files_parent ON files(parent_id);
CREATE INDEX IF NOT EXISTS ix_files_repo ON files(repo_id);

CREATE TABLE IF NOT EXISTS pages (
  file_id INTEGER NOT NULL,
  page INTEGER NOT NULL,
  label TEXT,
  start_line INTEGER, end_line INTEGER,
  text TEXT,
  PRIMARY KEY (file_id, page)
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  file_id UNINDEXED, page UNINDEXED, path, label, text, tokenize='porter unicode61');

CREATE TABLE IF NOT EXISTS embeddings (
  file_id INTEGER NOT NULL,
  page INTEGER NOT NULL,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL,
  PRIMARY KEY (file_id, page, model)
);

CREATE TABLE IF NOT EXISTS classification_cache (
  sha256 TEXT NOT NULL, registry_hash TEXT NOT NULL,
  card_type TEXT, suggested_type TEXT, confidence TEXT, model TEXT, created_at TEXT,
  PRIMARY KEY (sha256, registry_hash)
);

CREATE TABLE IF NOT EXISTS extraction_cache (
  sha256 TEXT NOT NULL, card_type TEXT NOT NULL, card_version INTEGER NOT NULL,
  model TEXT, data TEXT, input_tokens INTEGER, output_tokens INTEGER, created_at TEXT,
  PRIMARY KEY (sha256, card_type, card_version)
);
CREATE TABLE IF NOT EXISTS extraction_errors (file_id INTEGER, stage TEXT, error TEXT, at TEXT);

CREATE TABLE IF NOT EXISTS documents (
  file_id INTEGER PRIMARY KEY,
  card_type TEXT NOT NULL,
  card_version INTEGER,
  category TEXT,
  title TEXT,
  summary TEXT,
  status TEXT,
  main_entity_name TEXT,
  main_entity_id INTEGER,
  main_date TEXT,
  main_date_label TEXT,
  confidence TEXT,
  needs_review INTEGER DEFAULT 0,
  review_reasons TEXT DEFAULT '[]',
  suggested_type TEXT,
  extras TEXT DEFAULT '{}',
  refs TEXT DEFAULT '[]',
  model TEXT,
  extracted_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_docs_type ON documents(card_type);
CREATE INDEX IF NOT EXISTS ix_docs_main_date ON documents(main_date);
CREATE INDEX IF NOT EXISTS ix_docs_entity ON documents(main_entity_id);

CREATE TABLE IF NOT EXISTS doc_dates (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  label_detail TEXT,
  date TEXT NOT NULL,
  basis TEXT,
  quote TEXT, page INTEGER
);
CREATE INDEX IF NOT EXISTS ix_dates_label ON doc_dates(label, date);
CREATE INDEX IF NOT EXISTS ix_dates_file ON doc_dates(file_id);

CREATE TABLE IF NOT EXISTS doc_facts (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  value_text TEXT,
  value_num REAL,
  unit TEXT,
  quote TEXT, page INTEGER
);
CREATE INDEX IF NOT EXISTS ix_facts_key ON doc_facts(key);
CREATE INDEX IF NOT EXISTS ix_facts_file ON doc_facts(file_id);

CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  norm_key TEXT NOT NULL,
  aliases TEXT DEFAULT '[]',
  is_self INTEGER DEFAULT 0,
  UNIQUE(kind, norm_key)
);

CREATE TABLE IF NOT EXISTS doc_entities (
  file_id INTEGER NOT NULL,
  entity_id INTEGER NOT NULL,
  role TEXT NOT NULL DEFAULT 'mentioned',
  raw_name TEXT,
  PRIMARY KEY (file_id, entity_id, role)
);
CREATE INDEX IF NOT EXISTS ix_de_entity ON doc_entities(entity_id);

CREATE TABLE IF NOT EXISTS code_symbols (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL, repo_id INTEGER,
  name TEXT NOT NULL, qualname TEXT, kind TEXT,
  start_line INTEGER, end_line INTEGER, signature TEXT, docstring TEXT
);
CREATE INDEX IF NOT EXISTS ix_sym_name ON code_symbols(name);
CREATE INDEX IF NOT EXISTS ix_sym_file ON code_symbols(file_id);

CREATE TABLE IF NOT EXISTS code_imports (
  file_id INTEGER NOT NULL, module TEXT NOT NULL, resolved_file_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_imp_file ON code_imports(file_id);

CREATE TABLE IF NOT EXISTS edges (
  id INTEGER PRIMARY KEY,
  src_id INTEGER NOT NULL,
  dst_id INTEGER,
  edge_type TEXT NOT NULL,
  method TEXT NOT NULL,
  confidence REAL, evidence TEXT, page INTEGER,
  ref TEXT
);
CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges(dst_id);

CREATE TABLE IF NOT EXISTS filing_log (
  id INTEGER PRIMARY KEY, at TEXT, source_path TEXT, root_name TEXT, suggested_folder TEXT,
  is_new_folder INTEGER, confidence REAL, alternatives TEXT, reasoning TEXT, action TEXT, dest_path TEXT);

CREATE TABLE IF NOT EXISTS run_log (
  id INTEGER PRIMARY KEY, at TEXT, command TEXT, stats TEXT);
"""

CATALOG_VIEW = """\
DROP VIEW IF EXISTS catalog;
CREATE VIEW catalog AS
SELECT f.id AS file_id, r.name AS root, f.rel_path AS path, f.folder, f.filename, f.ext, f.kind,
       f.source, f.parent_id, f.repo_id, f.language,
       d.card_type, d.category, d.title, d.summary, d.status,
       d.main_entity_name, d.main_entity_id, e.name AS main_entity,
       d.main_date, d.main_date_label, d.confidence, d.needs_review, d.review_reasons,
       d.suggested_type, d.extras,
       (SELECT MIN(f2.id) FROM files f2 WHERE f2.sha256 = f.sha256 AND f2.id < f.id AND f2.missing = 0) AS duplicate_of
FROM files f
JOIN roots r ON r.id = f.root_id
JOIN documents d ON d.file_id = f.id
LEFT JOIN entities e ON e.id = d.main_entity_id
WHERE f.missing = 0;
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now() -> str:
    """UTC ISO-format timestamp with seconds precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with WAL mode and foreign keys.

    *readonly* opens via the ``file:`` URI with ``?mode=ro``.
    """
    if path is None:
        path = Path("./filegraph.db")
    path = Path(path).resolve()

    if readonly:
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables / indexes and write schema version to meta."""
    conn.executescript(SCHEMA)
    conn.executescript(CATALOG_VIEW)
    set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()


def check_schema_version(conn: sqlite3.Connection) -> None:
    """Verify that the schema version matches; exit with a clear message if not."""
    stored = get_meta(conn, "schema_version")
    if stored is not None and int(stored) != SCHEMA_VERSION:
        print(
            f"Database schema version mismatch: expected {SCHEMA_VERSION}, "
            f"found {stored}. Please rebuild with a new --db path.",
            file=sys.stderr,
        )
        sys.exit(2)


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Read a value from the meta table."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Write a value to the meta table."""
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
    )


def root_path(conn: sqlite3.Connection, root_id: int) -> Path:
    """Return the absolute Path for a root id."""
    row = conn.execute("SELECT path FROM roots WHERE id = ?", (root_id,)).fetchone()
    if row is None:
        raise ValueError(f"Root id {root_id} not found")
    return Path(row["path"])


def abs_path(conn: sqlite3.Connection, frow: sqlite3.Row) -> Path:
    """Return the absolute path of a file row.

    Attachments use ``cache_path``; disk files use root path + rel_path.
    """
    if frow["source"] == "attachment" and frow["cache_path"]:
        return Path(frow["cache_path"])
    rp = root_path(conn, frow["root_id"])
    return rp / frow["rel_path"].replace("/", os.sep)


def page_texts(conn: sqlite3.Connection, file_id: int) -> list[tuple[int, str]]:
    """Return ``[(page, text)]`` for a file."""
    rows = conn.execute(
        "SELECT page, text FROM pages WHERE file_id = ? ORDER BY page",
        (file_id,),
    ).fetchall()
    return [(r["page"], r["text"] or "") for r in rows]


def jloads(s: str | None, default: Any = None) -> Any:
    """Safe ``json.loads`` with a fallback default."""
    if not s:
        return default
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return default


def ensure_fts5(conn: sqlite3.Connection | None = None) -> None:
    """Check that FTS5 is available.  Exit with a clear message if not."""
    test_conn = conn or sqlite3.connect(":memory:")
    try:
        test_conn.execute(
            "CREATE VIRTUAL TABLE _fts5_test USING fts5(x, tokenize='porter unicode61')"
        )
        test_conn.execute("DROP TABLE _fts5_test")
    except sqlite3.OperationalError:
        print(
            "Your Python's SQLite lacks FTS5. Install Python from python.org "
            "or use a build with FTS5.",
            file=sys.stderr,
        )
        sys.exit(2)
    finally:
        if conn is None:
            test_conn.close()


# needed by abs_path
import os  # noqa: E402
