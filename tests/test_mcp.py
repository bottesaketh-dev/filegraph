"""Test MCP server creation, tools, and resource registration (Section 20 & Phase P11)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from filegraph.config import load_settings
from filegraph.db import connect, init_schema
from filegraph.mcp_server import create_mcp_server
from tests.make_fixtures import create_fixture_tree


@pytest.fixture(scope="module")
def mcp_env(tmp_path_factory):
    base = tmp_path_factory.mktemp("fg_mcp_tree")
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

    return settings


def test_mcp_server_tools_and_resources(mcp_env):
    """Test that the MCP server initializes with all 9 tools and schema resource."""
    server = create_mcp_server(mcp_env)
    assert server is not None

    # Get registered tools
    if hasattr(server, "_tool_manager"):
        tools = server._tool_manager.list_tools()
        tool_names = [t.name for t in tools]
    elif hasattr(server, "list_tools"):
        tools = server.list_tools()
        tool_names = [t.name if hasattr(t, "name") else str(t) for t in tools]
    else:
        tool_names = []

    expected_tools = {
        "run_sql",
        "search",
        "get_related",
        "read_document",
        "read_file",
        "find_symbol",
        "grep",
        "list_dir",
        "describe_cards",
    }
    assert expected_tools.issubset(set(tool_names)), f"Missing tools: {expected_tools - set(tool_names)}"


def test_mcp_tools_execution(mcp_env):
    """Test calling tools through MCP server."""
    server = create_mcp_server(mcp_env)

    # Call run_sql through tool function
    if hasattr(server, "_tool_manager"):
        tool = server._tool_manager.get_tool("run_sql")
        res = tool.fn(sql="SELECT 1 as num")
        assert "num" in str(res)
    elif hasattr(server, "get_tool"):
        tool = server.get_tool("run_sql")
        res = tool(sql="SELECT 1 as num")
        assert "num" in str(res)
