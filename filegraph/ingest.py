"""Tier 0 and Tier 1 ingestion, file parsing, and inventory (Section 7)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from filegraph.config import RootConfig, Settings
from filegraph.db import connect, now
from filegraph.parse import classify_kind, parse_file
from filegraph.repos import update_repos
from filegraph.security import redact
from filegraph.walk import find_enclosing_repo, walk_root


# ---------------------------------------------------------------------------
# Picklable top-level worker for ProcessPoolExecutor (Section 7.3 item 4)
# ---------------------------------------------------------------------------

def parse_job(path_str: str, kind: str) -> tuple[str, str | None, dict[str, Any]]:
    """Worker function to compute sha256 and parse a file."""
    path = Path(path_str)
    sha256: str | None = None
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        sha256 = h.hexdigest()
    except Exception:
        sha256 = None

    result = parse_file(path, kind)
    return path_str, sha256, result


# ---------------------------------------------------------------------------
# Attachment storage (Section 7.3 item 6)
# ---------------------------------------------------------------------------

def _store_attachments(
    conn: Any,
    parent_row: dict[str, Any],
    attachments: list[tuple[str, bytes]],
    settings: Settings,
    depth: int = 1,
) -> int:
    """Store email attachments under attachments_dir and index them (depth <= 2)."""
    if depth > 2 or not attachments:
        return 0

    count = 0
    att_dir = settings.attachments_dir
    parent_id = parent_row["id"]
    parent_rel = parent_row["rel_path"]
    root_id = parent_row["root_id"]
    parent_folder = parent_row["folder"]

    for name, data in attachments:
        try:
            sha = hashlib.sha256(data).hexdigest()
            safe_name = re.sub(r"[^\w\.-]", "_", Path(name).name)
            if not safe_name:
                safe_name = f"attachment_{sha[:8]}"

            # Cache path: attachments_dir/<sha[:16]>/<safe name>
            cache_folder = att_dir / sha[:16]
            cache_folder.mkdir(parents=True, exist_ok=True)
            cache_path = cache_folder / safe_name
            with open(cache_path, "wb") as f:
                f.write(data)

            rel_path = f"{parent_rel}::{safe_name}"
            ext = cache_path.suffix.lower()
            kind = classify_kind(ext, safe_name)

            # Parse cached attachment
            res = parse_file(cache_path, kind)
            parse_status = "ok" if res.get("ok") else "error"
            if res.get("unsupported"):
                parse_status = "unsupported"

            tier = 1 if (res.get("text_chars", 0) > 0 or res.get("is_scanned") == 1) else 0

            # Upsert into files
            conn.execute(
                """
                INSERT INTO files (
                    root_id, rel_path, folder, filename, ext, kind,
                    size, mtime, sha256, source, cache_path, parent_id,
                    tier, page_count, is_scanned, scan_pages, text_chars,
                    flags, email_meta, parse_status, parse_error, missing, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'attachment', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(root_id, rel_path) DO UPDATE SET
                    size = excluded.size,
                    sha256 = excluded.sha256,
                    cache_path = excluded.cache_path,
                    tier = excluded.tier,
                    page_count = excluded.page_count,
                    is_scanned = excluded.is_scanned,
                    scan_pages = excluded.scan_pages,
                    text_chars = excluded.text_chars,
                    flags = excluded.flags,
                    email_meta = excluded.email_meta,
                    parse_status = excluded.parse_status,
                    parse_error = excluded.parse_error,
                    missing = 0,
                    ingested_at = excluded.ingested_at
                """,
                (
                    root_id,
                    rel_path,
                    parent_folder,
                    safe_name,
                    ext,
                    kind,
                    len(data),
                    cache_path.stat().st_mtime,
                    sha,
                    str(cache_path),
                    parent_id,
                    tier,
                    res.get("page_count", 0),
                    res.get("is_scanned", 0),
                    json.dumps(res.get("scan_pages", [])),
                    res.get("text_chars", 0),
                    json.dumps(res.get("flags", {})),
                    json.dumps(res["email_meta"]) if res.get("email_meta") else None,
                    parse_status,
                    res.get("error"),
                    now(),
                ),
            )

            file_row = conn.execute(
                "SELECT id FROM files WHERE root_id = ? AND rel_path = ?",
                (root_id, rel_path),
            ).fetchone()
            if not file_row:
                continue

            att_file_id = file_row["id"]
            count += 1

            # Insert pages & FTS
            conn.execute("DELETE FROM pages WHERE file_id = ?", (att_file_id,))
            conn.execute("DELETE FROM fts WHERE file_id = ?", (att_file_id,))

            total_chars = 0
            max_chars = settings.limits.max_text_chars_per_file
            for p_num, p_text in res.get("pages", []):
                if total_chars >= max_chars:
                    break
                raw_text = p_text[: max_chars - total_chars]
                allowed_text, _ = redact(raw_text)
                total_chars += len(allowed_text)
                lbl = res.get("labels", {}).get(p_num, f"Page {p_num}")
                sl, el = res.get("lines", {}).get(p_num, (None, None))
                conn.execute(
                    "INSERT INTO pages (file_id, page, label, start_line, end_line, text) VALUES (?, ?, ?, ?, ?, ?)",
                    (att_file_id, p_num, lbl, sl, el, allowed_text),
                )
                conn.execute(
                    "INSERT INTO fts (file_id, page, path, label, text) VALUES (?, ?, ?, ?, ?)",
                    (att_file_id, p_num, rel_path, lbl, allowed_text),
                )

            # Recurse if attachment has nested attachments
            if res.get("attachments") and depth < 2:
                sub_row = {
                    "id": att_file_id,
                    "rel_path": rel_path,
                    "root_id": root_id,
                    "folder": parent_folder,
                }
                count += _store_attachments(conn, sub_row, res["attachments"], settings, depth=depth + 1)

        except Exception as exc:
            print(f"Warning: failed to store attachment {name}: {exc}", file=sys.stderr)

    return count


# ---------------------------------------------------------------------------
# Main Ingestion Algorithm (Section 7.3)
# ---------------------------------------------------------------------------

def ingest(
    settings: Settings,
    root_name: str | None = None,
    workers: int | None = None,
) -> dict[str, Any]:
    """Ingest configured roots (or one) into SQLite database (Section 7.3)."""
    conn = connect(settings.db_path)

    stats: dict[str, Any] = {
        "roots": 0,
        "scanned": 0,
        "new": 0,
        "changed": 0,
        "skipped_unchanged": 0,
        "skipped_large": 0,
        "missing": 0,
        "denied": 0,
        "attachments": 0,
        "repos": 0,
        "errors": 0,
    }

    # Upsert roots into DB
    for r in settings.roots:
        conn.execute(
            """
            INSERT INTO roots (path, name, profile, llm_policy, added_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                name = excluded.name,
                profile = excluded.profile,
                llm_policy = excluded.llm_policy
            """,
            (str(r.path.resolve()), r.name, r.profile, r.llm, now()),
        )
    conn.commit()

    # Determine which roots to process
    target_roots = [r for r in settings.roots if not root_name or r.name == root_name]
    num_workers = workers if workers is not None else min(8, max(1, (os.cpu_count() or 2) - 1))

    total_denied_files: list[Path] = []

    for rcfg in target_roots:
        stats["roots"] += 1
        root_path = rcfg.path.resolve()

        if not root_path.exists():
            print(f"Warning: root path missing: {root_path}; skipping without marking missing.", file=sys.stderr)
            continue

        row = conn.execute("SELECT id FROM roots WHERE name = ?", (rcfg.name,)).fetchone()
        if not row:
            continue
        root_id = row["id"]

        # 1. Load known disk files: {rel_path: (id, size, mtime)}
        known_rows = conn.execute(
            "SELECT id, rel_path, size, mtime FROM files WHERE root_id = ? AND source = 'disk' AND missing = 0",
            (root_id,),
        ).fetchall()
        known: dict[str, tuple[int, int, float]] = {
            r["rel_path"]: (r["id"], r["size"], r["mtime"]) for r in known_rows
        }

        # Walk root
        root_denied: list[Path] = []
        root_repos: list[Path] = []
        seen_rel_paths: set[str] = set()
        todo: list[tuple[Path, str, str, Path | None]] = []  # (path, rel_path, kind, repo_dir)

        max_bytes = settings.limits.max_file_mb * 1024 * 1024

        for file_p in walk_root(root_path, settings, denied=root_denied, repos=root_repos):
            try:
                rel = file_p.relative_to(root_path).as_posix()
            except ValueError:
                continue

            seen_rel_paths.add(rel)
            stats["scanned"] += 1

            enclosing_repo = find_enclosing_repo(file_p, root_path)
            inside_repo = enclosing_repo is not None
            kind = classify_kind(file_p.suffix, file_p.name, inside_repo=inside_repo)

            # Check if unchanged
            try:
                st = file_p.stat()
                f_size = st.st_size
                f_mtime = st.st_mtime
            except OSError:
                continue

            if rel in known:
                _, k_size, k_mtime = known[rel]
                if k_size == f_size and abs(k_mtime - f_mtime) < 1.0:
                    stats["skipped_unchanged"] += 1
                    continue
                else:
                    stats["changed"] += 1
            else:
                stats["new"] += 1

            todo.append((file_p, rel, kind, enclosing_repo))

        total_denied_files.extend(root_denied)
        stats["denied"] += len(root_denied)

        # 2. Mark files no longer seen as missing=1
        missing_rel_paths = set(known.keys()) - seen_rel_paths
        if missing_rel_paths:
            for m_rel in missing_rel_paths:
                m_id, _, _ = known[m_rel]
                conn.execute("UPDATE files SET missing = 1 WHERE id = ?", (m_id,))
                # Also mark child attachments missing
                conn.execute("UPDATE files SET missing = 1 WHERE parent_id = ?", (m_id,))
                stats["missing"] += 1
            conn.commit()

        # Update repos in DB for this root
        repo_id_map = update_repos(conn, root_id, root_path, root_repos)
        stats["repos"] += len(root_repos)
        conn.commit()

        # 3 & 4. Process todo items
        large_files = []
        regular_files = []

        for item in todo:
            fp, rel, kind, repo_dir = item
            try:
                if fp.stat().st_size > max_bytes:
                    large_files.append(item)
                else:
                    regular_files.append(item)
            except OSError:
                continue

        # Handle large files (Section 7.3 item 3)
        for fp, rel, kind, repo_dir in large_files:
            stats["skipped_large"] += 1
            st = fp.stat()
            repo_id = None
            if repo_dir:
                r_rel = repo_dir.relative_to(root_path).as_posix()
                repo_id = repo_id_map.get(r_rel if r_rel != "" else ".")

            folder = Path(rel).parent.as_posix()
            conn.execute(
                """
                INSERT INTO files (
                    root_id, rel_path, folder, filename, ext, kind,
                    size, mtime, sha256, source, repo_id,
                    tier, parse_status, missing, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'disk', ?, 0, 'skipped_large', 0, ?)
                ON CONFLICT(root_id, rel_path) DO UPDATE SET
                    size = excluded.size,
                    mtime = excluded.mtime,
                    sha256 = NULL,
                    tier = 0,
                    parse_status = 'skipped_large',
                    missing = 0,
                    ingested_at = excluded.ingested_at
                """,
                (
                    root_id,
                    rel,
                    folder,
                    fp.name,
                    fp.suffix.lower(),
                    kind,
                    st.st_size,
                    st.st_mtime,
                    repo_id,
                    now(),
                ),
            )
        conn.commit()

        # Parse regular files
        parsed_results: list[tuple[Path, str, str, Path | None, str | None, dict[str, Any]]] = []

        if num_workers > 1 and len(regular_files) > 1:
            with ProcessPoolExecutor(max_workers=num_workers) as executor:
                future_map = {
                    executor.submit(parse_job, str(fp), kind): (fp, rel, kind, repo_dir)
                    for fp, rel, kind, repo_dir in regular_files
                }
                for fut in as_completed(future_map):
                    fp, rel, kind, repo_dir = future_map[fut]
                    try:
                        _, sha256, res = fut.result()
                        parsed_results.append((fp, rel, kind, repo_dir, sha256, res))
                    except Exception as exc:
                        stats["errors"] += 1
                        parsed_results.append((
                            fp, rel, kind, repo_dir, None,
                            {"ok": False, "error": str(exc), "pages": []},
                        ))
        else:
            for fp, rel, kind, repo_dir in regular_files:
                try:
                    _, sha256, res = parse_job(str(fp), kind)
                    parsed_results.append((fp, rel, kind, repo_dir, sha256, res))
                except Exception as exc:
                    stats["errors"] += 1
                    parsed_results.append((
                        fp, rel, kind, repo_dir, None,
                        {"ok": False, "error": str(exc), "pages": []},
                    ))

        # 5. Store files, pages, and FTS rows
        commit_counter = 0
        for fp, rel, kind, repo_dir, sha256, res in parsed_results:
            st = fp.stat()
            repo_id = None
            if repo_dir:
                r_rel = repo_dir.relative_to(root_path).as_posix()
                repo_id = repo_id_map.get(r_rel if r_rel != "" else ".")

            folder = Path(rel).parent.as_posix()
            parse_status = "ok" if res.get("ok") else "error"
            if res.get("unsupported"):
                parse_status = "unsupported"

            tier = 1 if (res.get("text_chars", 0) > 0 or res.get("is_scanned") == 1) else 0

            conn.execute(
                """
                INSERT INTO files (
                    root_id, rel_path, folder, filename, ext, kind,
                    size, mtime, sha256, source, repo_id, language,
                    tier, page_count, is_scanned, scan_pages, text_chars,
                    flags, email_meta, parse_status, parse_error, missing, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'disk', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(root_id, rel_path) DO UPDATE SET
                    folder = excluded.folder,
                    filename = excluded.filename,
                    ext = excluded.ext,
                    kind = excluded.kind,
                    size = excluded.size,
                    mtime = excluded.mtime,
                    sha256 = excluded.sha256,
                    repo_id = excluded.repo_id,
                    language = excluded.language,
                    tier = excluded.tier,
                    page_count = excluded.page_count,
                    is_scanned = excluded.is_scanned,
                    scan_pages = excluded.scan_pages,
                    text_chars = excluded.text_chars,
                    flags = excluded.flags,
                    email_meta = excluded.email_meta,
                    parse_status = excluded.parse_status,
                    parse_error = excluded.parse_error,
                    missing = 0,
                    ingested_at = excluded.ingested_at
                """,
                (
                    root_id,
                    rel,
                    folder,
                    fp.name,
                    fp.suffix.lower(),
                    kind,
                    st.st_size,
                    st.st_mtime,
                    sha256,
                    repo_id,
                    res.get("language"),
                    tier,
                    res.get("page_count", 0),
                    res.get("is_scanned", 0),
                    json.dumps(res.get("scan_pages", [])),
                    res.get("text_chars", 0),
                    json.dumps(res.get("flags", {})),
                    json.dumps(res["email_meta"]) if res.get("email_meta") else None,
                    parse_status,
                    res.get("error"),
                    now(),
                ),
            )

            file_row = conn.execute(
                "SELECT id FROM files WHERE root_id = ? AND rel_path = ?",
                (root_id, rel),
            ).fetchone()
            if not file_row:
                continue

            file_id = file_row["id"]

            # Replace pages and FTS
            conn.execute("DELETE FROM pages WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM fts WHERE file_id = ?", (file_id,))

            total_chars = 0
            max_chars = settings.limits.max_text_chars_per_file
            for p_num, p_text in res.get("pages", []):
                if total_chars >= max_chars:
                    break
                raw_text = p_text[: max_chars - total_chars]
                allowed_text, _ = redact(raw_text)
                total_chars += len(allowed_text)
                lbl = res.get("labels", {}).get(p_num, f"Page {p_num}")
                sl, el = res.get("lines", {}).get(p_num, (None, None))
                conn.execute(
                    "INSERT INTO pages (file_id, page, label, start_line, end_line, text) VALUES (?, ?, ?, ?, ?, ?)",
                    (file_id, p_num, lbl, sl, el, allowed_text),
                )
                conn.execute(
                    "INSERT INTO fts (file_id, page, path, label, text) VALUES (?, ?, ?, ?, ?)",
                    (file_id, p_num, rel, lbl, allowed_text),
                )

            # Insert symbols and imports for code files (Section 7.3 item 7)
            if res.get("symbols"):
                conn.execute("DELETE FROM code_symbols WHERE file_id = ?", (file_id,))
                for sym in res["symbols"]:
                    conn.execute(
                        """
                        INSERT INTO code_symbols (file_id, repo_id, name, qualname, kind, start_line, end_line, signature, docstring)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            file_id,
                            repo_id,
                            sym["name"],
                            sym["qualname"],
                            sym["kind"],
                            sym["start_line"],
                            sym["end_line"],
                            sym["signature"],
                            sym["docstring"],
                        ),
                    )

            if res.get("imports"):
                conn.execute("DELETE FROM code_imports WHERE file_id = ?", (file_id,))
                for imp in res["imports"]:
                    conn.execute(
                        "INSERT INTO code_imports (file_id, module) VALUES (?, ?)",
                        (file_id, imp["module"]),
                    )

            # 6. Email attachments
            if res.get("attachments"):
                parent_info = {
                    "id": file_id,
                    "rel_path": rel,
                    "root_id": root_id,
                    "folder": folder,
                }
                att_count = _store_attachments(conn, parent_info, res["attachments"], settings)
                stats["attachments"] += att_count

            commit_counter += 1
            if commit_counter % 200 == 0:
                conn.commit()

        conn.commit()

    # 10. Write run_log row (Section 7.3 item 10)
    conn.execute(
        "INSERT INTO run_log (at, command, stats) VALUES (?, ?, ?)",
        (now(), "ingest", json.dumps(stats)),
    )
    conn.commit()
    conn.close()

    return stats


# ---------------------------------------------------------------------------
# Inventory (Section 7.5)
# ---------------------------------------------------------------------------

def inventory(settings: Settings) -> dict[str, Any]:
    """Compile inventory breakdown and extraction estimate (Section 7.5)."""
    conn = connect(settings.db_path)

    inv: dict[str, Any] = {
        "roots": {},
        "totals": {
            "files": 0,
            "attachments": 0,
            "duplicates": 0,
            "large_skipped": 0,
            "repos": 0,
            "scanned_docs": 0,
            "errors": 0,
        },
        "estimate": {
            "pending_files": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "images": 0,
        },
    }

    # Per root breakdown
    roots_rows = conn.execute("SELECT id, name, path FROM roots").fetchall()
    for r in roots_rows:
        r_id = r["id"]
        r_name = r["name"]

        breakdown = conn.execute(
            """
            SELECT kind, ext,
                   COUNT(*) as files,
                   SUM(is_scanned) as scanned,
                   SUM(page_count) as pages,
                   SUM(CASE WHEN parse_status = 'error' THEN 1 ELSE 0 END) as errors
            FROM files
            WHERE root_id = ? AND missing = 0
            GROUP BY kind, ext
            ORDER BY kind, ext
            """,
            (r_id,),
        ).fetchall()

        inv["roots"][r_name] = [
            {
                "kind": row["kind"],
                "ext": row["ext"],
                "files": row["files"],
                "scanned": row["scanned"] or 0,
                "pages": row["pages"] or 0,
                "errors": row["errors"] or 0,
            }
            for row in breakdown
        ]

    # Overall totals
    f_tot = conn.execute("SELECT COUNT(*) as n FROM files WHERE missing = 0").fetchone()
    inv["totals"]["files"] = f_tot["n"] if f_tot else 0

    att_tot = conn.execute("SELECT COUNT(*) as n FROM files WHERE source = 'attachment' AND missing = 0").fetchone()
    inv["totals"]["attachments"] = att_tot["n"] if att_tot else 0

    # Exact duplicates: files where sha256 matches an earlier file id
    dup_tot = conn.execute(
        """
        SELECT COUNT(*) as n FROM files f
        WHERE f.missing = 0 AND f.sha256 IS NOT NULL
          AND f.id > (SELECT MIN(f2.id) FROM files f2 WHERE f2.sha256 = f.sha256 AND f2.missing = 0)
        """
    ).fetchone()
    inv["totals"]["duplicates"] = dup_tot["n"] if dup_tot else 0

    large_tot = conn.execute(
        "SELECT COUNT(*) as n FROM files WHERE parse_status = 'skipped_large' AND missing = 0"
    ).fetchone()
    inv["totals"]["large_skipped"] = large_tot["n"] if large_tot else 0

    repo_tot = conn.execute("SELECT COUNT(*) as n FROM repos WHERE missing = 0").fetchone()
    inv["totals"]["repos"] = repo_tot["n"] if repo_tot else 0

    # Extraction estimate (Section 11.7)
    pending_rows = conn.execute(
        """
        SELECT f.id, f.sha256, f.text_chars, f.page_count, f.is_scanned, f.scan_pages
        FROM files f
        JOIN roots r ON r.id = f.root_id
        WHERE f.missing = 0
          AND f.parse_status = 'ok'
          AND f.kind IN ('document', 'email', 'data', 'image')
          AND f.repo_id IS NULL
          AND (f.text_chars > 0 OR f.is_scanned = 1)
          AND r.llm_policy != 'metadata_only'
          AND f.id = (
              SELECT MIN(f2.id) FROM files f2
              WHERE f2.sha256 = f.sha256 AND f2.missing = 0 AND f2.parse_status = 'ok'
          )
        """
    ).fetchall()

    inv["estimate"]["pending_files"] = len(pending_rows)
    tot_in = 0
    tot_out = 0
    tot_img = 0

    for prow in pending_rows:
        chars = prow["text_chars"] or 0
        scan_pages = json.loads(prow["scan_pages"] or "[]")
        num_images = min(8, len(scan_pages))
        tot_img += num_images

        # chars / 4 + images * 1500 + 2500 overhead
        in_tok = (chars // 4) + (num_images * 1500) + 2500
        out_tok = 900
        tot_in += in_tok
        tot_out += out_tok

    inv["estimate"]["input_tokens"] = tot_in
    inv["estimate"]["output_tokens"] = tot_out
    inv["estimate"]["images"] = tot_img

    conn.close()
    return inv


def print_inventory(inv: dict[str, Any]) -> None:
    """Print formatted inventory tables to stdout."""
    print("\nFileGraph Inventory")
    print("=" * 70)

    for r_name, items in inv["roots"].items():
        print(f"\nRoot: {r_name}")
        print(f"{'Kind':<12} {'Ext':<10} {'Files':<8} {'Scanned':<8} {'Pages':<8} {'Errors'}")
        print(f"{'─'*12} {'─'*10} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")
        for item in items:
            print(
                f"{item['kind']:<12} {item['ext']:<10} {item['files']:<8} "
                f"{item['scanned']:<8} {item['pages']:<8} {item['errors']}"
            )

    totals = inv["totals"]
    print("\nSummary")
    print("─" * 40)
    print(f"Total files on disk:     {totals['files']}")
    print(f"Email attachments:       {totals['attachments']}")
    print(f"Exact duplicates:        {totals['duplicates']}")
    print(f"Large files skipped:     {totals['large_skipped']}")
    print(f"Repositories found:      {totals['repos']}")

    est = inv["estimate"]
    print("\nExtraction Estimate (Tier 2)")
    print("─" * 40)
    print(f"Pending documents:       {est['pending_files']}")
    print(f"Image pages to process:  {est['images']}")
    print(f"Estimated input tokens:  {est['input_tokens']:,}")
    print(f"Estimated output tokens: {est['output_tokens']:,}")
