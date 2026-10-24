"""Export FileGraph index: interactive graph, Obsidian vault, review CSV, and report (Section 19)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from filegraph.config import Settings
from filegraph.db import abs_path, connect
from filegraph.graph_template import GRAPH_HTML_TEMPLATE


def _escape_script_json(obj: Any) -> str:
    """Serialize object to JSON with </ escaped as <\\/ to prevent script tag breakout."""
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    return s.replace("</", "<\\/")


def export_graph(conn: Any, settings: Settings, out_path: Path | str = "graph.html") -> Path:
    """Generate interactive graph.html (Section 19.2)."""
    out_file = Path(out_path).resolve()

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Map entity_id -> graph node id: offset to avoid collisions with file_id
    # We can prefix entity IDs or use high offset
    # Let's use 10_000_000 + entity_id, and 20_000_000 + repo_id
    ENT_OFFSET = 10_000_000
    REPO_OFFSET = 20_000_000

    # Fetch all doc entities to attach to document nodes
    doc_entities_rows = conn.execute(
        """
        SELECT de.file_id, e.id, e.name, e.kind, de.role, e.is_self
        FROM doc_entities de
        JOIN entities e ON e.id = de.entity_id
        """
    ).fetchall()
    doc_entities_by_file: dict[int, list[dict[str, Any]]] = {}
    for de in doc_entities_rows:
        doc_entities_by_file.setdefault(de["file_id"], []).append({
            "id": de["id"],
            "name": de["name"],
            "kind": de["kind"],
            "role": de["role"],
            "is_self": bool(de["is_self"]),
        })

    # 1. Documents & Files (include all non-missing files)
    file_rows = conn.execute(
        """
        SELECT f.id as file_id, f.rel_path, r.name as root_name,
               d.card_type, d.category, d.title, d.status, d.confidence,
               d.summary, d.main_date, d.main_entity_name
        FROM files f
        LEFT JOIN documents d ON d.file_id = f.id
        LEFT JOIN roots r ON r.id = f.root_id
        WHERE f.missing = 0
        """
    ).fetchall()

    doc_ids = set()
    for fr in file_rows:
        fid = fr["file_id"]
        doc_ids.add(fid)
        ctype = fr["card_type"] or "generic"
        cat = fr["category"] or ""

        # Read facts and dates
        facts_rows = conn.execute(
            "SELECT key, COALESCE(value_text, CAST(value_num AS TEXT), '') as value FROM doc_facts WHERE file_id = ? LIMIT 8", (fid,)
        ).fetchall()
        facts_dict = {row["key"]: row["value"] for row in facts_rows}

        root_path = Path(settings.roots[0].path) if settings.roots else Path.cwd()
        for root_cfg in settings.roots:
            if root_cfg.name == fr["root_name"]:
                root_path = Path(root_cfg.path)
                break
        full_p = root_path / fr["rel_path"]
        file_url = full_p.as_uri()

        title = fr["title"] or Path(fr["rel_path"]).name
        summary = fr["summary"] or f"File {Path(fr['rel_path']).name}"

        nodes.append({
            "id": fid,
            "label": title,
            "subtitle": f"{ctype} · {cat}" if cat else ctype,
            "group": ctype,
            "category": cat,
            "kind": "doc",
            "status": fr["status"],
            "confidence": fr["confidence"],
            "summary": summary,
            "main_date": fr["main_date"],
            "main_entity": fr["main_entity_name"],
            "rel_path": fr["rel_path"],
            "file_url": file_url,
            "facts": facts_dict,
            "entities": doc_entities_by_file.get(fid, []),
        })

    # 2. Entities
    ent_rows = conn.execute(
        """
        SELECT e.*, (SELECT COUNT(*) FROM doc_entities de WHERE de.entity_id = e.id) as mention_count
        FROM entities e
        WHERE (
            e.id IN (SELECT DISTINCT entity_id FROM doc_entities)
            OR e.id IN (SELECT DISTINCT main_entity_id FROM documents WHERE main_entity_id IS NOT NULL)
        )
        """
    ).fetchall()

    for er in ent_rows:
        eid = er["id"]
        graph_eid = ENT_OFFSET + eid
        nodes.append({
            "id": graph_eid,
            "label": er["name"],
            "subtitle": f"Entity: {er['kind']}",
            "group": "entity",
            "kind": "entity",
            "is_self": bool(er["is_self"]),
            "status": None,
            "confidence": None,
            "summary": f"{er['name']} ({er['kind']}) — mentioned across documents",
            "main_date": None,
            "main_entity": None,
            "rel_path": None,
            "file_url": None,
            "facts": {"Kind": er["kind"], "Mentions": er["mention_count"]},
        })

    # 3. Repositories
    repo_rows = conn.execute("SELECT * FROM repos").fetchall()
    for rr in repo_rows:
        rid = rr["id"]
        graph_rid = REPO_OFFSET + rid
        langs = json.loads(rr["languages"] or "{}")
        nodes.append({
            "id": graph_rid,
            "label": rr["name"],
            "subtitle": "Repository",
            "group": "repo",
            "kind": "repo",
            "status": rr["head_commit"][:7] if rr["head_commit"] else None,
            "confidence": None,
            "summary": rr["summary"] or f"Repository {rr['name']}",
            "main_date": None,
            "main_entity": None,
            "rel_path": rr["rel_path"],
            "file_url": None,
            "facts": langs,
        })

    # 4. Edges: doc -> entity (doc_entities)
    de_rows = conn.execute(
        """
        SELECT de.file_id, de.entity_id, de.role, de.raw_name, e.kind, e.is_self
        FROM doc_entities de
        JOIN entities e ON e.id = de.entity_id
        WHERE e.is_self = 0
        """
    ).fetchall()

    doc_entity_edges: dict[tuple[int, int], dict[str, Any]] = {}
    for de in de_rows:
        fid = de["file_id"]
        graph_eid = ENT_OFFSET + de["entity_id"]
        role = (de["role"] or "").lower()
        ekind = de["kind"]
        is_main = (
            role in ("main_party", "issuer", "provider", "counterparty", "employer", "school", "college", "institution", "insurer", "bank", "board", "company", "author")
            or ekind == "organization"
        )
        pair_key = (fid, graph_eid)

        if pair_key in doc_entity_edges:
            if is_main:
                doc_entity_edges[pair_key]["edge_type"] = "party"
        else:
            doc_entity_edges[pair_key] = {
                "id": f"e_de_{fid}_{graph_eid}",
                "from": fid,
                "to": graph_eid,
                "edge_type": "party" if is_main else "mentioned_in",
                "evidence": de["raw_name"],
                "arrows": "to",
            }

    edges.extend(doc_entity_edges.values())

    # 5. Edges from edges table
    edge_rows = conn.execute(
        """
        SELECT e.*, f1.missing as src_missing, f2.missing as dst_missing
        FROM edges e
        JOIN files f1 ON f1.id = e.src_id
        LEFT JOIN files f2 ON f2.id = e.dst_id
        WHERE f1.missing = 0 AND e.edge_type != 'duplicate_of'
        """
    ).fetchall()

    for idx, er in enumerate(edge_rows):
        src = er["src_id"]
        dst = er["dst_id"]
        if dst is None:
            # Unresolved reference node
            continue
        edge_id = er["id"] if "id" in er.keys() else idx
        edges.append({
            "id": f"e_db_{edge_id}_{src}_{dst}_{idx}",
            "from": src,
            "to": dst,
            "edge_type": er["edge_type"],
            "evidence": er["evidence"] or "",
            "arrows": "to",
        })

    # 6. Repo -> File edges (hidden by default)
    repo_files = conn.execute(
        "SELECT id, repo_id FROM files WHERE repo_id IS NOT NULL AND missing = 0"
    ).fetchall()
    for idx, rf in enumerate(repo_files):
        edges.append({
            "id": f"e_rf_{rf['repo_id']}_{rf['id']}_{idx}",
            "from": REPO_OFFSET + rf["repo_id"],
            "to": rf["id"],
            "edge_type": "contains_file",
            "evidence": "",
            "arrows": "to",
        })

    graph_data = {
        "nodes": nodes,
        "edges": edges,
    }

    # Inline vis-network script for 100% offline self-contained standalone execution
    vis_file = Path("vis-network.min.js").resolve()
    if not vis_file.is_file():
        vis_file = Path(__file__).resolve().parent.parent / "vis-network.min.js"

    if vis_file.is_file():
        vis_code = vis_file.read_text(encoding="utf-8")
        vis_script_tag = f"<script type=\"text/javascript\">\n{vis_code}\n</script>"
    else:
        vis_script_tag = '<script type="text/javascript" src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>'

    embedded_json = _escape_script_json(graph_data)
    html_content = (
        GRAPH_HTML_TEMPLATE
        .replace("__GRAPH_DATA__", embedded_json)
        .replace("__VIS_NETWORK_SCRIPT__", vis_script_tag)
    )

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html_content, encoding="utf-8")
    return out_file


def export_review_csv(conn: Any, settings: Settings, out_path: Path | str = "review.csv") -> Path:
    """Export review CSV with BOM for spreadsheet software (Section 19.3)."""
    out_file = Path(out_path).resolve()

    rows = conn.execute(
        """
        SELECT
            d.file_id,
            r.name as root,
            f.rel_path as path,
            d.card_type,
            d.category,
            d.title,
            d.status,
            d.main_entity_name as main_entity,
            d.main_date_label,
            d.main_date,
            d.confidence,
            d.needs_review,
            d.review_reasons,
            (SELECT MIN(f2.id) FROM files f2 WHERE f2.sha256 = f.sha256 AND f2.id < f.id) as duplicate_of
        FROM documents d
        JOIN files f ON f.id = d.file_id
        LEFT JOIN roots r ON r.id = f.root_id
        WHERE f.missing = 0
        ORDER BY d.file_id ASC
        """
    ).fetchall()

    headers = [
        "file_id", "root", "path", "card_type", "category", "title", "status",
        "main_entity", "main_date_label", "main_date", "dates", "top facts",
        "confidence", "needs_review", "review_reasons", "duplicate_of",
        "card type correct?", "main entity correct?", "main date correct?", "notes",
    ]

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(headers)

        for r in rows:
            fid = r["file_id"]

            # Dates
            date_rows = conn.execute(
                "SELECT label, date FROM doc_dates WHERE file_id = ?", (fid,)
            ).fetchall()
            dates_str = "; ".join(f"{dr['label']}={dr['date']}" for dr in date_rows)

            # Top facts
            fact_rows = conn.execute(
                "SELECT key, COALESCE(value_text, CAST(value_num AS TEXT), '') as value FROM doc_facts WHERE file_id = ? LIMIT 8", (fid,)
            ).fetchall()
            facts_str = "; ".join(f"{fr['key']}={fr['value']}" for fr in fact_rows)

            reasons_str = ""
            if r["review_reasons"]:
                try:
                    reasons_str = "; ".join(json.loads(r["review_reasons"]))
                except Exception:
                    reasons_str = str(r["review_reasons"])

            writer.writerow([
                r["file_id"],
                r["root"],
                r["path"],
                r["card_type"],
                r["category"],
                r["title"],
                r["status"],
                r["main_entity"],
                r["main_date_label"],
                r["main_date"],
                dates_str,
                facts_str,
                r["confidence"],
                1 if r["needs_review"] else 0,
                reasons_str,
                r["duplicate_of"] or "",
                "", "", "", "",
            ])

    return out_file


def export_vault(
    conn: Any,
    settings: Settings,
    out_dir: Path | str = "vault",
    include_code: bool = False,
) -> Path:
    """Export Obsidian vault (Section 19.1)."""
    vault_path = Path(out_dir).resolve()
    docs_dir = vault_path / "Documents"
    ents_dir = vault_path / "Entities"
    repos_dir = vault_path / "Repos"

    docs_dir.mkdir(parents=True, exist_ok=True)
    ents_dir.mkdir(parents=True, exist_ok=True)
    repos_dir.mkdir(parents=True, exist_ok=True)

    # 1. Documents
    query = """
        SELECT d.*, f.root_id, f.rel_path, f.kind, r.name as root_name
        FROM documents d
        JOIN files f ON f.id = d.file_id
        LEFT JOIN roots r ON r.id = f.root_id
        WHERE f.missing = 0
    """
    if not include_code:
        query += " AND d.card_type != 'code'"

    doc_rows = conn.execute(query).fetchall()

    needs_review_items = []

    for dr in doc_rows:
        fid = dr["file_id"]
        title = dr["title"] or f"Doc_{fid}"
        safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip() or f"Doc_{fid}"
        note_file = docs_dir / f"{safe_title}.md"

        # YAML tags
        tags = [
            f"type/{dr['card_type']}",
            f"cat/{dr['category']}" if dr["category"] else "cat/unknown",
            f"status/{dr['status']}" if dr["status"] else "status/unknown",
        ]
        if dr["needs_review"]:
            tags.append("needs-review")
            needs_review_items.append((safe_title, dr["review_reasons"]))

        # Dates
        date_rows = conn.execute("SELECT label, date FROM doc_dates WHERE file_id = ?", (fid,)).fetchall()
        # Facts
        fact_rows = conn.execute("SELECT key, COALESCE(value_text, CAST(value_num AS TEXT), '') as value FROM doc_facts WHERE file_id = ?", (fid,)).fetchall()

        root_path = Path(settings.roots[0].path) if settings.roots else Path.cwd()
        for root_cfg in settings.roots:
            if root_cfg.name == dr["root_name"]:
                root_path = Path(root_cfg.path)
                break
        full_p = root_path / dr["rel_path"]

        lines = [
            "---",
            f'title: "{dr["title"] or ""}"',
            f'card_type: "{dr["card_type"]}"',
            f'category: "{dr["category"] or ""}"',
            f'status: "{dr["status"] or ""}"',
            f'main_entity: "{dr["main_entity_name"] or ""}"',
            f'main_date: "{dr["main_date"] or ""}"',
            f'main_date_label: "{dr["main_date_label"] or ""}"',
            f'confidence: "{dr["confidence"] or ""}"',
            f'needs_review: {bool(dr["needs_review"])}',
            f'path: "{dr["rel_path"]}"',
            f'root: "{dr["root_name"] or ""}"',
            f'file_id: {fid}',
            f"tags: [{', '.join(tags)}]",
            "---",
            "",
            f"# {title}",
            "",
        ]

        if dr["main_entity_name"]:
            lines.append(f"**Entity**: [[{dr['main_entity_name']}]]\n")

        lines.append(f"[Open original file]({full_p.as_uri()})\n")

        if dr["summary"]:
            lines.append("## Summary\n")
            lines.append(dr["summary"] + "\n")

        if date_rows:
            lines.append("## Dates\n")
            for d in date_rows:
                lines.append(f"- **{d['label']}**: {d['date']}")
            lines.append("")

        if fact_rows:
            lines.append("## Facts\n")
            lines.append("| Key | Value |")
            lines.append("|---|---|")
            for f in fact_rows:
                lines.append(f"| {f['key']} | {f['value']} |")
            lines.append("")

        note_file.write_text("\n".join(lines), encoding="utf-8")

    # 2. Entities
    ent_rows = conn.execute(
        """
        SELECT e.* FROM entities e
        WHERE e.is_self = 0 AND (
            e.id IN (SELECT DISTINCT entity_id FROM doc_entities)
            OR e.id IN (SELECT DISTINCT main_entity_id FROM documents WHERE main_entity_id IS NOT NULL)
        )
        """
    ).fetchall()

    for er in ent_rows:
        ename = er["name"]
        safe_name = "".join(c for c in ename if c not in r'\/:*?"<>|').strip() or f"Entity_{er['id']}"
        note_file = ents_dir / f"{safe_name}.md"

        # Find linked docs
        linked_docs = conn.execute(
            """
            SELECT d.title, d.file_id FROM documents d
            JOIN doc_entities de ON de.file_id = d.file_id
            WHERE de.entity_id = ?
            UNION
            SELECT d.title, d.file_id FROM documents d
            WHERE d.main_entity_id = ?
            """,
            (er["id"], er["id"]),
        ).fetchall()

        lines = [
            "---",
            f'name: "{ename}"',
            f'kind: "{er["kind"]}"',
            f"tags: [entity/{er['kind']}]",
            "---",
            "",
            f"# {ename}",
            f"**Kind**: {er['kind']}",
            "",
            "## Documents",
            "",
        ]
        for ld in linked_docs:
            ltitle = ld["title"] or f"Doc_{ld['file_id']}"
            safe_ltitle = "".join(c for c in ltitle if c not in r'\/:*?"<>|').strip()
            lines.append(f"- [[{safe_ltitle}]]")

        note_file.write_text("\n".join(lines), encoding="utf-8")

    # 3. Needs review summary note
    nr_file = vault_path / "_Needs review.md"
    nr_lines = [
        "# Documents Flagged for Review",
        "",
        f"Total: {len(needs_review_items)}",
        "",
    ]
    for dtitle, reasons in needs_review_items:
        r_str = ""
        if reasons:
            try:
                r_str = " — " + ", ".join(json.loads(reasons))
            except Exception:
                r_str = f" — {reasons}"
        nr_lines.append(f"- [[{dtitle}]]{r_str}")

    nr_file.write_text("\n".join(nr_lines), encoding="utf-8")
    return vault_path


def report(conn: Any, settings: Settings) -> str:
    """Generate summary report (Section 19.4)."""
    out: list[str] = []
    out.append("=" * 60)
    out.append(" FileGraph Summary Report")
    out.append("=" * 60)

    # 1. Files vs cards
    total_files = conn.execute("SELECT COUNT(*) as n FROM files WHERE missing = 0").fetchone()["n"]
    total_docs = conn.execute("SELECT COUNT(*) as n FROM documents").fetchone()["n"]
    out.append(f"\nFiles: {total_files} active | Cards extracted: {total_docs}")

    # 2. Counts by card_type
    card_counts = conn.execute(
        "SELECT card_type, count(*) as n FROM documents GROUP BY card_type ORDER BY n DESC"
    ).fetchall()
    out.append("\nCard Types:")
    for r in card_counts:
        out.append(f"  {r['card_type']:<20}: {r['n']}")

    # 3. Counts by category
    cat_counts = conn.execute(
        "SELECT category, count(*) as n FROM documents GROUP BY category ORDER BY n DESC LIMIT 10"
    ).fetchall()
    out.append("\nTop Categories:")
    for r in cat_counts:
        out.append(f"  {r['category'] or '<none>':<20}: {r['n']}")

    # 4. Status counts
    stat_counts = conn.execute(
        "SELECT status, count(*) as n FROM documents GROUP BY status ORDER BY n DESC"
    ).fetchall()
    out.append("\nStatuses:")
    for r in stat_counts:
        out.append(f"  {r['status'] or '<none>':<20}: {r['n']}")

    # 5. Review count
    nr_count = conn.execute("SELECT COUNT(*) as n FROM documents WHERE needs_review = 1").fetchone()["n"]
    out.append(f"\nDocuments Flagged for Review: {nr_count}")

    # 6. Entities & Edges
    n_ent = conn.execute("SELECT COUNT(*) as n FROM entities").fetchone()["n"]
    n_edges = conn.execute("SELECT COUNT(*) as n FROM edges").fetchone()["n"]
    out.append(f"Entities: {n_ent} | Edges: {n_edges}")

    out.append("\n" + "=" * 60)
    return "\n".join(out)
