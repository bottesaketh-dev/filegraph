"""Test filing suggestions, collision avoidance, and backtesting (Section 17 & Phase P10)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pymupdf

from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.filing import backtest, candidates, file_document, folder_profile
from filegraph.ingest import ingest
from filegraph.extract import extract_sync
from filegraph.link import link
from tests.make_fixtures import create_fixture_tree


@pytest.fixture(scope="module")
def populated_env(tmp_path_factory):
    """Set up temporary fixture tree with fake LLM and populated database."""
    base = tmp_path_factory.mktemp("fg_filing_tree")
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
"""
    yaml_path.write_text(config_content, encoding="utf-8")

    os.environ["FG_FAKE_LLM"] = "1"
    settings = load_settings(config_path=yaml_path, db_path=db_path)

    conn = connect(settings.db_path)
    init_schema(conn)
    conn.close()

    # Ingest, extract, and link
    ingest(settings)
    extract_sync(settings)
    link(settings)

    return settings, base


def test_candidates_and_folder_profile(populated_env):
    """Test candidate scoring and folder profile generation."""
    settings, base = populated_env
    conn = connect(settings.db_path)
    try:
        doc_info = {
            "title": "ICICI Lombard Motor Premium Receipt",
            "summary": "Annual vehicle insurance premium receipt",
            "card_type": "generic",
            "category": "auto",
            "main_entity_name": "ICICI Lombard General Insurance Company",
            "suggested_type": "insurance_policy",
        }
        cands = candidates(conn, "personal", doc_info)
        assert len(cands) > 0
        top_folder, top_score = cands[0]
        # 'car' should be top folder for ICICI insurance
        assert top_folder == "car"
        assert top_score > 0.0

        profile = folder_profile(conn, "personal", "car")
        assert "car" in profile
        assert "ICICI Lombard" in profile or "policy" in profile.lower()
    finally:
        conn.close()


def test_file_document_dry_run_and_apply(populated_env):
    """Test filing suggestion and apply with collision avoidance."""
    settings, base = populated_env

    # Create a new test invoice outside roots
    unfiled_file = base / "new_icici_receipt.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (50, 72),
        "ICICI Lombard General Insurance Company\n"
        "Receipt No: REC-2026-999\n"
        "Insured Name: Ravi Sharma\n"
        "Registration TS09 AB 1234\n"
        "Premium Paid: Rs. 14,200",
        fontsize=12,
    )
    doc.save(str(unfiled_file))
    doc.close()

    # 1. Dry run (apply=False)
    rec, dest = file_document(
        unfiled_file,
        target_root="personal",
        apply=False,
        settings=settings,
    )
    assert rec["folder"] == "car"
    assert rec["confidence"] >= 0.6
    assert dest is None

    # 2. Apply (apply=True, yes=True)
    rec, dest = file_document(
        unfiled_file,
        target_root="personal",
        apply=True,
        yes=True,
        settings=settings,
    )
    assert dest is not None
    assert dest.exists()
    assert dest.name == "new_icici_receipt.pdf"
    assert dest.parent.name == "car"

    # 3. Collision avoidance: file another file with the same name
    rec2, dest2 = file_document(
        unfiled_file,
        target_root="personal",
        apply=True,
        yes=True,
        settings=settings,
    )
    assert dest2 is not None
    assert dest2.exists()
    assert dest2.name == "new_icici_receipt (2).pdf"

    # Verify filing_log
    conn = connect(settings.db_path)
    try:
        logs = conn.execute("SELECT * FROM filing_log WHERE root_name = 'personal'").fetchall()
        assert len(logs) >= 2
        last_log = logs[-1]
        assert last_log["action"] == "copied"
        assert "car" in last_log["suggested_folder"]
    finally:
        conn.close()


def test_backtest_runs(populated_env):
    """Test backtesting on canonical indexed documents."""
    settings, base = populated_env
    res = backtest(settings, n=5)
    assert res["total"] > 0
    assert "exact_acc" in res
    assert "top3_acc" in res
    assert Path(res["csv_path"]).exists()
