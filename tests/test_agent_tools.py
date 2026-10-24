"""Unit tests for Phase P7: Question-answering Agent & Tools (Section 15)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from filegraph.agent import Agent
from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.extract import extract_sync
from filegraph.ingest import ingest
from filegraph.link import link
from tests.make_fixtures import create_fixture_tree


@pytest.fixture
def agent_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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

    return settings, tmp_path


def test_agent_tools_run_sql(agent_env):
    """run_sql allows valid SELECT queries and rejects DELETE / multi-statements."""
    settings, _ = agent_env
    agent = Agent(settings)

    # 1. Valid SELECT
    res_str = agent.execute_tool("run_sql", {"sql": "SELECT COUNT(*) as cnt FROM catalog"})
    res = json.loads(res_str)
    assert "rows" in res
    assert res["rows"][0][0] > 0

    # 2. Reject DELETE
    err_del = agent.execute_tool("run_sql", {"sql": "DELETE FROM documents"})
    assert "error" in json.loads(err_del)

    # 3. Reject multi-statement
    err_multi = agent.execute_tool("run_sql", {"sql": "SELECT 1; SELECT 2;"})
    assert "error" in json.loads(err_multi)


def test_agent_tools_read_file_security(agent_env):
    """read_file refuses paths outside roots and denied files."""
    settings, tmp_path = agent_env
    agent = Agent(settings)

    # 1. Path outside roots
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret outside", encoding="utf-8")
    err_out = agent.execute_tool("read_file", {"path": str(outside_file), "root": "test_root"})
    assert "error" in json.loads(err_out) or "denied" in err_out.lower()

    # 2. Denied file (.env)
    env_file = Path(settings.roots[0].path) / ".env"
    if env_file.exists():
        err_denied = agent.execute_tool("read_file", {"path": ".env", "root": "test_root"})
        assert "error" in json.loads(err_denied) or "denied" in err_denied.lower()


def test_agent_tools_search_and_related(agent_env):
    """search, get_related, and describe_cards execute and return valid JSON."""
    settings, _ = agent_env
    agent = Agent(settings)

    # search
    s_res = json.loads(agent.execute_tool("search", {"query": "agreement", "limit": 5}))
    assert isinstance(s_res, list)

    # describe_cards
    cards_res = json.loads(agent.execute_tool("describe_cards", {}))
    assert "cards" in cards_res
    assert len(cards_res["cards"]) > 0

    # get_related
    doc_row = agent.conn.execute("SELECT file_id FROM documents LIMIT 1").fetchone()
    if doc_row:
        rel_res = json.loads(agent.execute_tool("get_related", {"file_id": doc_row["file_id"]}))
        assert "forward_edges" in rel_res


def test_scripted_fake_agent_run(agent_env, monkeypatch: pytest.MonkeyPatch):
    """Scripted fake agent run completes and returns text."""
    settings, tmp_path = agent_env

    script = [
        {
            "tool_calls": [
                {
                    "name": "run_sql",
                    "args": {"sql": "SELECT title FROM documents LIMIT 1"},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ]
        },
        {"content": "Found the document title."},
    ]
    script_file = tmp_path / "fake_script.json"
    script_file.write_text(json.dumps(script), encoding="utf-8")

    monkeypatch.setenv("FG_FAKE_AGENT_SCRIPT", str(script_file))

    agent = Agent(settings)
    ans = agent.ask("What is the first document?")
    assert "Found the document title." in ans
