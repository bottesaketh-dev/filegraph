"""Entity resolution and edge linking (Section 14)."""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from filegraph.config import Settings
from filegraph.db import connect

CORP_SUFFIXES = {
    "pvt", "private", "limited", "ltd", "llp", "gmbh", "ag", "sa", "bv", "nv",
    "plc", "pte", "pty", "inc", "corp", "co", "company", "the", "and", "&",
    "llc", "corp.", "inc.", "ltd.", "co.", "services", "group"
}

HONORIFICS = {"mr", "mrs", "ms", "dr", "prof", "sri", "shri", "smt", "adv"}


def norm_key(name: str, kind: str) -> str:
    """Normalize entity name for clustering across spellings (Section 14.1)."""
    if not name:
        return ""

    s = name.strip()

    if kind == "organization":
        s = s.lower()
        # Drop parenthetical asides e.g. "Acme (Delaware)" -> "Acme"
        s = re.sub(r"\([^)]*\)", " ", s)
        # Non-alphanumeric and & to space
        s = re.sub(r"[^a-z0-9& ]+", " ", s)
        tokens = [t.strip(".") for t in s.split()]
        filtered = [t for t in tokens if t not in CORP_SUFFIXES]
        return " ".join(filtered) if filtered else " ".join(tokens)

    elif kind == "person":
        s = s.lower()
        # Drop honorifics
        tokens = [t.strip(".") for t in s.split()]
        filtered = [t for t in tokens if t not in HONORIFICS]
        # Keep name in order
        return " ".join(filtered) if filtered else " ".join(tokens)

    else:
        # place / product / other
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return " ".join(s.split())


def resolve_entities(conn: Any, settings: Settings) -> None:
    """Resolve and merge all staged document entities into entities and doc_entities (Section 14.1)."""
    self_person_keys = {norm_key(n, "person") for n in settings.me.names if n}
    self_org_keys = {norm_key(o, "organization") for o in settings.me.organizations if o}
    all_self_keys = self_person_keys | self_org_keys

    # Load existing entities: {kind: {norm_key: (id, name, aliases_set, is_self)}}
    existing_rows = conn.execute("SELECT * FROM entities").fetchall()
    by_kind: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for er in existing_rows:
        k = er["kind"]
        nk = er["norm_key"]
        aliases = set(json.loads(er["aliases"] or "[]"))
        by_kind[k][nk] = {
            "id": er["id"],
            "name": er["name"],
            "aliases": aliases,
            "is_self": er["is_self"],
            "spellings": Counter(aliases),
        }

    # Collect all mentions from documents
    doc_rows = conn.execute(
        "SELECT file_id, main_entity_name, extras FROM documents WHERE file_id IN (SELECT id FROM files WHERE missing = 0)"
    ).fetchall()

    # (file_id, kind, name, role)
    staged_mentions: list[tuple[int, str, str, str]] = []

    for dr in doc_rows:
        fid = dr["file_id"]
        main_name = dr["main_entity_name"]
        if main_name:
            staged_mentions.append((fid, "organization", main_name, "main_party"))

        extras = json.loads(dr["extras"] or "{}")
        for ent in extras.get("_entities", []):
            if isinstance(ent, dict) and ent.get("name"):
                staged_mentions.append((
                    fid,
                    ent.get("kind", "other"),
                    ent["name"].strip(),
                    ent.get("role", "mentioned"),
                ))

    # Cluster spellings
    for fid, kind, raw_name, role in staged_mentions:
        nk = norm_key(raw_name, kind)
        if not nk:
            continue

        kind_dict = by_kind[kind]
        matched_nk = None

        if nk in kind_dict:
            matched_nk = nk
        else:
            # Fuzzy match with cutoff 0.9
            close = difflib.get_close_matches(nk, list(kind_dict.keys()), n=1, cutoff=0.9)
            if close:
                matched_nk = close[0]

        if matched_nk:
            record = kind_dict[matched_nk]
            record["aliases"].add(raw_name)
            record["spellings"][raw_name] += 1
        else:
            is_self = 1 if nk in all_self_keys else 0
            kind_dict[nk] = {
                "id": None,
                "name": raw_name,
                "aliases": {raw_name},
                "is_self": is_self,
                "spellings": Counter([raw_name]),
            }

    # Upsert into entities table
    for kind, records in by_kind.items():
        for nk, rec in records.items():
            best_name = rec["spellings"].most_common(1)[0][0] if rec["spellings"] else rec["name"]
            aliases_json = json.dumps(sorted(rec["aliases"]))

            if rec["id"] is not None:
                conn.execute(
                    "UPDATE entities SET name = ?, aliases = ? WHERE id = ?",
                    (best_name, aliases_json, rec["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO entities (kind, name, norm_key, aliases, is_self)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(kind, norm_key) DO UPDATE SET
                        name = excluded.name,
                        aliases = excluded.aliases
                    """,
                    (kind, best_name, nk, aliases_json, rec["is_self"]),
                )
                row = conn.execute(
                    "SELECT id FROM entities WHERE kind = ? AND norm_key = ?", (kind, nk)
                ).fetchone()
                if row:
                    rec["id"] = row["id"]

    # Fill doc_entities and update documents.main_entity_id
    conn.execute("DELETE FROM doc_entities")

    for fid, kind, raw_name, role in staged_mentions:
        nk = norm_key(raw_name, kind)
        if not nk:
            continue
        # Find entity id
        ent_rec = by_kind[kind].get(nk)
        if not ent_rec:
            close = difflib.get_close_matches(nk, list(by_kind[kind].keys()), n=1, cutoff=0.9)
            if close:
                ent_rec = by_kind[kind][close[0]]

        if ent_rec and ent_rec.get("id"):
            eid = ent_rec["id"]
            conn.execute(
                """
                INSERT OR IGNORE INTO doc_entities (file_id, entity_id, role, raw_name)
                VALUES (?, ?, ?, ?)
                """,
                (fid, eid, role, raw_name),
            )

    # Update main_entity_id on documents (never self entity)
    for dr in doc_rows:
        fid = dr["file_id"]
        main_name = dr["main_entity_name"]
        main_id = None

        if main_name:
            nk = norm_key(main_name, "organization")
            r = conn.execute(
                "SELECT id, is_self FROM entities WHERE norm_key = ?", (nk,)
            ).fetchone()
            if r and not r["is_self"]:
                main_id = r["id"]

        if not main_id:
            # First non-self entity mentioned
            r = conn.execute(
                """
                SELECT e.id FROM doc_entities de
                JOIN entities e ON e.id = de.entity_id
                WHERE de.file_id = ? AND e.is_self = 0
                ORDER BY CASE WHEN de.role = 'main_party' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (fid,),
            ).fetchone()
            if r:
                main_id = r["id"]

        conn.execute(
            "UPDATE documents SET main_entity_id = ? WHERE file_id = ?",
            (main_id, fid),
        )

    # Clean up orphaned entities
    conn.execute(
        """
        DELETE FROM entities
        WHERE id NOT IN (SELECT DISTINCT entity_id FROM doc_entities)
          AND id NOT IN (SELECT DISTINCT main_entity_id FROM documents WHERE main_entity_id IS NOT NULL)
        """
    )
    conn.commit()


def build_edges(conn: Any) -> None:
    """Build graph edges: structural, code imports, and stated document references (Section 14.3)."""
    # 1. Delete existing dynamic edges
    conn.execute("DELETE FROM edges WHERE method IN ('structural', 'stated', 'code')")

    # 2. Structural: attachments (file -> parent_id)
    att_rows = conn.execute(
        "SELECT id, parent_id FROM files WHERE source = 'attachment' AND parent_id IS NOT NULL AND missing = 0"
    ).fetchall()
    for ar in att_rows:
        conn.execute(
            """
            INSERT INTO edges (src_id, dst_id, edge_type, method, confidence, evidence)
            VALUES (?, ?, 'attachment_of', 'structural', 1.0, 'Email attachment')
            """,
            (ar["id"], ar["parent_id"]),
        )

    # 3. Structural: duplicates
    dup_rows = conn.execute(
        """
        SELECT f.id as src_id, (
            SELECT MIN(f2.id) FROM files f2 WHERE f2.sha256 = f.sha256 AND f2.id < f.id AND f2.missing = 0
        ) as dst_id
        FROM files f
        WHERE f.missing = 0 AND f.sha256 IS NOT NULL AND dst_id IS NOT NULL
        """
    ).fetchall()
    for dr in dup_rows:
        conn.execute(
            """
            INSERT INTO edges (src_id, dst_id, edge_type, method, confidence, evidence)
            VALUES (?, ?, 'duplicate_of', 'structural', 1.0, 'Identical SHA-256')
            """,
            (dr["src_id"], dr["dst_id"]),
        )

    # 4. Code imports
    code_rows = conn.execute(
        """
        SELECT ci.file_id as src_id, ci.resolved_file_id as dst_id, ci.module
        FROM code_imports ci
        JOIN files f ON f.id = ci.file_id
        WHERE ci.resolved_file_id IS NOT NULL AND f.missing = 0
        """
    ).fetchall()
    for cr in code_rows:
        conn.execute(
            """
            INSERT INTO edges (src_id, dst_id, edge_type, method, confidence, evidence)
            VALUES (?, ?, 'imports', 'code', 1.0, ?)
            """,
            (cr["src_id"], cr["dst_id"], cr["module"]),
        )

    # 5. Stated document references
    doc_rows = conn.execute(
        """
        SELECT d.file_id, d.title, d.main_entity_id, d.refs, f.folder
        FROM documents d
        JOIN files f ON f.id = d.file_id
        WHERE f.missing = 0 AND (
            SELECT MIN(f2.id) FROM files f2 WHERE f2.sha256 = f.sha256 AND f2.missing = 0
        ) = f.id
        """
    ).fetchall()

    for dr in doc_rows:
        src_id = dr["file_id"]
        refs = json.loads(dr["refs"] or "[]")
        if not refs:
            continue

        for ref in refs:
            if not isinstance(ref, dict):
                continue
            relation = ref.get("relation", "references")
            target_title = ref.get("title", "")
            target_date = ref.get("date")
            target_entity_raw = ref.get("entity")
            quote = ref.get("quote", "")
            page_num = ref.get("page")

            # Resolve entity of reference
            cand_entity_id = None
            if target_entity_raw:
                nk = norm_key(target_entity_raw, "organization")
                e_row = conn.execute("SELECT id FROM entities WHERE norm_key = ?", (nk,)).fetchone()
                if e_row:
                    cand_entity_id = e_row["id"]

            if not cand_entity_id:
                cand_entity_id = dr["main_entity_id"]

            # Candidate query
            candidates = []
            if cand_entity_id:
                candidates = conn.execute(
                    """
                    SELECT d.file_id, d.title, d.main_date, d.status
                    FROM documents d
                    WHERE d.file_id != ? AND d.main_entity_id = ?
                    """,
                    (src_id, cand_entity_id),
                ).fetchall()

            if not candidates and dr["folder"]:
                candidates = conn.execute(
                    """
                    SELECT d.file_id, d.title, d.main_date, d.status
                    FROM documents d
                    JOIN files f ON f.id = d.file_id
                    WHERE d.file_id != ? AND f.folder = ?
                    """,
                    (src_id, dr["folder"]),
                ).fetchall()

            best_match_id = None
            best_score = 0.0

            # Score candidates
            for c in candidates:
                cand_id = c["file_id"]
                cand_title = c["title"] or ""
                # Title similarity
                ratio = difflib.SequenceMatcher(None, target_title.lower(), cand_title.lower()).ratio()
                score = ratio

                # Date bonus
                cand_date = c["main_date"]
                if target_date and cand_date:
                    if target_date == cand_date:
                        score += 0.4
                    elif target_date[:4] == cand_date[:4]:
                        score += 0.1

                # Executed status bonus
                if c["status"] in ("executed", "final", "signed"):
                    score += 0.05

                if score > best_score:
                    best_score = score
                    best_match_id = cand_id

            if best_score >= 0.55 and best_match_id:
                conn.execute(
                    """
                    INSERT INTO edges (src_id, dst_id, edge_type, method, confidence, evidence, page)
                    VALUES (?, ?, ?, 'stated', ?, ?, ?)
                    """,
                    (src_id, best_match_id, relation, round(best_score, 2), quote, page_num),
                )
            else:
                # Unresolved reference: dst_id = NULL
                conn.execute(
                    """
                    INSERT INTO edges (src_id, dst_id, edge_type, method, confidence, evidence, page, ref)
                    VALUES (?, NULL, ?, 'stated', NULL, ?, ?, ?)
                    """,
                    (src_id, relation, quote, page_num, json.dumps(ref)),
                )

    conn.commit()


def link(settings: Settings) -> None:
    """Run full linking pipeline: entities and edges (Section 14)."""
    conn = connect(settings.db_path)
    resolve_entities(conn, settings)
    build_edges(conn)
    conn.close()
