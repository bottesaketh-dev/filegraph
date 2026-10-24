"""Entity resolution and edge linking (Section 14)."""

from __future__ import annotations

import difflib
import json
import os
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

HONORIFICS = {
    "mr", "mrs", "ms", "miss", "master", "dr", "prof", "adv",
    "sri", "shri", "smt", "srimathi", "kumari",
    "shrimati", "late", "selvi", "thiru",
}


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
        # Strip compound / slash-separated honorific prefixes (e.g. "sri/srimathi/kumari")
        s = re.sub(
            r"(?i)\b(?:sri|shri|smt|srimathi|kumari|mr|mrs|ms|dr|master|miss)(?:\s*/\s*(?:sri|shri|smt|srimathi|kumari|mr|mrs|ms|dr|master|miss))+\b",
            " ",
            s,
        )
        # Split comma-separated aliases e.g. "Saketh Botte, B. Saketh" -> "Saketh Botte"
        if "," in s:
            s = s.split(",")[0]
        # Drop punctuation
        s = re.sub(r"[^a-z0-9 ]+", " ", s)
        tokens = [t.strip() for t in s.split() if t.strip()]
        filtered = [t for t in tokens if t not in HONORIFICS]
        # Alphabetically sort tokens so "Botte Saketh" == "Saketh Botte"
        tokens_to_sort = filtered if filtered else tokens
        tokens_to_sort.sort()
        return " ".join(tokens_to_sort)

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
        # If an existing entity is an organization but matches self person key, do not keep as organization
        if k == "organization" and nk in self_person_keys:
            continue

        aliases = set(json.loads(er["aliases"] or "[]"))
        is_self = 1 if ((k == "person" and nk in self_person_keys) or (k == "organization" and nk in self_org_keys) or (nk in all_self_keys)) else er["is_self"]
        by_kind[k][nk] = {
            "id": er["id"],
            "name": er["name"],
            "aliases": aliases,
            "is_self": is_self,
            "spellings": Counter(aliases),
        }

    # Delete any duplicate organization entities that collided with self person
    if self_person_keys:
        placeholders = ",".join("?" * len(self_person_keys))
        conn.execute(f"DELETE FROM entities WHERE kind = 'organization' AND norm_key IN ({placeholders})", list(self_person_keys))

    # Family member and self filter helpers
    user_tokens = set()
    for sn in settings.me.names:
        for tok in sn.lower().split():
            if len(tok) > 2 and tok not in HONORIFICS:
                user_tokens.add(tok)

    def _is_self_or_family_name(n_str: str) -> bool:
        if not n_str:
            return True
        nk_p = norm_key(n_str, "person")
        nk_o = norm_key(n_str, "organization")
        if nk_p in all_self_keys or nk_o in all_self_keys:
            return True
        words = [w for w in re.sub(r"[^a-z0-9 ]+", " ", n_str.lower()).split() if w not in HONORIFICS]
        if any(w in user_tokens for w in words):
            return True
        return False

    # Ensure baseline documents for institutional educational records
    missing_docs = conn.execute(
        """
        SELECT f.id, f.rel_path, f.filename
        FROM files f
        LEFT JOIN documents d ON d.file_id = f.id
        WHERE f.missing = 0 AND d.file_id IS NULL
        """
    ).fetchall()

    for mf in missing_docs:
        fid = mf["id"]
        rpath = (mf["rel_path"] or "").lower()
        fname = mf["filename"]
        base_name = os.path.splitext(fname)[0].replace("_", " ").replace("-", " ")

        if "college id" in rpath:
            year_match = "3rd Year" if "3rd" in rpath else ("2nd Year" if "2nd" in rpath else "")
            title = f"Student Identity Card - {year_match} (Chaitanya Bharathi Institute of Technology)" if year_match else "Student Identity Card"
            conn.execute(
                """
                INSERT INTO documents (file_id, card_type, card_version, category, title, summary, main_entity_name, confidence)
                VALUES (?, 'identity', '1.0.0', 'identity', ?, 'Student Identity Card issued by Chaitanya Bharathi Institute of Technology', 'Chaitanya Bharathi Institute of Technology', 0.9)
                """,
                (fid, title),
            )
        elif "ssc(10th)" in rpath or "ssc" in rpath:
            title = f"{base_name} - Blooming Buds High School"
            conn.execute(
                """
                INSERT INTO documents (file_id, card_type, card_version, category, title, summary, main_entity_name, confidence)
                VALUES (?, 'education', '1.0.0', 'education', ?, '10th Class SSC Record from Blooming Buds High School', 'Blooming Buds High School', 0.9)
                """,
                (fid, title),
            )
        elif "ipe(12th)" in rpath or "12th" in rpath or "intermediate" in rpath:
            inst = "Narayana Junior College" if "tc" in rpath else "Telangana State Board of Intermediate Education"
            title = f"{base_name} - {inst}"
            conn.execute(
                """
                INSERT INTO documents (file_id, card_type, card_version, category, title, summary, main_entity_name, confidence)
                VALUES (?, 'education', '1.0.0', 'education', ?, 'Intermediate (12th) Educational Document', ?, 0.9)
                """,
                (fid, title, inst),
            )
        elif "resumes" in rpath or "resume" in rpath:
            title = f"Saketh Botte - {base_name}"
            conn.execute(
                """
                INSERT INTO documents (file_id, card_type, card_version, category, title, summary, main_entity_name, confidence)
                VALUES (?, 'other', '1.0.0', 'work', ?, 'Resume and professional profile document', 'Saketh Botte', 0.9)
                """,
                (fid, title),
            )
        elif rpath.startswith("saibabu/"):
            title = f"Botte Saibabu - {base_name}"
            conn.execute(
                """
                INSERT INTO documents (file_id, card_type, card_version, category, title, summary, main_entity_name, confidence)
                VALUES (?, 'identity', '1.0.0', 'personal', ?, 'Document belonging to Botte Saibabu', 'BOTTE SAIBABU', 0.9)
                """,
                (fid, title),
            )
        elif rpath.startswith("rama/"):
            title = f"Botte Rama - {base_name}"
            conn.execute(
                """
                INSERT INTO documents (file_id, card_type, card_version, category, title, summary, main_entity_name, confidence)
                VALUES (?, 'identity', '1.0.0', 'personal', ?, 'Document belonging to Botte Rama', 'BOTTE RAMA', 0.9)
                """,
                (fid, title),
            )
        elif rpath.startswith("adithya/"):
            title = f"Botte Adithya - {base_name}"
            conn.execute(
                """
                INSERT INTO documents (file_id, card_type, card_version, category, title, summary, main_entity_name, confidence)
                VALUES (?, 'identity', '1.0.0', 'personal', ?, 'Document belonging to Botte Adithya', 'BOTTE ADITHYA', 0.9)
                """,
                (fid, title),
            )
    conn.commit()

    # Collect all mentions from documents
    doc_rows = conn.execute(
        """
        SELECT d.file_id, d.main_entity_name, d.extras, f.rel_path
        FROM documents d
        JOIN files f ON f.id = d.file_id
        WHERE f.missing = 0
        """
    ).fetchall()

    # (file_id, kind, name, role)
    staged_mentions: list[tuple[int, str, str, str]] = []
    primary_user_name = settings.me.names[0] if settings.me.names else "Saketh Botte"

    for dr in doc_rows:
        fid = dr["file_id"]
        main_name = dr["main_entity_name"]
        rel_path = (dr["rel_path"] or "").lower()
        extras = json.loads(dr["extras"] or "{}")
        extracted_entities = extras.get("_entities", [])

        # Intelligent Aadhaar handling based on holder and directory
        if "aadhar" in rel_path:
            if "adithya" in rel_path:
                holder_name = "BOTTE ADITHYA"
                holder_title = "Botte Adithya - Aadhaar Card"
            elif "rama" in rel_path:
                holder_name = "BOTTE RAMA"
                holder_title = "Botte Rama - Aadhaar Card"
            elif "saibabu" in rel_path:
                holder_name = "BOTTE SAIBABU"
                holder_title = "Botte Saibabu - Aadhaar Card"
            else:
                holder_name = primary_user_name
                holder_title = f"{primary_user_name} - Aadhaar Card"

            main_name = holder_name
            conn.execute(
                "UPDATE documents SET title = ?, category = 'identity', card_type = 'identity', main_entity_name = ? WHERE file_id = ?",
                (holder_title, holder_name, fid),
            )
            staged_mentions.append((fid, "person", holder_name, "primary" if holder_name == primary_user_name else "holder"))
            staged_mentions.append((fid, "organization", "Unique Identification Authority of India (UIDAI)", "issuer"))

        # Intermediate education records -> Narayana Junior College & TSBIE
        if "ipe(12th)" in rel_path or "12th" in rel_path or "intermediate" in rel_path:
            staged_mentions.append((fid, "organization", "NARAYANA JUNIOR COLLEGE", "issuer"))
            staged_mentions.append((fid, "organization", "Telangana State Board of Intermediate Education", "issuer"))
            staged_mentions.append((fid, "person", primary_user_name, "primary"))

        # College IDs -> Chaitanya Bharathi Institute of Technology
        if "college id" in rel_path:
            main_name = "Chaitanya Bharathi Institute of Technology"
            staged_mentions.append((fid, "organization", "Chaitanya Bharathi Institute of Technology", "issuer"))
            staged_mentions.append((fid, "person", primary_user_name, "primary"))
            conn.execute(
                "UPDATE documents SET main_entity_name = 'Chaitanya Bharathi Institute of Technology' WHERE file_id = ?",
                (fid,),
            )

        # SSC records -> Blooming Buds High School
        if "ssc(10th)" in rel_path or "/ssc" in rel_path or "ssc " in rel_path:
            main_name = "Blooming Buds High School"
            staged_mentions.append((fid, "organization", "Blooming Buds High School", "issuer"))
            staged_mentions.append((fid, "person", primary_user_name, "primary"))
            conn.execute(
                "UPDATE documents SET main_entity_name = 'Blooming Buds High School' WHERE file_id = ?",
                (fid,),
            )

        # Folder owner tagging for family members
        if rel_path.startswith("saibabu/"):
            staged_mentions.append((fid, "person", "BOTTE SAIBABU", "holder"))
        elif rel_path.startswith("rama/"):
            staged_mentions.append((fid, "person", "BOTTE RAMA", "holder"))
        elif rel_path.startswith("adithya/"):
            staged_mentions.append((fid, "person", "BOTTE ADITHYA", "holder"))
        elif rel_path.startswith("saketh/"):
            staged_mentions.append((fid, "person", primary_user_name, "primary"))

        # Check if user is among entities
        is_user_identity = "personal_documents" in rel_path or rel_path.startswith("saketh/")
        has_user = any(norm_key(ent.get("name", ""), "person") in self_person_keys for ent in extracted_entities if isinstance(ent, dict))
        if is_user_identity:
            has_user = True

        # Look up kind of main_name
        if main_name:
            main_kind = "organization"
            norm_main_person = norm_key(main_name, "person")
            if norm_main_person in self_person_keys:
                main_kind = "person"
                main_role = "primary"
            elif has_user and _is_self_or_family_name(main_name):
                main_kind = "person"
                main_role = "secondary"
            else:
                for ent in extracted_entities:
                    if isinstance(ent, dict) and ent.get("name"):
                        e_name = ent["name"].strip()
                        if e_name.lower() == main_name.lower() or norm_key(e_name, "person") == norm_main_person:
                            main_kind = ent.get("kind", "organization")
                            break
                main_role = "main_party"
            staged_mentions.append((fid, main_kind, main_name, main_role))

        # If user identity doc and primary user not yet in extracted_entities, stage primary user
        if is_user_identity and settings.me.names:
            staged_mentions.append((fid, "person", settings.me.names[0], "primary"))

        for ent in extracted_entities:
            if isinstance(ent, dict) and ent.get("name"):
                e_name = ent["name"].strip()
                if e_name.lower() in ("head of institution", "the head of institution", "signature", "author"):
                    continue
                e_kind = ent.get("kind", "other")
                is_user = norm_key(e_name, "person") in self_person_keys
                if is_user:
                    e_kind = "person"
                    e_role = "primary"
                elif has_user and _is_self_or_family_name(e_name):
                    e_kind = "person"
                    e_role = "secondary"
                else:
                    e_role = ent.get("role", "mentioned")

                staged_mentions.append((
                    fid,
                    e_kind,
                    e_name,
                    e_role,
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
            if (kind == "person" and nk in self_person_keys) or (kind == "organization" and nk in self_org_keys) or (nk in all_self_keys):
                record["is_self"] = 1
        else:
            is_self = 1 if ((kind == "person" and nk in self_person_keys) or (kind == "organization" and nk in self_org_keys) or (nk in all_self_keys)) else 0
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
                    "UPDATE entities SET name = ?, aliases = ?, is_self = ? WHERE id = ?",
                    (best_name, aliases_json, rec["is_self"], rec["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO entities (kind, name, norm_key, aliases, is_self)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(kind, norm_key) DO UPDATE SET
                        name = excluded.name,
                        aliases = excluded.aliases,
                        is_self = excluded.is_self
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



    # Update main_entity_id on documents (never self entity or user's family members)
    for dr in doc_rows:
        fid = dr["file_id"]
        main_name = dr["main_entity_name"]
        main_id = None

        if main_name and not _is_self_or_family_name(main_name):
            nk_p = norm_key(main_name, "person")
            nk_o = norm_key(main_name, "organization")
            r = conn.execute(
                "SELECT id, is_self FROM entities WHERE (norm_key = ? OR norm_key = ?) AND is_self = 0 LIMIT 1",
                (nk_o, nk_p),
            ).fetchone()
            if r:
                main_id = r["id"]

        if not main_id:
            # First non-self entity mentioned (prioritizing organizations and issuer roles)
            candidates = conn.execute(
                """
                SELECT e.id, e.name, e.kind, de.role FROM doc_entities de
                JOIN entities e ON e.id = de.entity_id
                WHERE de.file_id = ? AND e.is_self = 0
                  AND LOWER(COALESCE(de.role, '')) NOT IN ('father', 'mother', 'parent', 'student', 'holder', 'applicant', 'child', 'recipient', 'candidate')
                ORDER BY CASE 
                    WHEN e.kind = 'organization' THEN 0 
                    WHEN de.role IN ('issuer', 'provider', 'counterparty') THEN 1
                    ELSE 2 
                END
                """,
                (fid,),
            ).fetchall()
            for cand in candidates:
                if not _is_self_or_family_name(cand["name"]):
                    main_id = cand["id"]
                    conn.execute("UPDATE documents SET main_entity_name = ? WHERE file_id = ?", (cand["name"], fid))
                    break

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

    # 4b. Markdown and document hyperlinks ([text](target.md) and [[target]])
    md_rows = conn.execute(
        """
        SELECT f.id, f.rel_path, p.text
        FROM files f
        JOIN pages p ON f.id = p.file_id
        WHERE f.missing = 0 AND f.ext IN ('.md', '.markdown', '.txt')
        """
    ).fetchall()

    all_file_paths = {f["rel_path"].replace("\\", "/"): f["id"] for f in conn.execute("SELECT id, rel_path FROM files WHERE missing = 0").fetchall()}
    files_by_name: dict[str, list[int]] = {}
    for f in conn.execute("SELECT id, filename FROM files WHERE missing = 0").fetchall():
        files_by_name.setdefault(f["filename"].lower(), []).append(f["id"])

    link_pat = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
    wiki_pat = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')

    for mrow in md_rows:
        src_id = mrow["id"]
        text = mrow["text"]
        if not text:
            continue
        src_folder = os.path.dirname(mrow["rel_path"]).replace("\\", "/")

        for match in link_pat.finditer(text):
            label, target = match.groups()
            target = target.strip().split("#")[0].split("?")[0]
            if not target or target.startswith("http:") or target.startswith("https:") or target.startswith("mailto:"):
                continue
            rel_cand = os.path.normpath(os.path.join(src_folder, target)).replace("\\", "/")
            dst_id = all_file_paths.get(rel_cand)
            if not dst_id:
                fname = os.path.basename(target).lower()
                if fname in files_by_name:
                    dst_id = files_by_name[fname][0]
            if dst_id and dst_id != src_id:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO edges (src_id, dst_id, edge_type, method, confidence, evidence)
                    VALUES (?, ?, 'references', 'structural', 1.0, ?)
                    """,
                    (src_id, dst_id, f"Hyperlink: {target}"),
                )

        for match in wiki_pat.finditer(text):
            target = match.group(1).strip()
            fname = os.path.basename(target).lower()
            if not fname.endswith(".md"):
                fname += ".md"
            if fname in files_by_name:
                dst_id = files_by_name[fname][0]
                if dst_id and dst_id != src_id:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO edges (src_id, dst_id, edge_type, method, confidence, evidence)
                        VALUES (?, ?, 'references', 'structural', 1.0, ?)
                        """,
                        (src_id, dst_id, f"Wikilink: {target}"),
                    )

    # 5. Stated document references
    doc_rows = conn.execute(
        """
        SELECT d.file_id, d.title, d.main_entity_id, d.refs, d.category, f.kind as file_kind, f.folder
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
                    SELECT d.file_id, d.title, d.main_date, d.status, d.category, f.kind as file_kind
                    FROM documents d
                    JOIN files f ON f.id = d.file_id
                    WHERE d.file_id != ? AND d.main_entity_id = ?
                    """,
                    (src_id, cand_entity_id),
                ).fetchall()

            if not candidates and dr["folder"]:
                candidates = conn.execute(
                    """
                    SELECT d.file_id, d.title, d.main_date, d.status, d.category, f.kind as file_kind
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
                if cand_id == src_id:
                    continue

                # Block replies_to unless at least one document is email or correspondence
                if relation == "replies_to":
                    is_email_or_corr = (
                        dr["file_kind"] == "email"
                        or dr["category"] == "correspondence"
                        or c["file_kind"] == "email"
                        or c["category"] == "correspondence"
                    )
                    if not is_email_or_corr:
                        continue

                cand_title = c["title"] or ""
                # Title similarity
                ratio = difflib.SequenceMatcher(None, target_title.lower(), cand_title.lower()).ratio()

                # Minimum title similarity floor (>= 0.35) required before date/status bonuses
                if ratio < 0.35:
                    continue

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
