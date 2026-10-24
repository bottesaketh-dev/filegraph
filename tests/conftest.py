"""Pytest configuration and shared fixtures for FileGraph tests."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure filegraph package and repo root are importable
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Always use fake LLM in tests
os.environ["FG_FAKE_LLM"] = "1"


@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a temporary database file."""
    return tmp_path / "test.db"


@pytest.fixture
def tmp_config(tmp_path):
    """Create a minimal filegraph.yaml in a temp dir and return its path."""
    cfg = tmp_path / "filegraph.yaml"
    cfg.write_text(
        """
me:
  names: ["Test User"]
  organizations: ["Test Corp"]

roots:
  - path: {root}
    name: test_root
    profile: auto
    llm: cloud

ignore:
  - "node_modules/"
  - ".git/"
  - "__pycache__/"

deny:
  - "**/.ssh/**"

limits:
  max_file_mb: 1
  max_text_chars_per_file: 100000
  confirm_tokens_over: 100000
""".format(root=str(tmp_path / "files").replace("\\", "/"))
    )
    # Create the root directory
    (tmp_path / "files").mkdir(exist_ok=True)
    return cfg


@pytest.fixture
def initialized_db(tmp_db):
    """Return a connection to an initialized database with the full schema."""
    from filegraph.db import connect, init_schema
    conn = connect(tmp_db)
    init_schema(conn)
    return conn
