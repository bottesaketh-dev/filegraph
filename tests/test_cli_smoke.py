"""End-to-end CLI smoke test running full pipeline and verifying Section 6.3 queries (Section 25.3)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.watch import run_full_pipeline
from tests.make_fixtures import create_fixture_tree


@pytest.fixture(scope="module")
def smoke_tree(tmp_path_factory):
    """Create complete fixture tree and configuration for full smoke test."""
    base = tmp_path_factory.mktemp("fg_smoke_tree")
    create_fixture_tree(base)

    yaml_path = base / "filegraph.yaml"
    db_path = base / "filegraph.db"
    cards_dir = Path(__file__).resolve().parent.parent / "cards"

    config_content = f"""\
db: "{db_path.as_posix()}"
cards_dir: "{cards_dir.as_posix()}"

me:
  names: [Ravi Sharma]
  organizations: [Harborline Events LLC]

limits:
  max_file_mb: 1
  confirm_tokens_over: 500000

roots:
  - name: personal
    path: "{(base / 'personal').as_posix()}"
    profile: mixed
    llm: cloud

  - name: legal
    path: "{(base / 'legal').as_posix()}"
    profile: auto
    llm: cloud

  - name: code
    path: "{(base / 'code').as_posix()}"
    profile: auto
    llm: cloud
"""
    yaml_path.write_text(config_content, encoding="utf-8")
    os.environ["FG_FAKE_LLM"] = "1"

    settings = load_settings(config_path=yaml_path, db_path=db_path)
    conn = connect(settings.db_path)
    init_schema(conn)
    conn.close()

    return settings, base


def test_cli_smoke_pipeline_and_section_6_3_queries(smoke_tree):
    """Run full pipeline and execute all Section 6.3 queries to verify catalog data."""
    settings, base = smoke_tree

    # Run full pipeline: ingest → extract → repos summarize → link → embed → vault → graph → review-csv
    stats = run_full_pipeline(settings, yes=True)
    assert stats["ingest"]["new"] > 0
    assert stats["extract"]["extracted"] > 0

    conn = connect(settings.db_path)
    try:
        # Query 1: Everything that expires before 2028, across all card types
        # Note: policy_2026.pdf expires 2027-03-31
        q1 = """
            SELECT c.title, d.date, c.path
            FROM doc_dates d
            JOIN catalog c USING (file_id)
            WHERE d.label = 'expires'
              AND d.date < '2028-01-01'
              AND c.duplicate_of IS NULL
            ORDER BY d.date
        """
        rows_q1 = conn.execute(q1).fetchall()
        assert len(rows_q1) >= 1
        assert any("policy" in r["path"].lower() or "car" in r["path"].lower() for r in rows_q1)

        # Query 2: Contracts effective in June 2026 (legal card)
        # Note: Acme Amendment 1 is effective June 1, 2026
        q2 = """
            SELECT title, main_date, path
            FROM catalog
            WHERE card_type = 'legal'
              AND main_date_label = 'effective'
              AND substr(main_date, 1, 7) = '2026-06'
              AND c.duplicate_of IS NULL
        """
        # (c.duplicate_of alias can be checked via catalog view duplicate_of)
        q2_clean = """
            SELECT title, main_date, path
            FROM catalog
            WHERE card_type = 'legal'
              AND main_date_label = 'effective'
              AND substr(main_date, 1, 7) = '2026-06'
              AND duplicate_of IS NULL
        """
        rows_q2 = conn.execute(q2_clean).fetchall()
        assert len(rows_q2) >= 1
        assert any("amendment" in r["title"].lower() or "amendment" in r["path"].lower() for r in rows_q2)

        # Query 3: Insurance premiums over 10,000
        # Note: ICICI Lombard premium is 14,200
        q3 = """
            SELECT c.title, f.value_num, f.unit
            FROM doc_facts f
            JOIN catalog c USING (file_id)
            WHERE f.key = 'premium'
              AND f.value_num > 10000
        """
        rows_q3 = conn.execute(q3).fetchall()
        assert len(rows_q3) >= 1
        assert any(r["value_num"] == 14200.0 for r in rows_q3)

        # Query 4: Legal card presence & repos
        q4 = "SELECT count(*) as n FROM catalog WHERE card_type = 'legal'"
        n_legal = conn.execute(q4).fetchone()["n"]
        assert n_legal >= 2

        # Verify exports exist
        assert (settings.db_path.parent / "graph.html").exists() or Path("graph.html").exists()
        assert (settings.db_path.parent / "vault").is_dir() or Path("vault").is_dir()

        # Verify run_log
        run_logs = conn.execute("SELECT * FROM run_log WHERE command = 'run'").fetchall()
        assert len(run_logs) >= 1
    finally:
        conn.close()
