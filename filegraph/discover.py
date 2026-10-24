"""Card discovery and proposal for new document kinds (Section 16)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from filegraph.cards import load_card_file, load_registry
from filegraph.config import Settings
from filegraph.llm import call_tool, model_for_policy


def discover(
    conn: Any,
    settings: Settings,
    min_count: int = 10,
    show_all: bool = False,
) -> list[dict[str, Any]]:
    """Discover frequent document types among generic or all documents (Section 16)."""
    registry = load_registry(settings.cards_dir)

    # Build absorbed set: suggested_type -> card_name
    absorbed: dict[str, str] = {}
    for cname, card in registry.cards.items():
        for st in card.absorbs:
            absorbed[st.lower()] = cname

    query = """
        SELECT suggested_type, COUNT(*) as count
        FROM documents
        WHERE suggested_type IS NOT NULL AND suggested_type != ''
    """
    if not show_all:
        query += " AND card_type = 'generic'"
    query += " GROUP BY suggested_type HAVING count >= ? ORDER BY count DESC"

    rows = conn.execute(query, (min_count,)).fetchall()
    results: list[dict[str, Any]] = []

    for r in rows:
        stype = r["suggested_type"]
        cnt = r["count"]

        # Fetch up to 3 example titles
        ex_rows = conn.execute(
            "SELECT title, rel_path FROM documents d JOIN files f ON f.id = d.file_id WHERE d.suggested_type = ? LIMIT 3",
            (stype,),
        ).fetchall()
        examples = [er["title"] or er["rel_path"] for er in ex_rows]

        abs_by = absorbed.get(stype.lower())

        results.append({
            "suggested_type": stype,
            "count": cnt,
            "examples": examples,
            "absorbed_by": abs_by,
        })

    return results


def print_discover(results: list[dict[str, Any]], min_count: int = 10) -> None:
    """Format and print discover results to stdout."""
    if not results:
        print(f"No document types found with count >= {min_count}.")
        print("Tip: Run with --min 1 or --all to see all suggested types.")
        return

    print(f"{'Suggested Type':<30} {'Count':<8} {'Status / Examples'}")
    print(f"{'─'*30} {'─'*8} {'─'*40}")

    for r in results:
        abs_tag = f"[absorbed by {r['absorbed_by']}] " if r["absorbed_by"] else ""
        ex_str = "; ".join(r["examples"])
        print(f"{r['suggested_type']:<30} {r['count']:<8} {abs_tag}{ex_str}")


DRAFT_CARD_TOOL = {
    "name": "draft_card",
    "description": "Draft a new YAML card template for a document type.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "pattern": "^[a-z0-9_]+$"},
            "description": {"type": "string"},
            "categories": {"type": "array", "items": {"type": "string"}},
            "statuses": {"type": "array", "items": {"type": "string"}},
            "main_entity": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "kind": {"type": "string"},
                },
                "required": ["role", "kind"],
            },
            "main_date": {"type": "string"},
            "date_labels": {"type": "array", "items": {"type": "string"}},
            "entity_roles": {"type": "array", "items": {"type": "string"}},
            "preferred_fact_keys": {"type": "array", "items": {"type": "string"}},
            "fact_key_aliases": {"type": "object"},
            "fields": {"type": "object"},
            "relations": {"type": "array", "items": {"type": "string"}},
            "instructions": {"type": "string"},
            "absorbs": {"type": "array", "items": {"type": "string"}},
            "match": {
                "type": "object",
                "properties": {
                    "filename_regex": {"type": "string"},
                    "keywords_any": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "required": [
            "name",
            "description",
            "categories",
            "statuses",
            "main_entity",
            "main_date",
            "date_labels",
            "entity_roles",
            "preferred_fact_keys",
            "absorbs",
        ],
    },
}


def propose_card(
    conn: Any,
    settings: Settings,
    suggested_type: str,
    samples: int = 15,
    name_override: str | None = None,
) -> Path:
    """Draft and write a new card template to cards/proposed/<name>.yaml (Section 16)."""
    # 1. Fetch sample documents
    doc_rows = conn.execute(
        """
        SELECT d.file_id, d.title, d.category, d.summary, d.main_entity_name,
               f.rel_path, f.filename
        FROM documents d
        JOIN files f ON f.id = d.file_id
        WHERE d.suggested_type = ?
        LIMIT ?
        """,
        (suggested_type, samples),
    ).fetchall()

    if not doc_rows:
        raise ValueError(f"No documents found with suggested_type = '{suggested_type}'")

    sample_data: list[dict[str, Any]] = []
    for dr in doc_rows:
        fid = dr["file_id"]
        dates = conn.execute("SELECT label, date FROM doc_dates WHERE file_id = ?", (fid,)).fetchall()
        facts = conn.execute("SELECT key, COALESCE(value_text, CAST(value_num AS TEXT)) as val FROM doc_facts WHERE file_id = ?", (fid,)).fetchall()
        ents = conn.execute("SELECT role, raw_name FROM doc_entities WHERE file_id = ?", (fid,)).fetchall()

        sample_data.append({
            "title": dr["title"],
            "filename": dr["filename"],
            "category": dr["category"],
            "summary": dr["summary"],
            "main_entity": dr["main_entity_name"],
            "dates": [dict(d) for d in dates],
            "facts": {f["key"]: str(f["val"])[:80] for f in facts},
            "entities": [dict(e) for e in ents],
        })

    # 2. Call extract model to draft card
    card_name = name_override or suggested_type.lower()
    prompt = f"""You design a card template for one kind of document so its key information can be extracted consistently.
You are given extracted data from {len(sample_data)} example documents that were all identified as "{suggested_type}".

Sample data:
{json.dumps(sample_data, indent=2, default=str)}

Propose a card template for this document kind. Call draft_card with the completed template.
"""

    model_key = model_for_policy("cloud", "extract") or "GPT_4_1"
    raw_card, _ = call_tool(model_key, DRAFT_CARD_TOOL, prompt)

    # 3. Assemble and validate card dict
    card_dict = dict(raw_card)
    card_dict["name"] = card_name
    card_dict["version"] = 1
    card_dict["llm"] = True
    card_dict["priority"] = 30
    if suggested_type not in card_dict.get("absorbs", []):
        card_dict.setdefault("absorbs", []).append(suggested_type)

    # Write to cards/proposed/<name>.yaml
    proposed_dir = Path(settings.cards_dir) / "proposed"
    proposed_dir.mkdir(parents=True, exist_ok=True)
    out_yaml = proposed_dir / f"{card_name}.yaml"

    with open(out_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(card_dict, f, sort_keys=False, allow_unicode=True)

    # Validate
    load_card_file(out_yaml)
    return out_yaml


def list_cards(registry: Any, conn: Any) -> list[dict[str, Any]]:
    """List all registered cards with version, document counts, and absorbs."""
    res = []
    for cname, card in registry.cards.items():
        doc_count = 0
        if conn:
            r = conn.execute("SELECT COUNT(*) as cnt FROM documents WHERE card_type = ?", (cname,)).fetchone()
            doc_count = r["cnt"] if r else 0

        res.append({
            "name": card.name,
            "version": card.version,
            "llm": card.llm,
            "priority": card.priority,
            "count": doc_count,
            "absorbs": card.absorbs,
            "description": card.description,
        })
    return res
