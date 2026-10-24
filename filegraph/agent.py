"""Question-answering agent with SQL, search, and graph traversal tools (Section 15)."""

from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from filegraph.cards import load_registry
from filegraph.config import Settings
from filegraph.db import abs_path, connect, page_texts
from filegraph.llm import chat_with_tools, model_for_policy
from filegraph.search import hybrid_search
from filegraph.security import is_denied, redact


def _get_ro_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open a read-only SQLite connection (Strict Rule 3)."""
    p = Path(db_path).resolve()
    # URI read-only connection
    uri = f"file:{p.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


AGENT_TOOL_SCHEMAS = [
    {
        "name": "run_sql",
        "description": "Execute a single SELECT or WITH query against the database (up to 300 rows). Never use DELETE, INSERT or DROP.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SELECT or WITH SQL statement"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "search",
        "description": "Hybrid BM25 and vector search across documents and units (limit <= 30).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query terms"},
                "limit": {"type": "integer", "description": "Max results to return (<= 30)"},
                "kind": {"type": "string", "description": "Filter by kind (document, code, image)"},
                "root": {"type": "string", "description": "Filter by root folder name"},
                "card_type": {"type": "string", "description": "Filter by card type (identity, finance, legal, generic, code)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_related",
        "description": "Traverse graph edges, linked documents, and unresolved references for a document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "integer", "description": "Target file ID"},
                "depth": {"type": "integer", "description": "Hop depth (1 or 2)"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "read_document",
        "description": "Read stored unit texts and pages of an indexed document (budget 20,000 chars).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "integer", "description": "Target file ID"},
                "pages": {"type": "string", "description": "Optional page range, e.g. '1-3,7'"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "read_file",
        "description": "Live read from disk for text/code files (up to 400 lines).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "integer", "description": "File ID from database"},
                "path": {"type": "string", "description": "Relative path"},
                "root": {"type": "string", "description": "Root name if path given"},
                "start_line": {"type": "integer", "description": "Starting line number"},
                "end_line": {"type": "integer", "description": "Ending line number"},
            },
        },
    },
    {
        "name": "find_symbol",
        "description": "Search code symbols (functions, classes, methods) across indexed repositories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Symbol name (exact or substring)"},
                "repo": {"type": "string", "description": "Filter by repo name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "grep",
        "description": "Live regex search across files in roots.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "root": {"type": "string", "description": "Filter by root folder name"},
                "glob": {"type": "string", "description": "Glob filter, e.g. '*.py'"},
                "max_results": {"type": "integer", "description": "Max matching lines (<= 100)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "list_dir",
        "description": "List directory entries inside a configured root folder (up to 200 entries).",
        "input_schema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Root name"},
                "path": {"type": "string", "description": "Subdirectory path relative to root"},
            },
            "required": ["root"],
        },
    },
    {
        "name": "describe_cards",
        "description": "Show card templates, categories, statuses, date labels, and top fact keys in the index.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


class Agent:
    """Tool-using QA Agent (Section 15)."""

    def __init__(
        self,
        settings: Settings,
        model_override: str | None = None,
        allow_cloud: bool = False,
    ):
        self.settings = settings
        self.db_path = settings.db_path
        self.conn = _get_ro_connection(self.db_path)
        self.registry = load_registry(settings.cards_dir)

        # Policy & Model selection (Section 15.1)
        has_local_root = any(r.llm == "local" for r in settings.roots)
        if model_override:
            self.model_key = model_override
        elif has_local_root and not allow_cloud:
            self.model_key = os.environ.get("FG_LOCAL_MODEL", "OLLAMA_LOCAL")
        else:
            self.model_key = model_for_policy("cloud", "agent") or "GPT_4_1"

        self.chat = chat_with_tools(self.model_key, AGENT_TOOL_SCHEMAS)
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Construct system prompt adhering to Appendix A.2."""
        today = datetime.date.today().isoformat()
        me_names = ", ".join(self.settings.me.names) if self.settings.me.names else "The user"
        me_orgs = ", ".join(self.settings.me.organizations) if self.settings.me.organizations else "None"
        roots_info = ", ".join(f"{r.name} ({r.llm})" for r in self.settings.roots)

        cards_summary = ", ".join(f"{c.name}: {c.description}" for c in self.registry.cards.values())

        prompt = f"""You answer questions about the user's own files using the tools provided. Today's date: {today}.
The user: {me_names}. The user's own organizations: {me_orgs}.
Roots (top-level folders): {roots_info}.
Card types: {cards_summary}.

Database (SQLite, read-only):
- View `catalog`, one row per analysed file: file_id, root, path, folder, filename, ext, kind (document|email|data|image|code),
  source ('disk' or 'attachment' = came out of an email), parent_id, repo_id, language, card_type, category, title, summary,
  status, main_entity, main_entity_id, main_date, main_date_label, confidence (high|medium|low), needs_review (0/1),
  review_reasons (JSON), suggested_type, extras (JSON of card-specific fields; use json_extract(extras,'$.field')),
  duplicate_of (file_id of an identical earlier copy, else NULL).
- `doc_dates`(file_id, label, label_detail, date, basis, quote, page): every labelled date. Dates are 'YYYY-MM-DD' strings.
- `doc_facts`(file_id, key, value_text, value_num, unit, quote, page): looked-up values such as policy_number, premium, amount.
- `entities`(id, kind, name, aliases JSON, is_self) and `doc_entities`(file_id, entity_id, role, raw_name).
- `edges`(src_id, dst_id NULL = referenced document not found, edge_type, method, confidence, evidence, ref JSON).
- `repos`(id, name, rel_path, remote_url, branch, head_commit, last_commit_at, languages JSON, summary, summary_json).
- `code_symbols`(file_id, repo_id, name, qualname, kind, start_line, end_line, signature, docstring).
- `files`(id, root_id, rel_path, kind, size, mtime, parse_status, ...) for files without cards.
Call describe_cards to see which date labels, fact keys and fields actually exist before filtering on them.

How to answer
- Lists, counts, totals, filters and anything about dates or amounts: ALWAYS use run_sql. SQL over the tables is complete; search is not.
- Exclude duplicate_of IS NOT NULL unless asked, and say how many duplicates you excluded.
- Match names with LIKE on entities.name and entities.aliases, then join doc_entities.
- Wording, topics or "find the document about...": use search, then read_document to confirm.
- Code questions: use find_symbol and search with kind='code'; use read_file for exact lines and grep for anything that may have changed since indexing. Report paths with line numbers.
- Relationship questions (what amends what, current terms, what is connected): use get_related.
- When a date's basis is not 'stated', or needs_review=1, or confidence is low, say so for those rows. Mention rows with missing dates that might also qualify.
- Never invent files, values or facts. Cite file paths for every claim. If the index has nothing relevant, say exactly: "I couldn't find this in your indexed files." and suggest what to check.
- Keep answers compact. Use a table for lists of documents.
"""
        return prompt

    def execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute tool and return JSON result string."""
        try:
            if name == "run_sql":
                sql = args.get("sql", "").strip()
                # Strict check: single SELECT or WITH only
                if ";" in sql.rstrip(";"):
                    return json.dumps({"error": "Only a single SQL statement without semicolons is permitted."})
                first_word = sql.split()[0].upper() if sql.split() else ""
                if first_word not in ("SELECT", "WITH"):
                    return json.dumps({"error": "Only SELECT or WITH queries are permitted."})

                cur = self.conn.cursor()
                cur.execute(sql)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchmany(301)
                note = "Results capped at 300 rows" if len(rows) > 300 else None
                formatted_rows = [[r[c] for c in cols] for r in rows[:300]]
                res = {"columns": cols, "rows": formatted_rows}
                if note:
                    res["note"] = note
                return json.dumps(res, default=str)

            elif name == "search":
                q = args.get("query", "")
                lim = min(30, int(args.get("limit", 10)))
                hits = hybrid_search(
                    self.conn,
                    query=q,
                    limit=lim,
                    kind=args.get("kind"),
                    root=args.get("root"),
                    card_type=args.get("card_type"),
                    settings=self.settings,
                )
                return json.dumps(hits, default=str)

            elif name == "get_related":
                fid = args["file_id"]
                edges_fwd = self.conn.execute(
                    "SELECT e.*, d.title as dst_title FROM edges e LEFT JOIN documents d ON d.file_id = e.dst_id WHERE e.src_id = ?",
                    (fid,),
                ).fetchall()
                edges_rev = self.conn.execute(
                    "SELECT e.*, d.title as src_title FROM edges e LEFT JOIN documents d ON d.file_id = e.src_id WHERE e.dst_id = ?",
                    (fid,),
                ).fetchall()

                # Same entity
                same_ent: list[dict] = []
                doc_row = self.conn.execute("SELECT main_entity_id FROM documents WHERE file_id = ?", (fid,)).fetchone()
                if doc_row and doc_row["main_entity_id"]:
                    same_rows = self.conn.execute(
                        "SELECT file_id, title FROM documents WHERE main_entity_id = ? AND file_id != ? LIMIT 60",
                        (doc_row["main_entity_id"], fid),
                    ).fetchall()
                    same_ent = [dict(r) for r in same_rows]

                return json.dumps({
                    "forward_edges": [dict(r) for r in edges_fwd],
                    "reverse_edges": [dict(r) for r in edges_rev],
                    "same_entity_documents": same_ent,
                }, default=str)

            elif name == "read_document":
                fid = args["file_id"]
                pages_arg = args.get("pages")
                units = page_texts(self.conn, fid)
                if not units:
                    return json.dumps({"text": "No text content available for this document."})

                allowed_pages = None
                if pages_arg:
                    allowed_pages = set()
                    for part in pages_arg.split(","):
                        if "-" in part:
                            p1, p2 = part.split("-", 1)
                            allowed_pages.update(range(int(p1), int(p2) + 1))
                        else:
                            allowed_pages.add(int(part.strip()))

                body = []
                total_len = 0
                for p_num, text in units:
                    if allowed_pages and p_num not in allowed_pages:
                        continue
                    header = f"=== Page {p_num} ==="
                    body.append(header)
                    t_str = text if text.strip() else "(scanned page - no text layer)"
                    body.append(t_str)
                    total_len += len(header) + len(t_str)
                    if total_len >= 20000:
                        body.append("[Truncated at 20,000 characters]")
                        break

                return "\n".join(body)

            elif name == "read_file":
                fid = args.get("file_id")
                rel_path = args.get("path")
                root_name = args.get("root")

                target_path = None
                if fid:
                    r = self.conn.execute("SELECT f.*, r.name as root_name FROM files f JOIN roots r ON r.id = f.root_id WHERE f.id = ?", (fid,)).fetchone()
                    if r:
                        target_path = abs_path(self.conn, r)
                elif rel_path and root_name:
                    for root_cfg in self.settings.roots:
                        if root_cfg.name == root_name:
                            target_path = Path(root_cfg.path) / rel_path
                            break

                if not target_path or not target_path.is_file():
                    return json.dumps({"error": "File not found or invalid arguments."})

                # Privacy check: outside roots or denied
                is_within = any(target_path.resolve().is_relative_to(Path(rc.path).resolve()) for rc in self.settings.roots)
                if not is_within or is_denied(target_path, self.settings):
                    return json.dumps({"error": "Access denied: file is outside roots or on deny list."})

                try:
                    lines = target_path.read_text(encoding="utf-8", errors="replace").splitlines()
                except Exception as exc:
                    return json.dumps({"error": f"Cannot read text: {exc}"})

                s_line = max(1, int(args.get("start_line", 1)))
                e_line = min(len(lines), int(args.get("end_line", s_line + 399)))
                if (e_line - s_line) > 400:
                    e_line = s_line + 400

                selected = [f"{i}: {lines[i-1]}" for i in range(s_line, e_line + 1)]
                text_out = "\n".join(selected)
                redacted_out, _ = redact(text_out)
                return redacted_out

            elif name == "find_symbol":
                sym_name = args["name"]
                repo_filter = args.get("repo")
                sql = """
                    SELECT cs.name, cs.qualname, cs.kind, cs.start_line, cs.end_line,
                           cs.signature, cs.docstring, f.rel_path, r.name as repo_name
                    FROM code_symbols cs
                    JOIN files f ON f.id = cs.file_id
                    LEFT JOIN repos r ON r.id = cs.repo_id
                    WHERE cs.name LIKE ?
                """
                params = [f"%{sym_name}%"]
                if repo_filter:
                    sql += " AND r.name = ?"
                    params.append(repo_filter)
                sql += " ORDER BY CASE WHEN cs.name = ? THEN 0 ELSE 1 END LIMIT 50"
                params.append(sym_name)

                rows = self.conn.execute(sql, params).fetchall()
                return json.dumps([dict(r) for r in rows], default=str)

            elif name == "grep":
                pat = args["pattern"]
                r_name = args.get("root")
                max_res = min(100, int(args.get("max_results", 100)))

                target_roots = [r for r in self.settings.roots if not r_name or r.name == r_name]
                matches: list[str] = []
                regex = re.compile(pat)

                for r_cfg in target_roots:
                    r_path = Path(r_cfg.path)
                    if not r_path.is_dir():
                        continue
                    for p in r_path.rglob("*"):
                        if not p.is_file() or is_denied(p, self.settings):
                            continue
                        try:
                            for idx, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                                if regex.search(line):
                                    rel = p.relative_to(r_path)
                                    matches.append(f"{r_cfg.name}/{rel}:{idx}: {line[:200]}")
                                    if len(matches) >= max_res:
                                        break
                        except Exception:
                            continue
                        if len(matches) >= max_res:
                            break
                    if len(matches) >= max_res:
                        break

                redacted_matches = [redact(m)[0] for m in matches]
                return "\n".join(redacted_matches) if redacted_matches else "No matches found."

            elif name == "list_dir":
                r_name = args["root"]
                sub_p = args.get("path", "")
                target_root = next((r for r in self.settings.roots if r.name == r_name), None)
                if not target_root:
                    return json.dumps({"error": f"Root '{r_name}' not found."})

                folder = Path(target_root.path) / sub_p if sub_p else Path(target_root.path)
                if not folder.is_dir():
                    return json.dumps({"error": f"Folder '{folder}' not found."})

                entries = []
                for child in sorted(folder.iterdir()):
                    if is_denied(child, self.settings):
                        continue
                    entries.append({
                        "name": child.name,
                        "type": "directory" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else None,
                    })
                    if len(entries) >= 200:
                        break
                return json.dumps(entries)

            elif name == "describe_cards":
                cards_data = []
                for cname, c in self.registry.cards.items():
                    cards_data.append({
                        "name": c.name,
                        "description": c.description,
                        "categories": c.categories,
                        "statuses": c.statuses,
                        "date_labels": c.date_labels,
                        "preferred_fact_keys": c.preferred_fact_keys,
                        "fields": c.fields,
                    })
                top_facts = self.conn.execute(
                    "SELECT key, count(*) as count FROM doc_facts GROUP BY key ORDER BY count DESC LIMIT 50"
                ).fetchall()
                top_dates = self.conn.execute(
                    "SELECT label, count(*) as count FROM doc_dates GROUP BY label ORDER BY count DESC LIMIT 50"
                ).fetchall()
                return json.dumps({
                    "cards": cards_data,
                    "top_fact_keys": [dict(r) for r in top_facts],
                    "top_date_labels": [dict(r) for r in top_dates],
                })

            return json.dumps({"error": f"Unknown tool '{name}'"})
        except Exception as exc:
            return json.dumps({"error": f"Tool execution failed: {exc}"})

    def ask(self, question: str, max_steps: int = 15, verbose: bool = False) -> str:
        """Execute agent question answering loop (Section 15.1)."""
        from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=question),
        ]

        for step in range(max_steps):
            resp = self.chat.invoke(messages)
            tool_calls = getattr(resp, "tool_calls", None)

            if not tool_calls:
                return str(resp.content)

            # Execute tool calls
            messages.append(resp)
            for tc in tool_calls:
                name = tc.get("name")
                args = tc.get("args", {})
                call_id = tc.get("id", f"call_{step}")

                if verbose:
                    print(f"  · {name}: {str(args)[:200]}")

                tool_output = self.execute_tool(name, args)
                if len(tool_output) > 40000:
                    tool_output = tool_output[:40000] + "... [truncated at 40,000 chars]"

                messages.append(ToolMessage(content=tool_output, tool_call_id=call_id))

        return "(Stopped after too many steps; try a narrower question.)"

    def chat(self, verbose: bool = False, allow_cloud: bool = False) -> None:
        """Interactive REPL chat loop (Section 18)."""
        print(f"FileGraph Agent ready (model: {self.model_key}). Type your question or press Enter/Ctrl+C to quit.\n")
        while True:
            try:
                line = input("fg> ").strip()
                if not line:
                    break
                print()
                ans = self.ask(line, verbose=verbose)
                print(f"\n{ans}\n")
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break
