"""Test filesystem watching and pipeline refresh (Section 18.1 & Phase P12)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.watch import run_pipeline_cycle, watch_roots
from tests.make_fixtures import create_fixture_tree


def test_watch_cycle_detects_new_file(tmp_path: Path):
    """Test that a new file added to a root appears in catalog after one cycle."""
    base = tmp_path / "fixtures"
    create_fixture_tree(base)

    yaml_path = tmp_path / "filegraph.yaml"
    db_path = tmp_path / "filegraph.db"
    cards_dir = Path(__file__).resolve().parent.parent / "cards"

    config_content = f"""\
db: "{db_path.as_posix()}"
cards_dir: "{cards_dir.as_posix()}"

me:
  names: [Ravi Sharma]
  organizations: [Harborline Events LLC]

roots:
  - name: personal
    path: "{(base / 'personal').as_posix()}"
    profile: mixed
    llm: cloud
"""
    yaml_path.write_text(config_content, encoding="utf-8")
    os.environ["FG_FAKE_LLM"] = "1"

    settings = load_settings(config_path=yaml_path, db_path=db_path)
    conn = connect(settings.db_path)
    init_schema(conn)
    conn.close()

    # Initial cycle
    watch_roots(settings, once=True)

    conn = connect(settings.db_path)
    before_count = conn.execute("SELECT count(*) as n FROM catalog").fetchone()["n"]
    conn.close()

    # Add a new file to personal root
    new_doc = base / "personal" / "notes_2026.md"
    new_doc.write_text("# Personal Notes 2026\n\nHealth checkup appointment notes.", encoding="utf-8")

    # Second cycle
    watch_roots(settings, once=True)

    conn = connect(settings.db_path)
    try:
        after_count = conn.execute("SELECT count(*) as n FROM catalog").fetchone()["n"]
        assert after_count > before_count

        found = conn.execute("SELECT * FROM catalog WHERE filename = 'notes_2026.md'").fetchone()
        assert found is not None
        assert found["card_type"] is not None
    finally:
        conn.close()
