#!/usr/bin/env python3
"""FileGraph CLI entry point (Section 18)."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Reconfigure stdout to UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    """Copy example files, check FTS5, print next steps."""
    repo = _repo_root()
    cwd = Path.cwd()

    def _find_src(name: str) -> Path | None:
        """Look for *name* in CWD first, then the repo root."""
        for d in (cwd, repo):
            p = d / name
            if p.is_file():
                return p
        return None

    # Copy filegraph.yaml.example -> filegraph.yaml
    yaml_dst = cwd / "filegraph.yaml"
    if yaml_dst.exists():
        print(f"  ✓ filegraph.yaml already exists")
    else:
        yaml_src = _find_src("filegraph.yaml.example")
        if yaml_src:
            shutil.copy2(yaml_src, yaml_dst)
            print(f"  ✓ Created filegraph.yaml from template")
        else:
            print(f"  ✗ filegraph.yaml.example not found", file=sys.stderr)

    # Copy .env.example -> .env
    env_dst = cwd / ".env"
    if env_dst.exists():
        print(f"  ✓ .env already exists")
    else:
        env_src = _find_src(".env.example")
        if env_src:
            shutil.copy2(env_src, env_dst)
            print(f"  ✓ Created .env from template")
        else:
            print(f"  ✗ .env.example not found", file=sys.stderr)

    # Create cards/proposed/ directory
    cards_dir = repo / "cards"
    proposed_dir = cards_dir / "proposed"
    proposed_dir.mkdir(parents=True, exist_ok=True)
    print(f"  ✓ cards/ directory ready")

    # Check FTS5
    from filegraph.db import ensure_fts5
    ensure_fts5()
    print(f"  ✓ FTS5 is available")

    # Initialize DB
    from filegraph.db import connect, init_schema, check_schema_version
    settings = _load_settings(args, require_roots=False)
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)
    conn.close()
    print(f"  ✓ Database initialized at {settings.db_path}")

    print(f"\nNext steps:")
    print(f"  1. Edit filegraph.yaml — add your folders under 'roots:'")
    print(f"  2. Edit .env — add your API keys")
    print(f"  3. Run: python fg.py ingest")
    print(f"  4. Run: python fg.py extract")
    return 0


def cmd_roots(args: argparse.Namespace) -> int:
    """List configured roots with file counts."""
    settings = _load_settings(args)

    from filegraph.db import connect, init_schema, check_schema_version
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)

    print(f"{'Name':<20} {'LLM Policy':<15} {'Profile':<12} {'Files':<8} {'Path'}")
    print(f"{'─'*20} {'─'*15} {'─'*12} {'─'*8} {'─'*40}")

    for root in settings.roots:
        # Count files for this root
        row = conn.execute(
            "SELECT id FROM roots WHERE name = ?", (root.name,)
        ).fetchone()
        count = 0
        if row:
            cr = conn.execute(
                "SELECT COUNT(*) as n FROM files WHERE root_id = ? AND missing = 0",
                (row["id"],),
            ).fetchone()
            count = cr["n"] if cr else 0
        print(f"{root.name:<20} {root.llm:<15} {root.profile:<12} {count:<8} {root.path}")

    conn.close()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest roots into the index."""
    settings = _load_settings(args)

    from filegraph.db import connect, init_schema, check_schema_version
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)
    conn.close()

    from filegraph.ingest import ingest, inventory, print_inventory
    if not getattr(args, "quiet", False):
        print(f"Ingesting roots into {settings.db_path}...")
    stats = ingest(settings, root_name=getattr(args, "root", None), workers=getattr(args, "workers", None))

    if not getattr(args, "quiet", False):
        print(f"\nIngest complete: {stats['new']} new, {stats['changed']} changed, "
              f"{stats['skipped_unchanged']} unchanged, {stats['missing']} missing, "
              f"{stats['skipped_large']} large skipped, {stats['attachments']} attachments, "
              f"{stats['denied']} denied.")
        inv = inventory(settings)
        print_inventory(inv)

    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    """Show file inventory and extraction estimate."""
    settings = _load_settings(args)

    from filegraph.db import connect, init_schema, check_schema_version
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)
    conn.close()

    from filegraph.ingest import inventory, print_inventory
    inv = inventory(settings)
    print_inventory(inv)
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract structured cards from documents."""
    settings = _load_settings(args)

    from filegraph.db import connect, init_schema, check_schema_version
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)
    conn.close()

    from filegraph.extract import extract_sync
    stats = extract_sync(
        settings,
        limit=getattr(args, "limit", None),
        card_filter=getattr(args, "card", None),
        root_filter=getattr(args, "root", None),
        model_override=getattr(args, "model", None),
        refresh=getattr(args, "refresh", False),
        redo_low=getattr(args, "redo_low", False),
    )

    if not getattr(args, "quiet", False):
        print(f"\nExtraction complete: {stats['extracted']} extracted, {stats['skipped_cached']} cached, {stats['deterministic']} deterministic, {stats['errors']} errors.")

    return 0


def cmd_link(args: argparse.Namespace) -> int:
    """Resolve entities and build edges."""
    settings = _load_settings(args)

    from filegraph.db import connect, init_schema, check_schema_version
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)

    from filegraph.link import resolve_entities, build_edges
    if not getattr(args, "quiet", False):
        print("Resolving entities...")
    resolve_entities(conn, settings)

    if not getattr(args, "quiet", False):
        print("Building edges...")
    build_edges(conn)

    n_ent = conn.execute("SELECT COUNT(*) as n FROM entities").fetchone()["n"]
    n_doc_ent = conn.execute("SELECT COUNT(*) as n FROM doc_entities").fetchone()["n"]
    n_edges = conn.execute("SELECT COUNT(*) as n FROM edges").fetchone()["n"]
    conn.close()

    if not getattr(args, "quiet", False):
        print(f"\nLinking complete: {n_ent} entities, {n_doc_ent} mentions, {n_edges} edges.")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    """Generate interactive graph.html."""
    settings = _load_settings(args)

    from filegraph.db import connect, init_schema, check_schema_version
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)

    from filegraph.export import export_graph
    out_path = getattr(args, "out", None) or "graph.html"
    res = export_graph(conn, settings, out_path=out_path)
    conn.close()

    if not getattr(args, "quiet", False):
        print(f"Graph generated at: {res}")
    return 0


def cmd_vault(args: argparse.Namespace) -> int:
    """Export Obsidian vault."""
    settings = _load_settings(args)

    from filegraph.db import connect, init_schema, check_schema_version
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)

    from filegraph.export import export_vault
    out_dir = getattr(args, "dir", None) or "vault"
    res = export_vault(conn, settings, out_dir=out_dir)
    conn.close()

    if not getattr(args, "quiet", False):
        print(f"Obsidian vault exported to: {res}")
    return 0


def cmd_review_csv(args: argparse.Namespace) -> int:
    """Export review CSV."""
    settings = _load_settings(args)

    from filegraph.db import connect, init_schema, check_schema_version
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)

    from filegraph.export import export_review_csv
    out_path = getattr(args, "out", None) or "review.csv"
    res = export_review_csv(conn, settings, out_path=out_path)
    conn.close()

    if not getattr(args, "quiet", False):
        print(f"Review CSV exported to: {res}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Print summary report."""
    settings = _load_settings(args)

    from filegraph.db import connect, init_schema, check_schema_version
    conn = connect(settings.db_path)
    init_schema(conn)
    check_schema_version(conn)

    from filegraph.export import report
    rep = report(conn, settings)
    conn.close()
    print(rep)
    return 0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_settings(args: argparse.Namespace, require_roots: bool = True):
    """Load settings using CLI overrides."""
    from filegraph.config import load_settings
    return load_settings(
        config_path=getattr(args, "config", None),
        db_path=getattr(args, "db", None),
        require_roots=require_roots,
    )


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fg",
        description="FileGraph — local knowledge index for your files",
    )
    p.add_argument("--db", help="Path to the database file")
    p.add_argument("--config", help="Path to filegraph.yaml")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress non-essential output")

    sub = p.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="Initialize configuration files and database")

    # roots
    sub.add_parser("roots", help="List configured roots")

    # ingest (stub for now)
    p_ingest = sub.add_parser("ingest", help="Ingest files from roots")
    p_ingest.add_argument("--root", help="Ingest only this root")
    p_ingest.add_argument("--workers", type=int, help="Number of parallel workers")

    # inventory (stub)
    sub.add_parser("inventory", help="Show file inventory")

    # extract (stub)
    p_extract = sub.add_parser("extract", help="Extract cards from documents")
    p_extract.add_argument("--limit", type=int, help="Max documents to extract")
    p_extract.add_argument("--workers", type=int, help="Number of parallel workers")
    p_extract.add_argument("--model", help="Override model key")
    p_extract.add_argument("--root", help="Extract only this root")
    p_extract.add_argument("--card", help="Extract only this card type")
    p_extract.add_argument("--redo-low", action="store_true", help="Re-extract low confidence")
    p_extract.add_argument("--refresh", action="store_true", help="Re-route and re-extract")
    p_extract.add_argument("--yes", action="store_true", help="Skip confirmation")
    p_extract.add_argument("--dry-run", action="store_true", help="Print estimate only")
    p_extract.add_argument("--no-post", action="store_true", help="Skip post-extraction steps")

    # repos (stub)
    p_repos = sub.add_parser("repos", help="Manage repository metadata")
    p_repos.add_argument("action", nargs="?", default="list", choices=["list", "summarize"])
    p_repos.add_argument("--repo", help="Target repo name")
    p_repos.add_argument("--refresh", action="store_true")

    # embed (stub)
    p_embed = sub.add_parser("embed", help="Generate embeddings")
    p_embed.add_argument("--root", help="Embed only this root")
    p_embed.add_argument("--rebuild", action="store_true")

    # link (stub)
    sub.add_parser("link", help="Build entity and edge links")

    # run (stub)
    p_run = sub.add_parser("run", help="Full pipeline: ingest → extract → link → embed → export")
    p_run.add_argument("--root", help="Process only this root")
    p_run.add_argument("--limit", type=int, help="Max documents to extract")
    p_run.add_argument("--yes", action="store_true", help="Skip confirmation")

    # search (stub)
    p_search = sub.add_parser("search", help="Hybrid search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--kind", help="Filter by file kind")
    p_search.add_argument("--root", help="Filter by root")
    p_search.add_argument("--card", help="Filter by card type")
    p_search.add_argument("-n", type=int, default=10, help="Number of results")

    # ask (stub)
    p_ask = sub.add_parser("ask", help="Ask a question")
    p_ask.add_argument("question", help="Your question")
    p_ask.add_argument("-v", "--verbose", action="store_true")
    p_ask.add_argument("--allow-cloud", action="store_true")

    # chat (stub)
    p_chat = sub.add_parser("chat", help="Interactive chat")
    p_chat.add_argument("-v", "--verbose", action="store_true")
    p_chat.add_argument("--allow-cloud", action="store_true")

    # discover (stub)
    p_discover = sub.add_parser("discover", help="Discover new card types")
    p_discover.add_argument("--min", type=int, default=10)
    p_discover.add_argument("--all", action="store_true")

    # propose-card (stub)
    p_propose = sub.add_parser("propose-card", help="Propose a new card type")
    p_propose.add_argument("type", help="suggested_type value")
    p_propose.add_argument("--samples", type=int, default=15)
    p_propose.add_argument("--name", help="Card name override")

    # cards (stub)
    sub.add_parser("cards", help="List card types")

    # file (stub)
    p_file = sub.add_parser("file", help="Suggest a folder for a file")
    p_file.add_argument("path", help="Path to the file")
    p_file.add_argument("--root", help="Target root")
    p_file.add_argument("--apply", action="store_true")
    p_file.add_argument("--move", action="store_true")
    p_file.add_argument("--yes", action="store_true")

    # backtest (stub)
    p_bt = sub.add_parser("backtest", help="Backtest filing accuracy")
    p_bt.add_argument("-n", type=int, default=20)
    p_bt.add_argument("--root", help="Target root")
    p_bt.add_argument("--model", help="Model key")

    # report (stub)
    sub.add_parser("report", help="Print a summary report")

    # review-csv (stub)
    p_csv = sub.add_parser("review-csv", help="Export review CSV")
    p_csv.add_argument("out", nargs="?", help="Output path")

    # vault (stub)
    p_vault = sub.add_parser("vault", help="Export Obsidian vault")
    p_vault.add_argument("dir", nargs="?", help="Output directory")

    # graph (stub)
    p_graph = sub.add_parser("graph", help="Generate interactive graph.html")
    p_graph.add_argument("out", nargs="?", help="Output path")

    # mcp (stub)
    sub.add_parser("mcp", help="Start MCP server")

    # watch (stub)
    p_watch = sub.add_parser("watch", help="Watch for file changes")
    p_watch.add_argument("--interval", type=int, default=300)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Dispatch
    handler = {
        "init": cmd_init,
        "roots": cmd_roots,
        "ingest": cmd_ingest,
        "inventory": cmd_inventory,
        "extract": cmd_extract,
        "link": cmd_link,
        "graph": cmd_graph,
        "vault": cmd_vault,
        "review-csv": cmd_review_csv,
        "report": cmd_report,
    }.get(args.command)

    if handler:
        try:
            return handler(args)
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    # Stubs for unimplemented commands
    print(f"Command '{args.command}' is not yet implemented (coming in later phases).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
