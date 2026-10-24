"""Tier 2 structured card extraction and materialization (Section 11)."""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from filegraph.cards import Card, Registry, compile_tool, normalize_fact_key, system_prompt
from filegraph.config import Settings
from filegraph.db import connect, now
from filegraph.inputs import build_blocks
from filegraph.llm import call_tool, model_for_policy, supports_vision
from filegraph.router import route

_FAKE_LLM = os.environ.get("FG_FAKE_LLM", "0") == "1"


def _clean_date(d_str: str | None, quote: str | None = None) -> tuple[str | None, str | None, str]:
    """Clean date string into (ISO_date, reason_if_any, basis).

    YYYY-MM -> YYYY-MM-01 + reason + basis='partial'
    YYYY-MM-DD -> YYYY-MM-DD + basis='stated'
    invalid -> (None, reason, 'stated')
    Rejects Today header dates and validates calendar day/month ranges.
    Normalizes academic/fiscal year ranges like '2021-22' to '2021-01-01'.
    """
    if not d_str:
        return None, None, "stated"

    if quote and ("today:" in quote.lower() or quote.strip().lower().startswith("today")):
        return None, "rejected date extracted from Today header", "stated"

    s = d_str.strip()
    if s.lower().startswith("today"):
        return None, "rejected date extracted from Today header", "stated"

    # Academic year ranges: 2021-22 or 2021-2022
    m_acad = re.match(r"^(\d{4})[-/](\d{2}|\d{4})$", s)
    if m_acad:
        yr1 = int(m_acad.group(1))
        yr2_str = m_acad.group(2)
        yr2 = int(yr2_str) if len(yr2_str) == 4 else (yr1 // 100 * 100 + int(yr2_str))
        if yr2 in (yr1 + 1, yr1):
            return f"{yr1:04d}-01-01", f"academic year range {s} normalized to start of year", "partial"

    # YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            dt = datetime.date.fromisoformat(s)
            return dt.isoformat(), None, "stated"
        except ValueError:
            return None, f"invalid calendar date: '{s}'", "stated"

    # YYYY-MM
    m_ym = re.match(r"^(\d{4})-(\d{2})$", s)
    if m_ym:
        yr = int(m_ym.group(1))
        mo = int(m_ym.group(2))
        if 1 <= mo <= 12:
            return f"{yr:04d}-{mo:02d}-01", "only month and year known", "partial"
        else:
            return None, f"invalid month in date: '{s}'", "stated"

    # DD/MM/YYYY or MM/DD/YYYY fallback
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m:
        p1, p2, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        candidate_dates = []
        if p1 > 12:
            candidate_dates.append((yr, p2, p1))
        else:
            candidate_dates.append((yr, p1, p2))
            candidate_dates.append((yr, p2, p1))

        for y, m_val, d_val in candidate_dates:
            try:
                dt = datetime.date(y, m_val, d_val)
                return dt.isoformat(), None, "inferred"
            except ValueError:
                continue

    return None, f"invalid date: '{d_str}'", "stated"


def _parse_number(val: Any) -> float | None:
    """Parse numeric amount from string or number, handling commas and currency signs."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    # Strip currency symbols and letters
    s = re.sub(r"[^\d.,\-+]", "", s)
    if not s:
        return None

    # Handle Indian numbering and thousand separators: 1,20,000 or 120,000.50
    # If both . and , exist, the last one is the decimal point
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # European format 1.200,50
            s = s.replace(".", "").replace(",", ".")
        else:
            # Standard or Indian format 1,20,000.50
            s = s.replace(",", "")
    elif "," in s:
        # e.g. 1,20,000 -> 120000
        s = s.replace(",", "")

    try:
        return float(s)
    except ValueError:
        return None


def repair_and_validate(data: dict[str, Any], tool_schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate data against tool JSON Schema, repairing errors rather than failing (Section 11.5)."""
    schema = tool_schema.get("input_schema", {})
    props = schema.get("properties", {})
    reasons: list[str] = []
    clean: dict[str, Any] = {}

    # Alias type/doc_type -> category if category is in schema and missing in data
    if "category" in props and "category" not in data:
        for alias in ("type", "doc_type", "document_type", "classification"):
            if alias in data and data[alias]:
                data["category"] = data[alias]
                reasons.append(f"schema repair: mapped '{alias}' -> 'category'")
                break

    for k, v in data.items():
        if k not in props:
            reasons.append(f"schema repair: dropped unknown key '{k}'")
            continue

        p_schema = props[k]
        allowed_enum = p_schema.get("enum")
        if allowed_enum and v not in allowed_enum:
            fallback = "other" if "other" in allowed_enum else ("unknown" if "unknown" in allowed_enum else allowed_enum[0])
            clean[k] = fallback
            reasons.append(f"schema repair: invalid enum value '{v}' for '{k}' replaced with '{fallback}'")
            continue

        clean[k] = v

    # Ensure required fields have valid defaults
    for req in schema.get("required", []):
        if req not in clean or clean[req] is None or (isinstance(clean[req], str) and not clean[req].strip()):
            p_schema = props.get(req, {})
            if "enum" in p_schema:
                allowed = p_schema["enum"]
                fallback = "other" if "other" in allowed else ("unknown" if "unknown" in allowed else allowed[0])
                clean[req] = fallback
                reasons.append(f"schema repair: provided default '{fallback}' for missing required '{req}'")
            elif p_schema.get("type") == "string":
                clean[req] = "unknown"
                reasons.append(f"schema repair: provided default for missing required '{req}'")
            elif p_schema.get("type") == "array":
                clean[req] = []
                reasons.append(f"schema repair: provided default for missing required '{req}'")
            elif p_schema.get("type") == "object":
                clean[req] = {}
                reasons.append(f"schema repair: provided default for missing required '{req}'")

    return clean, reasons


def materialize(
    conn: Any,
    file_id: int,
    card: Card,
    raw_data: dict[str, Any],
    model_name: str,
    settings: Settings,
    forced_reasons: list[str] | None = None,
) -> None:
    """Materialize validated extraction data into documents, doc_dates, and doc_facts (Section 11.5)."""
    tool_spec = compile_tool(card)
    data, repair_reasons = repair_and_validate(raw_data, tool_spec)

    review_reasons: list[str] = list(data.get("review_reasons", []))
    if forced_reasons:
        review_reasons.extend(forced_reasons)
    review_reasons.extend(repair_reasons)

    # Validate and repair title and summary
    title = data.get("title")
    summary = data.get("summary")
    if not title or title.strip().lower() in ("unknown", "untitled", ""):
        stem = frow["filename"].rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
        data["title"] = stem
        title = stem
        review_reasons.append(f"schema repair: title repaired from filename '{stem}'")

    if not summary or summary.strip().lower() in ("unknown", ""):
        data["summary"] = f"Document: {data.get('title') or frow['filename']}"
        summary = data["summary"]
        review_reasons.append("schema repair: summary generated from title")

    # Clean dates
    cleaned_dates: list[dict[str, Any]] = []
    for d in data.get("dates", []):
        if not isinstance(d, dict):
            continue
        c_date, d_reason, basis_override = _clean_date(d.get("date"), quote=str(d.get("quote", "")))
        if d_reason:
            review_reasons.append(f"{d.get('label', 'date')} date: {d_reason}")
        if not c_date:
            continue
        cleaned_dates.append({
            "label": d.get("label", "other"),
            "label_detail": d.get("label_detail"),
            "date": c_date,
            "basis": basis_override if basis_override == "partial" else d.get("basis", "stated"),
            "quote": str(d.get("quote", ""))[:300],
            "page": d.get("page"),
        })

    # Pick main_date according to card priority
    main_date = None
    main_date_label = None

    priority_labels: list[str] = []
    if isinstance(card.main_date, list):
        priority_labels = card.main_date
    elif isinstance(card.main_date, str):
        priority_labels = [card.main_date]
    if card.name == "generic" and not priority_labels:
        priority_labels = ["expires", "due", "effective", "start", "issued", "event", "signed"]

    for lbl in priority_labels:
        for cd in cleaned_dates:
            if cd["label"] == lbl:
                main_date = cd["date"]
                main_date_label = lbl
                break
        if main_date:
            break

    if not main_date and cleaned_dates:
        main_date = cleaned_dates[0]["date"]
        main_date_label = cleaned_dates[0]["label"]

    # Pick main_entity (never self entity or user's family members)
    from filegraph.link import norm_key
    self_person_keys = {norm_key(n, "person") for n in settings.me.names if n}
    self_org_keys = {norm_key(o, "organization") for o in settings.me.organizations if o}
    all_self_keys = self_person_keys | self_org_keys

    def _is_self_or_family(name_str: str) -> bool:
        if not name_str:
            return True
        nk_p = norm_key(name_str, "person")
        nk_o = norm_key(name_str, "organization")
        if nk_p in all_self_keys or nk_o in all_self_keys:
            return True
        for sn in settings.me.names + settings.me.organizations:
            if sn and sn.lower() in name_str.lower():
                return True
        return False

    raw_main = data.get("main_entity")
    main_entity_name = None
    if raw_main and not _is_self_or_family(raw_main):
        main_entity_name = raw_main.strip()

    entities_list = data.get("entities", [])
    if not main_entity_name and entities_list:
        # Priority 1: issuer/provider/counterparty/organization
        for ent in entities_list:
            if not isinstance(ent, dict):
                continue
            ename = ent.get("name", "").strip()
            erole = (ent.get("role") or "").lower()
            ekind = ent.get("kind", "")
            if not ename or _is_self_or_family(ename):
                continue
            if erole in ("issuer", "provider", "counterparty", "bank", "insurer", "company", "employer", "landlord", "author") or ekind == "organization":
                main_entity_name = ename
                break

        # Priority 2: any non-self non-family entity
        if not main_entity_name:
            for ent in entities_list:
                if not isinstance(ent, dict):
                    continue
                ename = ent.get("name", "").strip()
                erole = (ent.get("role") or "").lower()
                if not ename or _is_self_or_family(ename):
                    continue
                if erole in ("father", "mother", "parent", "holder", "student", "applicant", "patient", "employee"):
                    continue
                main_entity_name = ename
                break

    # Apply review rules
    rules = card.review_rules
    if rules:
        # required_dates
        req_dates = rules.get("required_dates", [])
        for r_spec in req_dates:
            r_lbl = r_spec.get("label")
            unless_st = r_spec.get("unless_status", [])
            for_cats = r_spec.get("for_categories")

            curr_status = data.get("status")
            curr_cat = data.get("category")

            if curr_status in unless_st:
                continue
            if for_cats and curr_cat not in for_cats:
                continue

            found = any(cd["label"] == r_lbl for cd in cleaned_dates)
            if not found:
                review_reasons.append(f"no {r_lbl} date found")

        # flag_values
        flag_vals = rules.get("flag_values", [])
        for f_spec in flag_vals:
            field_name = f_spec.get("field")
            bad_vals = f_spec.get("values", [])
            reason_msg = f_spec.get("reason", f"{field_name} is flagged")

            val = data.get(field_name) or data.get("fields", {}).get(field_name)
            if val in bad_vals:
                review_reasons.append(reason_msg)

    conf = data.get("confidence", "medium")
    if conf == "low":
        review_reasons.append("low extraction confidence")

    needs_review = 1 if len(review_reasons) > 0 else 0

    # Extras JSON: card fields + staging entities
    extras_dict = dict(data.get("fields", {}))
    extras_dict["_entities"] = entities_list

    # Delete existing document records
    conn.execute("DELETE FROM documents WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM doc_dates WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM doc_facts WHERE file_id = ?", (file_id,))

    # Insert document
    conn.execute(
        """
        INSERT INTO documents (
            file_id, card_type, card_version, category, title, summary, status,
            main_entity_name, main_date, main_date_label, confidence,
            needs_review, review_reasons, suggested_type, extras, refs, model, extracted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file_id,
            card.name,
            card.version,
            data.get("category"),
            data.get("title") or "Untitled",
            data.get("summary"),
            data.get("status") or "unknown",
            main_entity_name,
            main_date,
            main_date_label,
            conf,
            needs_review,
            json.dumps(review_reasons),
            data.get("suggested_type"),
            json.dumps(extras_dict),
            json.dumps(data.get("references", [])),
            model_name,
            now(),
        ),
    )

    # Insert doc_dates
    for cd in cleaned_dates:
        conn.execute(
            """
            INSERT INTO doc_dates (file_id, label, label_detail, date, basis, quote, page)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, cd["label"], cd["label_detail"], cd["date"], cd["basis"], cd["quote"], cd["page"]),
        )

    # Insert doc_facts
    generic_card = settings.cards_dir / "generic.yaml"
    g_card = None  # fallback normalizer handles without full generic card instance if needed
    for f in data.get("facts", []):
        if not isinstance(f, dict):
            continue
        raw_key = f.get("key", "")
        f_key = normalize_fact_key(raw_key, card, g_card)
        if not f_key:
            continue

        raw_val = f.get("value", "")
        num_val = _parse_number(f.get("number") if f.get("number") is not None else raw_val)

        conn.execute(
            """
            INSERT INTO doc_facts (file_id, key, value_text, value_num, unit, quote, page)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                f_key,
                str(raw_val),
                num_val,
                f.get("unit"),
                f.get("quote"),
                f.get("page"),
            ),
        )

    # Mark tier = 2
    conn.execute("UPDATE files SET tier = 2 WHERE id = ?", (file_id,))


def materialize_deterministic(
    conn: Any,
    file_id: int,
    card_type: str,
    frow: Any,
) -> None:
    """Materialize deterministic card for code or metadata_only policy (Section 11.5)."""
    filename = frow["filename"]
    rel_path = frow["rel_path"]

    conn.execute("DELETE FROM documents WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM doc_dates WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM doc_facts WHERE file_id = ?", (file_id,))

    if card_type == "code":
        lang = frow["language"] or "code"
        sym_count = conn.execute("SELECT COUNT(*) as n FROM code_symbols WHERE file_id = ?", (file_id,)).fetchone()
        n_syms = sym_count["n"] if sym_count else 0

        imp_rows = conn.execute("SELECT module FROM code_imports WHERE file_id = ? LIMIT 50", (file_id,)).fetchall()
        imports = [r["module"] for r in imp_rows]

        extras = {"language": lang, "symbols": n_syms, "imports": imports}
        title = f"{Path(rel_path).parts[0]}/{filename}" if len(Path(rel_path).parts) > 1 else filename

        conn.execute(
            """
            INSERT INTO documents (
                file_id, card_type, card_version, category, title, summary, status,
                confidence, needs_review, review_reasons, suggested_type, extras, refs, model, extracted_at
            ) VALUES (?, 'code', 1, ?, ?, NULL, 'unknown', 'high', 0, '[]', 'code_file', ?, '[]', 'deterministic', ?)
            """,
            (file_id, lang, title, json.dumps(extras), now()),
        )
    else:
        # metadata_only
        conn.execute(
            """
            INSERT INTO documents (
                file_id, card_type, card_version, category, title, summary, status,
                confidence, needs_review, review_reasons, suggested_type, extras, refs, model, extracted_at
            ) VALUES (?, 'generic', 1, 'other', ?, NULL, 'unknown', 'low', 1,
                     '["not analysed (metadata_only root)"]', 'document', '{}', '[]', 'deterministic', ?)
            """,
            (file_id, filename, now()),
        )

    conn.execute("UPDATE files SET tier = 2 WHERE id = ?", (file_id,))


def materialize_stub(
    conn: Any,
    file_id: int,
    card_type: str,
    frow: Any,
    reason: str = "scanned file skipped: model lacks vision",
) -> None:
    """Materialize stub card for scanned documents when model has no vision (Section 11.3)."""
    filename = frow["filename"]
    conn.execute("DELETE FROM documents WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM doc_dates WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM doc_facts WHERE file_id = ?", (file_id,))
    conn.execute(
        """
        INSERT INTO documents (
            file_id, card_type, card_version, category, title, summary, status,
            confidence, needs_review, review_reasons, suggested_type, extras, refs, model, extracted_at
        ) VALUES (?, ?, 1, 'other', ?, 'Scanned document skipped because extraction model lacks vision capability.', 'unknown',
                 'low', 1, ?, 'scanned_document', '{}', '[]', 'stub', ?)
        """,
        (file_id, card_type, filename, json.dumps([reason]), now()),
    )
    conn.execute("UPDATE files SET tier = 2 WHERE id = ?", (file_id,))


def materialize_all(conn: Any, registry: Registry, settings: Settings) -> int:
    """Fill documents for duplicates and missing card synchronisation (Section 11.5 item 8)."""
    # Delete cards of missing files
    conn.execute("DELETE FROM documents WHERE file_id IN (SELECT id FROM files WHERE missing = 1)")
    conn.execute("DELETE FROM doc_dates WHERE file_id NOT IN (SELECT file_id FROM documents)")
    conn.execute("DELETE FROM doc_facts WHERE file_id NOT IN (SELECT file_id FROM documents)")

    # Propagate cards to duplicate files that have identical sha256
    dup_rows = conn.execute(
        """
        SELECT f.id as file_id, f.sha256, (
            SELECT d.file_id FROM documents d
            JOIN files f2 ON f2.id = d.file_id
            WHERE f2.sha256 = f.sha256 AND f2.missing = 0 AND d.file_id != f.id
            LIMIT 1
        ) as source_file_id
        FROM files f
        WHERE f.missing = 0 AND f.sha256 IS NOT NULL
          AND f.id NOT IN (SELECT file_id FROM documents)
          AND source_file_id IS NOT NULL
        """
    ).fetchall()

    copied_count = 0
    for r in dup_rows:
        dst_id = r["file_id"]
        src_id = r["source_file_id"]
        src_doc = conn.execute("SELECT * FROM documents WHERE file_id = ?", (src_id,)).fetchone()
        if not src_doc:
            continue

        conn.execute(
            """
            INSERT INTO documents (
                file_id, card_type, card_version, category, title, summary, status,
                main_entity_name, main_entity_id, main_date, main_date_label, confidence,
                needs_review, review_reasons, suggested_type, extras, refs, model, extracted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dst_id,
                src_doc["card_type"],
                src_doc["card_version"],
                src_doc["category"],
                src_doc["title"],
                src_doc["summary"],
                src_doc["status"],
                src_doc["main_entity_name"],
                src_doc["main_entity_id"],
                src_doc["main_date"],
                src_doc["main_date_label"],
                src_doc["confidence"],
                src_doc["needs_review"],
                src_doc["review_reasons"],
                src_doc["suggested_type"],
                src_doc["extras"],
                src_doc["refs"],
                src_doc["model"],
                now(),
            ),
        )

        # Copy dates & facts
        for d in conn.execute("SELECT * FROM doc_dates WHERE file_id = ?", (src_id,)).fetchall():
            conn.execute(
                "INSERT INTO doc_dates (file_id, label, label_detail, date, basis, quote, page) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (dst_id, d["label"], d["label_detail"], d["date"], d["basis"], d["quote"], d["page"]),
            )
        for f in conn.execute("SELECT * FROM doc_facts WHERE file_id = ?", (src_id,)).fetchall():
            conn.execute(
                "INSERT INTO doc_facts (file_id, key, value_text, value_num, unit, quote, page) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (dst_id, f["key"], f["value_text"], f["value_num"], f["unit"], f["quote"], f["page"]),
            )

        conn.execute("UPDATE files SET tier = 2 WHERE id = ?", (dst_id,))
        copied_count += 1

    conn.commit()
    return copied_count


def extract_sync(
    settings: Settings,
    limit: int | None = None,
    card_filter: str | None = None,
    root_filter: str | None = None,
    model_override: str | None = None,
    refresh: bool = False,
    redo_low: bool = False,
) -> dict[str, Any]:
    """Execute Tier 2 card extraction pipeline synchronously (Section 11.4)."""
    conn = connect(settings.db_path)
    registry = Registry(settings.cards_dir) if isinstance(settings.cards_dir, dict) else None
    if not registry:
        from filegraph.cards import load_registry
        registry = load_registry(settings.cards_dir)

    stats = {"extracted": 0, "skipped_cached": 0, "errors": 0, "deterministic": 0}

    # If redo_low, delete low-confidence documents
    if redo_low:
        conn.execute(
            """
            DELETE FROM documents WHERE confidence = 'low'
            """
        )
        conn.commit()

    # Query pending candidates: one representative per unique sha256 (Section 11.2)
    query = """
        SELECT f.id, f.root_id, f.rel_path, f.folder, f.filename, f.ext, f.kind,
               f.size, f.mtime, f.sha256, f.source, f.cache_path, f.parent_id, f.repo_id,
               f.language, f.page_count, f.is_scanned, f.scan_pages, f.flags, f.email_meta,
               r.name as root_name, r.profile, r.llm_policy
        FROM files f
        JOIN roots r ON r.id = f.root_id
        WHERE f.missing = 0
          AND f.parse_status = 'ok'
          AND f.id = (
              SELECT MIN(f2.id) FROM files f2
              WHERE f2.sha256 = f.sha256 AND f2.missing = 0 AND f2.parse_status = 'ok'
          )
    """
    params: list[Any] = []
    if root_filter:
        query += " AND r.name = ?"
        params.append(root_filter)

    candidates = conn.execute(query, params).fetchall()

    # Filter candidates
    todo_files: list[tuple[Any, Card, str, str]] = []  # (frow, card, how, model_key)

    for frow in candidates:
        card_name, how = route(conn, frow, registry, settings)
        card = registry.get(card_name) or registry["generic"]

        if card_filter and card_name != card_filter:
            continue

        # Deterministic cards
        if how == "code" or how == "policy" or not card.llm:
            # Check if document already exists
            doc_exists = conn.execute("SELECT file_id FROM documents WHERE file_id = ?", (frow["id"],)).fetchone()
            if not doc_exists or refresh:
                materialize_deterministic(conn, frow["id"], card_name, frow)
                stats["deterministic"] += 1
            else:
                stats["skipped_cached"] += 1
            continue

        # If document already extracted with same card version, skip (cached)
        doc_row = conn.execute(
            "SELECT card_type, card_version FROM documents WHERE file_id = ?", (frow["id"],)
        ).fetchone()
        if doc_row and doc_row["card_type"] == card.name and doc_row["card_version"] == card.version and not refresh:
            stats["skipped_cached"] += 1
            continue

        # Check extraction cache
        sha = frow["sha256"]
        cached = conn.execute(
            "SELECT data, model FROM extraction_cache WHERE sha256 = ? AND card_type = ? AND card_version = ?",
            (sha, card.name, card.version),
        ).fetchone()

        if cached and not refresh:
            # Materialize from cache
            try:
                c_data = json.loads(cached["data"])
                materialize(conn, frow["id"], card, c_data, cached["model"], settings)
                stats["skipped_cached"] += 1
                conn.commit()
                continue
            except Exception:
                pass

        # Select model
        policy = frow["llm_policy"] or "cloud"
        model_key = model_override or model_for_policy(policy, "extract")

        # Check if scanned document or image with 0 text chars and model lacks vision
        text_chars_val = 0
        p_row = conn.execute("SELECT SUM(LENGTH(text)) as tc FROM pages WHERE file_id = ?", (frow["id"],)).fetchone()
        if p_row and p_row["tc"]:
            text_chars_val = p_row["tc"]

        is_img_file = bool(frow["ext"] and frow["ext"].lower() in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"))
        if (frow["is_scanned"] == 1 or is_img_file) and text_chars_val == 0 and not supports_vision(model_key):
            materialize_stub(conn, frow["id"], card.name, frow, reason="scanned file skipped: model lacks vision")
            stats["deterministic"] += 1
            continue

        todo_files.append((frow, card, how, model_key))
        if limit is not None and len(todo_files) >= limit:
            break

    if limit is not None and len(todo_files) > limit:
        todo_files = todo_files[:limit]

    try:
        for idx, (frow, card, how, model_key) in enumerate(todo_files, start=1):
            file_id = frow["id"]
            rel = frow["rel_path"]
            print(f"[{idx}/{len(todo_files)}] Extracting {card.name} card: {rel}...", flush=True)

            try:
                content, forced_reasons = build_blocks(conn, frow, frow["root_name"], model_key, settings)
                tool = compile_tool(card)
                sys_prompt = system_prompt(card, settings)

                res_args, usage = call_tool(model_key, tool, content, system=sys_prompt)

                in_tokens = usage.get("input_tokens", 0)
                out_tokens = usage.get("output_tokens", 0)

                # Store in extraction_cache
                conn.execute(
                    """
                    INSERT INTO extraction_cache (
                        sha256, card_type, card_version, model, data, input_tokens, output_tokens, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sha256, card_type, card_version) DO UPDATE SET
                        model = excluded.model,
                        data = excluded.data,
                        input_tokens = excluded.input_tokens,
                        output_tokens = excluded.output_tokens,
                        created_at = excluded.created_at
                    """,
                    (
                        frow["sha256"],
                        card.name,
                        card.version,
                        model_key,
                        json.dumps(res_args),
                        in_tokens,
                        out_tokens,
                        now(),
                    ),
                )

                # Materialize
                materialize(conn, file_id, card, res_args, model_key, settings, forced_reasons=forced_reasons)
                conn.commit()
                stats["extracted"] += 1
                print(f"[{idx}/{len(todo_files)}] Extracted {card.name} card: {rel}")

            except KeyboardInterrupt:
                print(f"\nExtraction cancelled. Finished {stats['extracted']} documents already saved.", file=sys.stderr)
                break
            except Exception as exc:
                stats["errors"] += 1
                conn.execute(
                    "INSERT INTO extraction_errors (file_id, stage, error, at) VALUES (?, 'extract', ?, ?)",
                    (file_id, str(exc), now()),
                )
                conn.commit()
                print(f"Warning: extraction error on {rel}: {exc}", file=sys.stderr)

    finally:
        # Populate duplicates for identical files
        materialize_all(conn, registry, settings)
        conn.close()

    return stats
