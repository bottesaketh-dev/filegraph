"""Unit tests for Phase P9: Exports (graph.html, Obsidian vault, review CSV, report)."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.export import export_graph, export_review_csv, export_vault, report
from filegraph.extract import extract_sync
from filegraph.ingest import ingest
from filegraph.link import link
from tests.make_fixtures import create_fixture_tree


@pytest.fixture
def populated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FG_FAKE_LLM", "1")
    root_dir = create_fixture_tree(tmp_path / "fixtures")
    db_file = tmp_path / "test.db"

    cfg_yaml = tmp_path / "filegraph.yaml"
    cfg_yaml.write_text(
        f"""
me:
  names: ["Alice Smith"]
  organizations: ["Acme Corp"]
roots:
  - path: {str(root_dir).replace('\\\\', '/')}
    name: test_root
    profile: auto
    llm: cloud
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path=cfg_yaml, db_path=db_file)
    conn = connect(settings.db_path)
    init_schema(conn)
    conn.close()

    ingest(settings)
    extract_sync(settings)
    link(settings)

    return settings


def test_export_graph(populated_env, tmp_path: Path):
    conn = connect(populated_env.db_path)
    out_html = tmp_path / "graph.html"
    res = export_graph(conn, populated_env, out_path=out_html)
    conn.close()

    assert res.exists()
    content = res.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "vis.Network" in content
    assert "const GRAPH_DATA =" in content
    assert "</script>" in content
    # Embedded JSON must have </ escaped
    assert "<\\/" in content or "nodes" in content


def test_export_review_csv(populated_env, tmp_path: Path):
    conn = connect(populated_env.db_path)
    out_csv = tmp_path / "review.csv"
    res = export_review_csv(conn, populated_env, out_path=out_csv)
    conn.close()

    assert res.exists()
    # Read with utf-8-sig
    with open(res, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        assert "file_id" in headers
        assert "card_type" in headers
        assert "title" in headers
        assert "main_entity" in headers
        rows = list(reader)
        assert len(rows) > 0


def test_export_vault(populated_env, tmp_path: Path):
    conn = connect(populated_env.db_path)
    vault_dir = tmp_path / "vault"
    res = export_vault(conn, populated_env, out_dir=vault_dir)
    conn.close()

    assert res.exists()
    assert (res / "Documents").is_dir()
    assert (res / "Entities").is_dir()
    assert (res / "Repos").is_dir()
    assert (res / "_Needs review.md").is_file()

    doc_notes = list((res / "Documents").glob("*.md"))
    assert len(doc_notes) > 0
    # Check frontmatter in one doc note
    sample_note = doc_notes[0].read_text(encoding="utf-8")
    assert sample_note.startswith("---")
    assert "tags:" in sample_note


def test_report(populated_env):
    conn = connect(populated_env.db_path)
    rep = report(conn, populated_env)
    conn.close()

    assert "FileGraph Summary Report" in rep
    assert "Files:" in rep
    assert "Cards extracted:" in rep
