"""Acceptance tests for Phase P2 (Ingestion, inventory, walk, security, parsing)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.ingest import ingest, inventory
from tests.make_fixtures import create_fixture_tree


@pytest.fixture
def fixture_env(tmp_path: Path):
    """Set up a fresh fixture tree and temporary database."""
    tree_dir = create_fixture_tree(tmp_path / "_tree")
    cfg_path = tree_dir / "filegraph.yaml"
    db_path = tmp_path / "test_filegraph.db"

    # Load settings with explicit paths
    settings = load_settings(config_path=cfg_path, db_path=db_path)

    # Initialize schema
    conn = connect(db_path)
    init_schema(conn)
    conn.close()

    return settings, tree_dir, db_path


def test_phase_2_acceptance_checks(fixture_env):
    """Verify all acceptance criteria for Phase P2 (Section 22).

    Acceptance:
    - node_modules/ and .git/ ignored
    - .env and id_rsa denied (counted, not stored)
    - fake secret in config.py fixture shows [REDACTED] in pages
    - email attachment stored as mail.eml::contract.pdf (or signed.eml::contract.pdf)
    - duplicate detected
    - large file skipped_large
    - second ingest reports 0 new/changed
    - deleting a file marks it missing
    """
    settings, tree_dir, db_path = fixture_env

    # 1. First ingest run
    stats1 = ingest(settings, workers=1)

    assert stats1["new"] > 0
    assert stats1["denied"] >= 2  # .env and id_rsa counted
    assert stats1["skipped_large"] >= 1  # big.bin > 1MB
    assert stats1["attachments"] >= 1  # contract.pdf

    conn = connect(db_path)

    # Acceptance check: node_modules/ and .git/ ignored
    rows = conn.execute("SELECT rel_path FROM files WHERE missing = 0").fetchall()
    all_rel_paths = [r["rel_path"] for r in rows]

    for p in all_rel_paths:
        assert "node_modules" not in p, f"node_modules should be ignored: {p}"
        assert not p.startswith(".git/"), f".git should be ignored: {p}"
        assert "/.git/" not in p, f".git subdirs should be ignored: {p}"

    # Acceptance check: .env and id_rsa denied (counted, not stored)
    for p in all_rel_paths:
        fname = Path(p).name
        assert fname != ".env", ".env must not be stored in files"
        assert not fname.startswith("id_rsa"), "id_rsa must not be stored in files"

    # Acceptance check: large file skipped_large
    large_row = conn.execute(
        "SELECT size, parse_status, tier, sha256 FROM files WHERE filename = 'big.bin'"
    ).fetchone()
    assert large_row is not None
    assert large_row["parse_status"] == "skipped_large"
    assert large_row["tier"] == 0
    assert large_row["sha256"] is None

    # Acceptance check: fake secret in config.py fixture shows [REDACTED] in pages
    cfg_file = conn.execute(
        "SELECT id FROM files WHERE filename = 'config.py' AND missing = 0"
    ).fetchone()
    assert cfg_file is not None
    page_rows = conn.execute(
        "SELECT text FROM pages WHERE file_id = ?", (cfg_file["id"],)
    ).fetchall()
    combined_pages = " ".join(r["text"] for r in page_rows)
    assert "[REDACTED]" in combined_pages
    assert "sk-test-0123456789abcdefghij" not in combined_pages

    # Acceptance check: email attachment stored as signed.eml::contract.pdf
    att_row = conn.execute(
        "SELECT rel_path, source, parent_id FROM files WHERE filename = 'contract.pdf'"
    ).fetchone()
    assert att_row is not None
    assert att_row["source"] == "attachment"
    assert "::contract.pdf" in att_row["rel_path"]
    assert att_row["parent_id"] is not None

    # Acceptance check: duplicate detected
    # Acme MSA 2023.docx and Acme MSA 2023 copy.docx share the same sha256
    msa_row = conn.execute(
        "SELECT id, sha256 FROM files WHERE filename = 'Acme MSA 2023.docx'"
    ).fetchone()
    copy_row = conn.execute(
        "SELECT id, sha256 FROM files WHERE filename = 'Acme MSA 2023 copy.docx'"
    ).fetchone()
    assert msa_row is not None and copy_row is not None
    assert msa_row["sha256"] == copy_row["sha256"]

    # Verify duplicate is detected
    dup_row = conn.execute(
        """
        SELECT f.id, (SELECT MIN(f2.id) FROM files f2 WHERE f2.sha256 = f.sha256 AND f2.id < f.id AND f2.missing = 0) as duplicate_of
        FROM files f WHERE f.id = ?
        """,
        (max(msa_row["id"], copy_row["id"]),),
    ).fetchone()
    assert dup_row["duplicate_of"] == min(msa_row["id"], copy_row["id"])

    # 2. Acceptance check: second ingest reports 0 new/changed
    stats2 = ingest(settings, workers=1)
    assert stats2["new"] == 0
    assert stats2["changed"] == 0
    assert stats2["skipped_unchanged"] > 0

    # 3. Acceptance check: deleting a file marks it missing
    # Delete budget.csv on disk
    budget_path = tree_dir / "personal" / "budget.csv"
    assert budget_path.is_file()
    budget_path.unlink()

    stats3 = ingest(settings, workers=1)
    assert stats3["missing"] >= 1

    budget_db = conn.execute(
        "SELECT missing FROM files WHERE filename = 'budget.csv'"
    ).fetchone()
    assert budget_db is not None
    assert budget_db["missing"] == 1

    # Check inventory runs cleanly
    inv = inventory(settings)
    assert inv["totals"]["files"] > 0
    assert inv["totals"]["attachments"] >= 1
    assert inv["totals"]["duplicates"] >= 1
    assert inv["totals"]["large_skipped"] >= 1
    assert inv["estimate"]["pending_files"] > 0

    conn.close()


def test_cli_ingest_and_inventory(fixture_env):
    """Verify CLI commands `fg ingest` and `fg inventory` run without errors."""
    settings, tree_dir, db_path = fixture_env

    # Run fg ingest
    res_ingest = subprocess.run(
        [
            sys.executable,
            "fg.py",
            "--config",
            str(tree_dir / "filegraph.yaml"),
            "--db",
            str(db_path),
            "ingest",
        ],
        capture_output=True,
        text=True,
    )
    assert res_ingest.returncode == 0, f"fg ingest failed: {res_ingest.stderr}"
    assert "Ingest complete" in res_ingest.stdout
    assert "FileGraph Inventory" in res_ingest.stdout

    # Run fg inventory
    res_inv = subprocess.run(
        [
            sys.executable,
            "fg.py",
            "--config",
            str(tree_dir / "filegraph.yaml"),
            "--db",
            str(db_path),
            "inventory",
        ],
        capture_output=True,
        text=True,
    )
    assert res_inv.returncode == 0, f"fg inventory failed: {res_inv.stderr}"
    assert "FileGraph Inventory" in res_inv.stdout
    assert "Extraction Estimate" in res_inv.stdout
