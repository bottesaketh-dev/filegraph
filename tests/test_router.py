"""Tests for router module (Section 10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from filegraph.cards import load_registry
from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.ingest import ingest
from filegraph.router import route
from tests.make_fixtures import create_fixture_tree


@pytest.fixture
def routed_db(tmp_path: Path):
    tree_dir = create_fixture_tree(tmp_path / "_tree")
    cfg_path = tree_dir / "filegraph.yaml"
    db_path = tmp_path / "test_router.db"
    settings = load_settings(config_path=cfg_path, db_path=db_path)

    conn = connect(db_path)
    init_schema(conn)
    conn.close()

    ingest(settings, workers=1)
    conn = connect(db_path)
    return conn, settings, tree_dir


def test_routing_rules_and_code(routed_db):
    """Verify routing decisions for fixture files (Section 10)."""
    conn, settings, _ = routed_db
    reg = load_registry(settings.cards_dir)

    # 1. Code file -> code
    py_row = conn.execute("SELECT * FROM files WHERE filename = 'session.py'").fetchone()
    assert py_row is not None
    ctype, how = route(conn, py_row, reg, settings)
    assert ctype == "code"
    assert how == "code"

    # 2. Passport -> identity via rules
    pass_row = conn.execute("SELECT * FROM files WHERE filename = 'Scan_0042.pdf'").fetchone()
    assert pass_row is not None
    ctype, how = route(conn, pass_row, reg, settings)
    assert ctype == "identity"
    assert how == "rules"

    # 3. Contract MSA -> legal via rules
    msa_row = conn.execute("SELECT * FROM files WHERE filename = 'Acme MSA 2023.docx'").fetchone()
    assert msa_row is not None
    ctype, how = route(conn, msa_row, reg, settings)
    assert ctype == "legal"
    assert how == "rules"

    # 4. Car policy -> generic via classification
    pol_row = conn.execute("SELECT * FROM files WHERE filename = 'policy_2026.pdf'").fetchone()
    assert pol_row is not None
    ctype, how = route(conn, pol_row, reg, settings)
    assert ctype == "generic"
    assert how in ("llm", "rules")

    conn.close()
