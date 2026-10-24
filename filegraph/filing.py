"""Filing and backtesting (Section 17).

Suggests destination folders for unfiled documents based on entity clustering,
FTS matching, folder profiles, and LLM reasoning.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from filegraph.cards import load_registry
from filegraph.config import Settings, load_settings
from filegraph.db import connect, now
from filegraph.link import norm_key

# Tool choose_folder schema (Appendix A.3 & Section 17)
TOOL_CHOOSE_FOLDER = {
    "name": "choose_folder",
    "description": "Choose the most appropriate folder to file a new document into.",
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {
                "type": "string",
                "description": "The chosen folder path relative to the root (e.g. 'car' or 'billing/invoices'), or a new folder path if no existing folder fits.",
            },
            "is_new_folder": {
                "type": "boolean",
                "description": "True if this is a newly proposed folder, False if it is an existing folder.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score between 0.0 and 1.0. Below 0.6 means human review is advised.",
            },
            "reasoning": {
                "type": "string",
                "description": "1-2 sentences explaining why this folder was chosen.",
            },
            "alternatives": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 3 alternative existing folders.",
            },
        },
        "required": ["folder", "is_new_folder", "confidence", "reasoning", "alternatives"],
    },
}

SYSTEM_PROMPT = """\
You file documents into the user's folders. The folder tree grew organically, so conventions differ between branches.
Decide where a new document belongs by:
1. Keeping related documents together: if the document amends, renews, replies to, or is issued under something already filed, prefer that document's folder (or its sibling subfolder that holds such documents, if that is the convention there).
2. Otherwise follow where documents with the same main entity and of the same kind already live.
3. Follow the local conventions visible in the folder profiles and sibling folders (year subfolders, per-entity folders, naming style).
4. Propose a NEW folder only if no existing folder fits; make it consistent with its would-be siblings.
Use folder paths exactly as written. Be honest about confidence: below 0.6 means a human should decide.
"""


def match_entity(conn: Any, kind: str, name: str) -> dict[str, Any] | None:
    """Find an entity in the database matching name and kind (Section 17)."""
    if not name:
        return None
    key = norm_key(name, kind or "organization")
    row = conn.execute(
        "SELECT id, kind, name, norm_key FROM entities WHERE norm_key = ?", (key,)
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        "SELECT id, kind, name, norm_key FROM entities WHERE lower(name) = lower(?)", (name.strip(),)
    ).fetchone()
    if row:
        return dict(row)
    return None


def candidates(
    conn: Any,
    target_root_name: str,
    doc_info: dict[str, Any],
    exclude_file_ids: set[int] | None = None,
) -> list[tuple[str, float]]:
    """Score existing folders in target_root_name as candidates for filing (Section 17).

    Scoring:
    - entity's folders ×3 share
    - FTS/keyword on title / main entity / suggested type words
    - folder-name contains entity first word
    - fallback by card_type + category
    Restricted to target root, never inside repos.
    """
    exclude_file_ids = exclude_file_ids or set()

    # Get all non-repo folders in target root
    query = """
        SELECT DISTINCT f.folder
        FROM files f
        JOIN roots r ON r.id = f.root_id
        WHERE r.name = ?
          AND f.missing = 0
          AND f.repo_id IS NULL
    """
    rows = conn.execute(query, (target_root_name,)).fetchall()
    all_folders = [r["folder"] or "" for r in rows]

    if not all_folders:
        return [("", 0.0)]

    # Collect documents in target root (excluding exclude_file_ids)
    doc_query = """
        SELECT f.id as file_id, f.folder, d.card_type, d.category, d.main_entity_name,
               d.main_entity_id, d.title, d.suggested_type
        FROM files f
        JOIN roots r ON r.id = f.root_id
        JOIN documents d ON d.file_id = f.id
        WHERE r.name = ?
          AND f.missing = 0
          AND f.repo_id IS NULL
    """
    all_docs = conn.execute(doc_query, (target_root_name,)).fetchall()
    active_docs = [d for d in all_docs if d["file_id"] not in exclude_file_ids]

    main_ent_name = (doc_info.get("main_entity_name") or "").strip()
    main_ent_key = norm_key(main_ent_name, "organization") if main_ent_name else ""
    target_card_type = doc_info.get("card_type")
    target_category = doc_info.get("category")
    title = doc_info.get("title") or ""
    sug_type = doc_info.get("suggested_type") or ""

    # Words for textual matching
    words = set(re.findall(r"[a-zA-Z0-9]{3,}", f"{title} {main_ent_name} {sug_type}".lower()))

    # First word of entity
    ent_tokens = re.findall(r"[a-zA-Z0-9]+", main_ent_name.lower())
    first_ent_word = ent_tokens[0] if ent_tokens and len(ent_tokens[0]) >= 3 and ent_tokens[0] not in {"the", "and"} else ""

    # Entity counts across target root
    entity_docs_total = 0
    entity_docs_per_folder: Counter[str] = Counter()
    card_type_per_folder: Counter[str] = Counter()
    cat_per_folder: Counter[str] = Counter()
    total_docs_per_folder: Counter[str] = Counter()

    for d in active_docs:
        fld = d["folder"] or ""
        total_docs_per_folder[fld] += 1

        d_ent = (d["main_entity_name"] or "").strip()
        d_key = norm_key(d_ent, "organization") if d_ent else ""
        if (main_ent_key and d_key == main_ent_key) or (main_ent_name and main_ent_name.lower() in d_ent.lower()):
            entity_docs_total += 1
            entity_docs_per_folder[fld] += 1

        if target_card_type and d["card_type"] == target_card_type:
            card_type_per_folder[fld] += 1

        if target_category and d["category"] == target_category:
            cat_per_folder[fld] += 1

    scores: dict[str, float] = {}

    for folder in all_folders:
        score = 0.0

        # 1. Entity's folders ×3 share
        if entity_docs_total > 0 and entity_docs_per_folder[folder] > 0:
            share = entity_docs_per_folder[folder] / entity_docs_total
            score += 3.0 * share

        # 2. Text / keyword matches in this folder's document titles / types
        folder_matches = 0
        for d in active_docs:
            if (d["folder"] or "") == folder:
                d_text = f"{d['title'] or ''} {d['main_entity_name'] or ''} {d['suggested_type'] or ''}".lower()
                for w in words:
                    if w in d_text:
                        folder_matches += 1
        if folder_matches > 0:
            score += 0.5 * min(folder_matches, 4)

        # 3. Folder name contains entity first word
        if first_ent_word and first_ent_word in folder.lower():
            score += 1.5

        # 4. Fallback by card_type + category
        total_in_folder = total_docs_per_folder[folder]
        if total_in_folder > 0:
            if target_card_type and card_type_per_folder[folder] > 0:
                score += 1.0 * (card_type_per_folder[folder] / total_in_folder)
            if target_category and cat_per_folder[folder] > 0:
                score += 0.5 * (cat_per_folder[folder] / total_in_folder)

        scores[folder] = score

    sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_candidates


def folder_profile(
    conn: Any,
    target_root_name: str,
    folder: str,
    exclude_file_ids: set[int] | None = None,
) -> str:
    """Generate a descriptive profile of an existing folder (Section 17).

    Includes counts by main entity, card type/category, date range of main_date, and example filenames.
    """
    exclude_file_ids = exclude_file_ids or set()
    query = """
        SELECT f.id as file_id, f.filename, d.card_type, d.category,
               COALESCE(e.name, d.main_entity_name) as main_entity,
               d.main_date
        FROM files f
        JOIN roots r ON r.id = f.root_id
        JOIN documents d ON d.file_id = f.id
        LEFT JOIN entities e ON e.id = d.main_entity_id
        WHERE r.name = ?
          AND (f.folder = ? OR (? = '' AND (f.folder IS NULL OR f.folder = '')))
          AND f.missing = 0
          AND f.repo_id IS NULL
    """
    rows = conn.execute(query, (target_root_name, folder, folder)).fetchall()
    active_rows = [r for r in rows if r["file_id"] not in exclude_file_ids]

    if not active_rows:
        return f'Folder: "{folder}" (empty / no documents yet)'

    total_files = len(active_rows)
    entities = Counter([r["main_entity"] for r in active_rows if r["main_entity"]])
    types = Counter([f"{r['card_type']}:{r['category']}" if r["category"] else r["card_type"] for r in active_rows if r["card_type"]])
    dates = [r["main_date"] for r in active_rows if r["main_date"]]
    filenames = [r["filename"] for r in active_rows[:3]]

    ent_summary = ", ".join(f"{k} ({v})" for k, v in entities.most_common(3)) or "none"
    types_summary = ", ".join(f"{k} ({v})" for k, v in types.most_common(3)) or "none"

    if dates:
        date_range = f"{min(dates)} to {max(dates)}"
    else:
        date_range = "no dates"

    examples_str = ", ".join(filenames)

    folder_display = folder if folder else "(root directory)"
    return (
        f'Folder: "{folder_display}" ({total_files} files)\n'
        f"  Main entities: {ent_summary}\n"
        f"  Types: {types_summary}\n"
        f"  Date range: {date_range}\n"
        f"  Examples: {examples_str}"
    )


def extract_doc_info_for_path(
    path: Path,
    settings: Settings,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Extract or retrieve card information for a file path (cached by sha256)."""
    close_conn = False
    if conn is None:
        conn = connect(settings.db_path)
        close_conn = True

    try:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        sha = h.hexdigest()

        # Check if already in documents
        row = conn.execute(
            """
            SELECT d.card_type, d.category, d.title, d.summary, d.status,
                   COALESCE(e.name, d.main_entity_name) as main_entity,
                   d.main_entity_name, d.main_date, d.main_date_label,
                   d.confidence, d.suggested_type, d.extras, d.refs
            FROM files f
            JOIN documents d ON d.file_id = f.id
            LEFT JOIN entities e ON e.id = d.main_entity_id
            WHERE f.sha256 = ?
            LIMIT 1
            """,
            (sha,),
        ).fetchone()

        if row:
            return {
                "card_type": row["card_type"],
                "category": row["category"],
                "title": row["title"],
                "summary": row["summary"],
                "status": row["status"],
                "main_entity_name": row["main_entity"] or row["main_entity_name"],
                "main_date": row["main_date"],
                "main_date_label": row["main_date_label"],
                "confidence": row["confidence"],
                "suggested_type": row["suggested_type"],
                "sha256": sha,
            }

        # Parse file live
        from filegraph.parse import classify_kind, parse_file
        kind = classify_kind(path.suffix, filename=path.name)
        p_res = parse_file(path, kind)
        if not p_res.get("ok"):
            return {
                "card_type": "generic",
                "category": "unknown",
                "title": path.stem,
                "summary": f"Parse status: {p_res.get('error')}",
                "status": "unknown",
                "main_entity_name": None,
                "main_date": None,
                "suggested_type": "document",
                "sha256": sha,
            }

        registry = load_registry(settings.cards_dir)
        from filegraph.router import _matches_rules
        pages = p_res.get("pages", [])
        text_sample = " ".join(t for _, t in pages[:3]) if pages else ""
        candidate_scores: list[tuple[int, int, str]] = []
        for cname, card in registry.cards.items():
            if not card.llm:
                continue
            qualifies, score = _matches_rules(card, path.name, path.name, path.suffix, text_sample)
            if qualifies:
                candidate_scores.append((card.priority, score, cname))

        if candidate_scores:
            candidate_scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
            card_name = candidate_scores[0][2]
        else:
            card_name = "generic"

        card = registry.get(card_name) or registry["generic"]

        # Check extraction cache
        cached = conn.execute(
            "SELECT data FROM extraction_cache WHERE sha256 = ? AND card_type = ? AND card_version = ?",
            (sha, card.name, card.version),
        ).fetchone()

        if cached:
            c_data = json.loads(cached["data"])
            main_ent = c_data.get("main_entity")
            if isinstance(main_ent, dict):
                main_ent = main_ent.get("name")
            return {
                "card_type": card.name,
                "category": c_data.get("category"),
                "title": c_data.get("title") or path.stem,
                "summary": c_data.get("summary"),
                "status": c_data.get("status"),
                "main_entity_name": main_ent,
                "main_date": None,
                "suggested_type": c_data.get("suggested_type"),
                "sha256": sha,
            }

        # Call extraction tool via LLM
        from filegraph.cards import compile_tool, system_prompt
        from filegraph.llm import call_tool, model_for_policy

        plan_items = [{"type": "text", "text": f"=== Page {p} ===\n{t}"} for p, t in pages[:5]]
        tool_spec = compile_tool(card)
        model_key = model_for_policy("cloud", "extract") or "GPT_4_1"
        sys_prompt = system_prompt(card, settings)

        res_args, _ = call_tool(model_key, tool=tool_spec, content=plan_items, system=sys_prompt)

        main_ent = res_args.get("main_entity")
        if isinstance(main_ent, dict):
            main_ent = main_ent.get("name")

        return {
            "card_type": card.name,
            "category": res_args.get("category"),
            "title": res_args.get("title") or path.stem,
            "summary": res_args.get("summary"),
            "status": res_args.get("status"),
            "main_entity_name": main_ent,
            "main_date": None,
            "suggested_type": res_args.get("suggested_type"),
            "sha256": sha,
        }

    finally:
        if close_conn:
            conn.close()


def suggest_folder(
    conn: Any,
    settings: Settings,
    doc_info: dict[str, Any],
    target_root_name: str,
    exclude_file_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Call LLM with choose_folder tool to decide destination folder (Section 17)."""
    exclude_file_ids = exclude_file_ids or set()
    ranked_candidates = candidates(conn, target_root_name, doc_info, exclude_file_ids=exclude_file_ids)

    # Top candidate folders for profiling
    top_candidates = [f for f, s in ranked_candidates[:5]]
    # Ensure root folder is considered if not already in top
    if "" not in top_candidates:
        top_candidates.append("")

    profiles = [folder_profile(conn, target_root_name, fld, exclude_file_ids=exclude_file_ids) for fld in top_candidates]
    profiles_text = "\n\n".join(profiles)

    all_existing_folders = [f for f, _ in ranked_candidates if f]
    existing_list_str = ", ".join(f'"{f}"' for f in all_existing_folders) if all_existing_folders else "(none)"

    user_content = f"""\
Target Root: {target_root_name}

New Document To File:
- Title: {doc_info.get('title') or 'Untitled'}
- Summary: {doc_info.get('summary') or 'None'}
- Card Type: {doc_info.get('card_type') or 'generic'}
- Category: {doc_info.get('category') or 'unknown'}
- Main Entity: {doc_info.get('main_entity_name') or 'None'}
- Suggested Type: {doc_info.get('suggested_type') or 'document'}

Existing Folders in Target Root:
{existing_list_str}

Candidate Folder Profiles:
{profiles_text}

Where should this document be filed? Call choose_folder.
"""

    from filegraph.llm import call_tool, model_for_policy
    model_key = model_for_policy("cloud", "filing") or "GPT_4_1"

    rec_args, _ = call_tool(
        model_key,
        tool=TOOL_CHOOSE_FOLDER,
        content=user_content,
        system=SYSTEM_PROMPT,
    )

    # Sanitize and ensure required keys
    folder_str = str(rec_args.get("folder") or "").strip().replace("\\", "/").strip("/")
    is_new = bool(rec_args.get("is_new_folder", False))
    confidence = float(rec_args.get("confidence", 0.5))
    reasoning = str(rec_args.get("reasoning") or "")
    alternatives = [str(a).strip().replace("\\", "/").strip("/") for a in rec_args.get("alternatives", [])]

    return {
        "folder": folder_str,
        "is_new_folder": is_new,
        "confidence": confidence,
        "reasoning": reasoning,
        "alternatives": alternatives[:3],
    }


def file_document(
    path: Path | str,
    target_root: str | None = None,
    apply: bool = False,
    move: bool = False,
    yes: bool = False,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Recommend or execute filing for a given document (Section 17).

    Returns (recommendation_dict, destination_path_or_None).
    """
    settings = settings or load_settings()
    src_path = Path(path).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"Source file not found: {src_path}")

    conn = connect(settings.db_path)
    try:
        # Extract metadata
        doc_info = extract_doc_info_for_path(src_path, settings, conn=conn)

        roots_by_name = {r.name: r for r in settings.roots}
        if not roots_by_name:
            raise ValueError("No roots configured in settings.")

        # Determine target root
        if target_root:
            if target_root not in roots_by_name:
                raise ValueError(f"Target root '{target_root}' not defined in config roots: {list(roots_by_name.keys())}")
            selected_root_name = target_root
        else:
            # Score candidates across all roots
            best_root = None
            best_score = -1.0
            for r_name in roots_by_name.keys():
                cands = candidates(conn, r_name, doc_info)
                top_score = cands[0][1] if cands else 0.0
                if top_score > best_score:
                    best_score = top_score
                    best_root = r_name
            selected_root_name = best_root or list(roots_by_name.keys())[0]

        recommendation = suggest_folder(conn, settings, doc_info, selected_root_name)

        dest_path = None
        action = "suggested"

        if apply:
            root_cfg = roots_by_name[selected_root_name]
            folder_clean = recommendation["folder"].strip("/\\")
            target_dir = Path(root_cfg.path) / folder_clean if folder_clean else Path(root_cfg.path)
            target_dir.mkdir(parents=True, exist_ok=True)

            # Prevent overwriting
            stem = src_path.stem
            ext = src_path.suffix
            candidate_dest = target_dir / src_path.name
            idx = 2
            while candidate_dest.exists():
                candidate_dest = target_dir / f"{stem} ({idx}){ext}"
                idx += 1
            dest_path = candidate_dest

            # Confirmation check
            if not yes:
                confirm = input(f"File '{src_path.name}' to '{dest_path}'? [y/N]: ").strip().lower()
                if confirm not in {"y", "yes"}:
                    action = "rejected"
                    conn.execute(
                        """
                        INSERT INTO filing_log (at, source_path, root_name, suggested_folder,
                                               is_new_folder, confidence, alternatives, reasoning, action, dest_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            now(),
                            str(src_path),
                            selected_root_name,
                            recommendation["folder"],
                            1 if recommendation["is_new_folder"] else 0,
                            recommendation["confidence"],
                            json.dumps(recommendation["alternatives"]),
                            recommendation["reasoning"],
                            action,
                            str(dest_path),
                        ),
                    )
                    conn.commit()
                    return recommendation, None

            # Copy or move
            if move:
                shutil.move(str(src_path), str(dest_path))
                action = "moved"
            else:
                shutil.copy2(str(src_path), str(dest_path))
                action = "copied"

            # Log to filing_log
            conn.execute(
                """
                INSERT INTO filing_log (at, source_path, root_name, suggested_folder,
                                       is_new_folder, confidence, alternatives, reasoning, action, dest_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now(),
                    str(src_path),
                    selected_root_name,
                    recommendation["folder"],
                    1 if recommendation["is_new_folder"] else 0,
                    recommendation["confidence"],
                    json.dumps(recommendation["alternatives"]),
                    recommendation["reasoning"],
                    action,
                    str(dest_path),
                ),
            )
            conn.commit()

            # Re-ingest, extract, link
            from filegraph.extract import extract_sync
            from filegraph.ingest import ingest
            from filegraph.link import link

            ingest(settings, root_name=selected_root_name)
            extract_sync(settings, root_filter=selected_root_name)
            link(settings)

        return recommendation, dest_path

    finally:
        conn.close()


def backtest(
    settings: Settings | None = None,
    n: int = 20,
    root_name: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Backtest filing accuracy on existing canonical documents (Section 17).

    Hides each document and its duplicates, predicts destination folder,
    evaluates exact and top-3 accuracy, and writes backtest_results.csv.
    """
    settings = settings or load_settings()
    conn = connect(settings.db_path)

    try:
        query = """
            SELECT c.file_id, c.root, c.path, c.folder, c.filename, c.card_type, c.category,
                   c.title, c.summary, c.main_entity, c.main_entity_name, c.main_date,
                   c.suggested_type, f.sha256
            FROM catalog c
            JOIN files f ON f.id = c.file_id
            WHERE c.kind != 'code'
              AND c.repo_id IS NULL
        """
        params: list[Any] = []
        if root_name:
            query += " AND c.root = ?"
            params.append(root_name)

        query += " ORDER BY c.file_id LIMIT ?"
        params.append(n)

        docs = conn.execute(query, params).fetchall()
        if not docs:
            return {"total": 0, "exact_acc": 0.0, "top3_acc": 0.0, "high_conf_acc": 0.0}

        results: list[dict[str, Any]] = []
        exact_matches = 0
        top3_matches = 0
        high_conf_total = 0
        high_conf_exact = 0

        for d in docs:
            file_id = d["file_id"]
            sha = d["sha256"]
            actual_folder = (d["folder"] or "").replace("\\", "/").strip("/")

            # Find all duplicates of this file
            dup_rows = conn.execute("SELECT id FROM files WHERE sha256 = ?", (sha,)).fetchall()
            exclude_ids = {r["id"] for r in dup_rows}
            exclude_ids.add(file_id)

            doc_info = {
                "card_type": d["card_type"],
                "category": d["category"],
                "title": d["title"],
                "summary": d["summary"],
                "main_entity_name": d["main_entity"] or d["main_entity_name"],
                "main_date": d["main_date"],
                "suggested_type": d["suggested_type"],
            }

            rec = suggest_folder(conn, settings, doc_info, d["root"], exclude_file_ids=exclude_ids)
            pred_folder = rec["folder"].replace("\\", "/").strip("/")
            alts = [a.replace("\\", "/").strip("/") for a in rec.get("alternatives", [])]
            conf = float(rec.get("confidence", 0.0))

            is_exact = pred_folder.lower() == actual_folder.lower()
            is_top3 = is_exact or any(a.lower() == actual_folder.lower() for a in alts[:2])

            if is_exact:
                exact_matches += 1
            if is_top3:
                top3_matches += 1

            if conf >= 0.8:
                high_conf_total += 1
                if is_exact:
                    high_conf_exact += 1

            results.append({
                "file_id": file_id,
                "root": d["root"],
                "path": d["path"],
                "actual_folder": actual_folder,
                "predicted_folder": pred_folder,
                "is_new_folder": rec.get("is_new_folder", False),
                "confidence": conf,
                "exact_match": is_exact,
                "top3_match": is_top3,
                "reasoning": rec.get("reasoning", ""),
            })

        total = len(results)
        exact_acc = exact_matches / total if total > 0 else 0.0
        top3_acc = top3_matches / total if total > 0 else 0.0
        high_conf_acc = high_conf_exact / high_conf_total if high_conf_total > 0 else 0.0

        # Write backtest_results.csv next to DB
        csv_path = settings.db_path.parent / "backtest_results.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "file_id", "root", "path", "actual_folder", "predicted_folder",
                    "is_new_folder", "confidence", "exact_match", "top3_match", "reasoning"
                ]
            )
            writer.writeheader()
            writer.writerows(results)

        return {
            "total": total,
            "exact_matches": exact_matches,
            "exact_acc": exact_acc,
            "top3_matches": top3_matches,
            "top3_acc": top3_acc,
            "high_conf_total": high_conf_total,
            "high_conf_exact": high_conf_exact,
            "high_conf_acc": high_conf_acc,
            "csv_path": str(csv_path),
        }

    finally:
        conn.close()
