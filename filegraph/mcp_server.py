"""MCP server exposing FileGraph agent tools and schema (Section 20).

Uses the official MCP Python SDK (FastMCP) with stdio transport.
Exposes read-only queries and search over the indexed catalog.
"""

from __future__ import annotations

import sys
from typing import Any

from filegraph.config import Settings, load_settings

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        FastMCP = None


def create_mcp_server(settings: Settings | None = None) -> Any:
    """Create and configure the FastMCP server instance."""
    if FastMCP is None:
        raise ImportError(
            "The 'mcp' package is not installed. Install optional dependencies with: "
            "pip install -r requirements-optional.txt or pip install mcp"
        )

    settings = settings or load_settings()
    from filegraph.agent import Agent

    agent = Agent(settings=settings)
    mcp = FastMCP("filegraph")

    # Resource: filegraph://schema
    @mcp.resource("filegraph://schema")
    def schema_resource() -> str:
        """Database schema and catalog description for FileGraph."""
        return agent.build_system_prompt()

    # Tool 1: run_sql
    @mcp.tool()
    def run_sql(sql: str) -> str:
        """Execute a single SELECT or WITH query against the database (up to 300 rows). Never use DELETE, INSERT or DROP."""
        return agent.execute_tool("run_sql", {"sql": sql})

    # Tool 2: search
    @mcp.tool()
    def search(
        query: str,
        limit: int = 10,
        kind: str | None = None,
        root: str | None = None,
        card_type: str | None = None,
    ) -> str:
        """Hybrid BM25 and vector search across documents and units (limit <= 30)."""
        args: dict[str, Any] = {"query": query, "limit": limit}
        if kind:
            args["kind"] = kind
        if root:
            args["root"] = root
        if card_type:
            args["card_type"] = card_type
        return agent.execute_tool("search", args)

    # Tool 3: get_related
    @mcp.tool()
    def get_related(file_id: int, depth: int = 1) -> str:
        """Traverse graph edges, linked documents, and unresolved references for a document."""
        return agent.execute_tool("get_related", {"file_id": file_id, "depth": depth})

    # Tool 4: read_document
    @mcp.tool()
    def read_document(file_id: int, pages: str | None = None) -> str:
        """Read stored unit texts and pages of an indexed document (budget 20,000 chars)."""
        args: dict[str, Any] = {"file_id": file_id}
        if pages:
            args["pages"] = pages
        return agent.execute_tool("read_document", args)

    # Tool 5: read_file
    @mcp.tool()
    def read_file(
        file_id: int | None = None,
        path: str | None = None,
        root: str | None = None,
        start_line: int = 1,
        end_line: int = 400,
    ) -> str:
        """Live read from disk for text/code files (up to 400 lines)."""
        args: dict[str, Any] = {"start_line": start_line, "end_line": end_line}
        if file_id is not None:
            args["file_id"] = file_id
        if path is not None:
            args["path"] = path
        if root is not None:
            args["root"] = root
        return agent.execute_tool("read_file", args)

    # Tool 6: find_symbol
    @mcp.tool()
    def find_symbol(name: str, repo: str | None = None) -> str:
        """Search code symbols (functions, classes, methods) across indexed repositories."""
        args: dict[str, Any] = {"name": name}
        if repo:
            args["repo"] = repo
        return agent.execute_tool("find_symbol", args)

    # Tool 7: grep
    @mcp.tool()
    def grep(
        pattern: str,
        root: str | None = None,
        glob: str | None = None,
        max_results: int = 100,
    ) -> str:
        """Live regex search across files in roots."""
        args: dict[str, Any] = {"pattern": pattern, "max_results": max_results}
        if root:
            args["root"] = root
        if glob:
            args["glob"] = glob
        return agent.execute_tool("grep", args)

    # Tool 8: list_dir
    @mcp.tool()
    def list_dir(root: str, path: str = "") -> str:
        """Lists entries (name, type, size) of a folder inside a root, <= 200 entries."""
        return agent.execute_tool("list_dir", {"root": root, "path": path})

    # Tool 9: describe_cards
    @mcp.tool()
    def describe_cards() -> str:
        """Card registry summary and top date/fact keys present in the database."""
        return agent.execute_tool("describe_cards", {})

    return mcp


def run_mcp_server(settings: Settings | None = None) -> None:
    """Run MCP server over stdio transport."""
    server = create_mcp_server(settings)
    server.run(transport="stdio")
