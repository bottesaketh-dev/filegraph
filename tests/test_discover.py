"""Unit tests for Phase P8: Card Discovery and Proposal (Section 16)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from filegraph.cards import load_card_file, load_registry
from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.discover import discover, list_cards, propose_card
from filegraph.extract import extract_sync
from filegraph.ingest import ingest
from tests.make_fixtures import create_fixture_tree


@pytest.fixture
def discover_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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

    return settings, tmp_path


def test_discover_lists_suggested_types(discover_env):
    """fg discover lists frequent suggested_type among generic documents."""
    settings, _ = discover_env
    conn = connect(settings.db_path)
    res = discover(conn, settings, min_count=1)
    conn.close()

    assert len(res) > 0
    types = [r["suggested_type"] for r in res]
    assert "insurance_policy" in types


def test_propose_and_adopt_new_card(discover_env):
    """fg propose-card writes valid YAML, and re-extraction updates the card type."""
    settings, tmp_path = discover_env
    conn = connect(settings.db_path)

    # 1. Propose card
    proposed_yaml = propose_card(conn, settings, suggested_type="insurance_policy", samples=5)
    assert proposed_yaml.exists()

    # Verify YAML is valid
    card = load_card_file(proposed_yaml)
    assert card.name == "insurance_policy"
    assert "insurance_policy" in card.absorbs

    # 2. Move to cards/ directory and verify registry
    cards_dir = Path(settings.cards_dir)
    target_yaml = cards_dir / "insurance_policy.yaml"
    shutil.copy2(proposed_yaml, target_yaml)

    try:
        reg = load_registry(cards_dir)
        assert "insurance_policy" in reg

        # 3. Re-extract with refresh for that card
        stats = extract_sync(settings, card_filter="insurance_policy", refresh=True)
        assert stats["extracted"] > 0

        # Check document now has card_type = insurance_policy
        doc_row = conn.execute(
            "SELECT card_type FROM documents WHERE suggested_type = 'insurance_policy'"
        ).fetchone()
        assert doc_row is not None
        assert doc_row["card_type"] == "insurance_policy"
    finally:
        # Clean up added card
        if target_yaml.exists():
            target_yaml.unlink()
        if proposed_yaml.exists():
            proposed_yaml.unlink()
        conn.close()


def test_list_cards(discover_env):
    """list_cards returns all registered cards with metadata and counts."""
    settings, _ = discover_env
    conn = connect(settings.db_path)
    reg = load_registry(settings.cards_dir)
    cards = list_cards(reg, conn)
    conn.close()

    assert len(cards) >= 5
    card_names = [c["name"] for c in cards]
    assert "generic" in card_names
    assert "identity" in card_names
    assert "legal" in card_names
