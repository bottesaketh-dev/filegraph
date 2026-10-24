"""Filesystem watching and continuous pipeline refresh (Section 18.1).

Monitors registered root directories using watchdog (if available) with debounce,
or polls at a regular interval. Each cycle runs:
ingest (incremental) → extract → link → embed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from filegraph.config import Settings, load_settings
from filegraph.db import connect, now
from filegraph.embed import embed_units
from filegraph.export import export_graph, export_review_csv, export_vault
from filegraph.extract import extract_sync
from filegraph.ingest import ingest
from filegraph.link import link
from filegraph.repos import summarize_repo

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False


def run_pipeline_cycle(
    settings: Settings,
    root_filter: str | None = None,
    yes: bool = True,
) -> dict[str, Any]:
    """Execute one incremental cycle of ingest → extract → link → embed."""
    print("\n--- Running pipeline cycle ---")
    i_stats = ingest(settings, root_name=root_filter)
    print(f"Ingest: {i_stats['new']} new, {i_stats['changed']} changed, {i_stats['missing']} missing")

    e_stats = extract_sync(settings, root_filter=root_filter)
    print(f"Extract: {e_stats['extracted']} extracted, {e_stats['skipped_cached']} cached, {e_stats['deterministic']} deterministic")

    link(settings)
    print(f"Link: complete")

    conn = connect(settings.db_path)
    try:
        n_embedded = embed_units(conn, settings, root_name=root_filter)
        print(f"Embed: {n_embedded} units embedded")
    finally:
        conn.close()

    return {
        "ingest": i_stats,
        "extract": e_stats,
        "embedded": n_embedded,
    }


def run_full_pipeline(
    settings: Settings,
    root_filter: str | None = None,
    limit: int | None = None,
    yes: bool = False,
) -> dict[str, Any]:
    """Execute the full pipeline for fg run (Section 18).

    ingest → extract → repos summarize → link → embed → vault → graph → review-csv
    """
    print("\n=== Running FileGraph Full Pipeline ===")
    i_stats = ingest(settings, root_name=root_filter)
    print(f"✓ Ingest: {i_stats['new']} new, {i_stats['changed']} changed, {i_stats['missing']} missing")

    e_stats = extract_sync(settings, root_filter=root_filter, limit=limit)
    print(f"✓ Extract: {e_stats['extracted']} extracted, {e_stats['skipped_cached']} cached, {e_stats['deterministic']} deterministic")

    conn = connect(settings.db_path)
    try:
        repo_rows = conn.execute("SELECT name, summary FROM repos").fetchall()
        for r in repo_rows:
            if not r["summary"]:
                try:
                    summarize_repo(conn, r["name"], settings)
                    print(f"✓ Summarized repo: {r['name']}")
                except Exception as exc:
                    print(f"! Repo {r['name']} summary skipped: {exc}")

        link(settings)
        print(f"✓ Link complete")

        n_embedded = embed_units(conn, settings, root_name=root_filter)
        print(f"✓ Embed: {n_embedded} units embedded")

        v_path = export_vault(conn, settings)
        print(f"✓ Obsidian Vault exported to: {v_path}")

        g_path = export_graph(conn, settings)
        print(f"✓ Interactive Graph generated at: {g_path}")

        csv_path = export_review_csv(conn, settings)
        print(f"✓ Review CSV exported to: {csv_path}")

        stats_data = {
            "ingest": i_stats,
            "extract": e_stats,
            "embedded": n_embedded,
        }
        conn.execute(
            "INSERT INTO run_log (at, command, stats) VALUES (?, ?, ?)",
            (now(), "run", json.dumps(stats_data)),
        )
        conn.commit()
    finally:
        conn.close()

    return stats_data


class DebouncedChangeHandler(FileSystemEventHandler if WATCHDOG_AVAILABLE else object):  # type: ignore
    def __init__(self, debounce_sec: float = 30.0):
        super().__init__()
        self.debounce_sec = debounce_sec
        self.last_event_time = 0.0
        self.has_changes = False

    def on_any_event(self, event: Any) -> None:
        if getattr(event, "is_directory", False):
            return
        self.last_event_time = time.time()
        self.has_changes = True


def watch_roots(
    settings: Settings,
    interval: int = 300,
    debounce: float = 30.0,
    once: bool = False,
) -> None:
    """Watch configured root folders and re-run pipeline on changes (Section 18.1)."""
    if once:
        run_pipeline_cycle(settings, yes=True)
        return

    if WATCHDOG_AVAILABLE:
        print(f"Starting watchdog observer for {len(settings.roots)} root(s) (debounce: {debounce}s)...")
        handler = DebouncedChangeHandler(debounce_sec=debounce)
        observer = Observer()

        for r_cfg in settings.roots:
            r_path = Path(r_cfg.path)
            if r_path.is_dir():
                observer.schedule(handler, str(r_path), recursive=True)
                print(f"  Watching: {r_cfg.name} -> {r_path}")

        observer.start()
        print("Watcher running. Press Ctrl+C to stop.")

        try:
            while True:
                time.sleep(1.0)
                if handler.has_changes and (time.time() - handler.last_event_time) >= debounce:
                    handler.has_changes = False
                    run_pipeline_cycle(settings, yes=True)
        except KeyboardInterrupt:
            print("\nStopping observer...")
            observer.stop()
        observer.join()

    else:
        print(f"watchdog package not installed; falling back to polling every {interval}s.")
        print("Polling active. Press Ctrl+C to stop.")
        try:
            while True:
                run_pipeline_cycle(settings, yes=True)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nPolling stopped.")
