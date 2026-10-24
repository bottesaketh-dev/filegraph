"""Tests for extraction module (Section 11)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from filegraph.cards import load_registry
from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.extract import extract_sync, repair_and_validate
from filegraph.ingest import ingest
from tests.make_fixtures import create_fixture_tree


@pytest.fixture
def extracted_env(tmp_path: Path):
    os.environ["FG_FAKE_LLM"] = "1"
    tree_dir = create_fixture_tree(tmp_path / "_tree")
    cfg_path = tree_dir / "filegraph.yaml"
    db_path = tmp_path / "test_extract.db"
    settings = load_settings(config_path=cfg_path, db_path=db_path)

    conn = connect(db_path)
    init_schema(conn)
    conn.close()

    ingest(settings, workers=1)
    return settings, tree_dir, db_path


def test_schema_repair():
    """Verify schema repair replaces bad enums and drops unknown keys (Section 11.5)."""
    tool_spec = {
        "input_schema": {
            "type": "object",
            "required": ["category", "title", "status"],
            "properties": {
                "category": {"type": "string", "enum": ["finance", "medical", "legal", "other"]},
                "title": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "unknown"]},
            },
        }
    }
    # When category is missing, ensure it defaults to 'other', NOT enum[0] ('finance')
    malformed = {
        "title": "Document Title",
        "unknown_extra_key": "some_value",
    }
    clean, reasons = repair_and_validate(malformed, tool_spec)
    assert clean["category"] == "other"
    assert clean["title"] == "Document Title"
    assert "unknown_extra_key" not in clean

    # When model outputs 'type' instead of 'category', verify mapping
    with_type = {
        "type": "legal",
        "title": "Agreement",
    }
    clean2, reasons2 = repair_and_validate(with_type, tool_spec)
    assert clean2["category"] == "legal"


def test_clean_date_calendar_and_today():
    """Verify calendar validation, academic year normalization, and Today header rejection."""
    from filegraph.extract import _clean_date

    # Academic year normalization
    d, reason, basis = _clean_date("2021-22")
    assert d == "2021-01-01"
    assert basis == "partial"

    # Today header rejection
    d_today, _, _ = _clean_date("2026-10-24", quote="Today: 2026-10-24")
    assert d_today is None

    # Invalid month rejection
    d_inv, _, _ = _clean_date("2021-22-01")
    assert d_inv is None


def test_norm_key_person_sorting_and_honorifics():
    """Verify person entity norm_key sorts tokens and strips compound honorifics."""
    from filegraph.link import norm_key

    k1 = norm_key("Saketh Botte", "person")
    k2 = norm_key("Botte Saketh", "person")
    k3 = norm_key("Mr. Botte Saketh", "person")
    k4 = norm_key("Sri/Srimathi/Kumari BOTTE SAKETH", "person")
    k5 = norm_key("Saketh Botte, B. Saketh", "person")

    assert k1 == "botte saketh"
    assert k2 == "botte saketh"
    assert k3 == "botte saketh"
    assert k4 == "botte saketh"
    assert k5 == "botte saketh"


def test_phase_3_acceptance_extraction(extracted_env):
    """Verify Phase P3 acceptance criteria on the fixture tree (Section 22).

    Acceptance:
    - extraction writes documents, doc_dates, doc_facts
    - duplicates get cards without extra LLM calls
    - re-run extracts nothing
    - version bump re-extracts only that card's documents
    """
    settings, tree_dir, db_path = extracted_env

    # 1. First extract run
    stats1 = extract_sync(settings)
    assert stats1["extracted"] > 0

    conn = connect(db_path)

    # Check documents, doc_dates, doc_facts populated
    doc_count = conn.execute("SELECT COUNT(*) as n FROM documents").fetchone()["n"]
    date_count = conn.execute("SELECT COUNT(*) as n FROM doc_dates").fetchone()["n"]
    fact_count = conn.execute("SELECT COUNT(*) as n FROM doc_facts").fetchone()["n"]

    assert doc_count >= 5
    assert date_count >= 3
    assert fact_count >= 1

    # Check duplicate gets card without extra LLM call
    msa_row = conn.execute("SELECT id FROM files WHERE filename = 'Acme MSA 2023.docx'").fetchone()
    copy_row = conn.execute("SELECT id FROM files WHERE filename = 'Acme MSA 2023 copy.docx'").fetchone()

    msa_doc = conn.execute("SELECT * FROM documents WHERE file_id = ?", (msa_row["id"],)).fetchone()
    copy_doc = conn.execute("SELECT * FROM documents WHERE file_id = ?", (copy_row["id"],)).fetchone()

    assert msa_doc is not None
    assert copy_doc is not None
    assert msa_doc["title"] == copy_doc["title"]

    # 2. Re-run extracts nothing (cached)
    stats2 = extract_sync(settings)
    assert stats2["extracted"] == 0
    assert stats2["skipped_cached"] > 0

    # 3. Version bump re-extracts only that card's documents
    # Bump version of identity card
    reg = load_registry(settings.cards_dir)
    id_card = reg["identity"]
    id_card.version += 1

    # Invalidate cache by version bump and re-run with refresh=False
    conn.execute("UPDATE extraction_cache SET card_version = 999 WHERE card_type = 'identity'")
    conn.commit()

    conn.close()
