"""Unit tests for Phase P6: Embeddings and Hybrid Search (Section 13)."""

from __future__ import annotations

from pathlib import Path
import pytest

from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.extract import extract_sync
from filegraph.ingest import ingest
from filegraph.search import hybrid_search
from tests.make_fixtures import create_fixture_tree


@pytest.fixture
def search_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FG_FAKE_LLM", "1")
    monkeypatch.setenv("FG_EMBED_PROVIDER", "none")

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
    return settings


def test_fts_search_passport_expiry(search_env):
    """With embeddings disabled, FTS-only search returns the passport for 'passport expiry'."""
    conn = connect(search_env.db_path)
    results = hybrid_search(conn, "passport expiry", settings=search_env)
    conn.close()

    assert len(results) > 0
    # Passport file Scan_0042.pdf should be in the results
    paths = [r["path"] for r in results]
    assert any("Scan_0042.pdf" in p or "passport" in p.lower() for p in paths)
    assert "bm25" in results[0]["matched_by"]


def test_search_filters(search_env):
    """Filters by kind, root, and card_type work."""
    conn = connect(search_env.db_path)

    # Filter by card_type
    res_legal = hybrid_search(conn, "agreement", card_type="legal", settings=search_env)
    for r in res_legal:
        assert r["card_type"] == "legal"

    # Filter by kind
    res_code = hybrid_search(conn, "def", kind="code", settings=search_env)
    for r in res_code:
        assert r["kind"] == "code"

    conn.close()
