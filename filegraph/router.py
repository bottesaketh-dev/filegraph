"""Document routing to appropriate card types (Section 10)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pathspec

from filegraph.cards import Card, Registry
from filegraph.config import Settings
from filegraph.db import abs_path, now, page_texts
from filegraph.llm import call_tool, model_for_policy


def _matches_rules(
    card: Card,
    rel_path: str,
    filename: str,
    ext: str,
    text_sample: str,
) -> tuple[bool, int]:
    """Score a card's match block against a file (Section 10 item 4).

    Returns (qualifies, score).
    """
    match = card.match
    if not match:
        return False, 0

    # 1. Extension match required if extensions given
    allowed_exts = match.get("extensions")
    if allowed_exts and ext.lower() not in [e.lower() for e in allowed_exts]:
        return False, 0

    score = 0
    matched_structure = False

    # 2. Path globs match on rel_path (+3)
    path_globs = match.get("path_globs")
    if path_globs:
        spec = pathspec.PathSpec.from_lines("gitwildmatch", path_globs)
        if spec.match_file(rel_path) or spec.match_file("/" + rel_path):
            score += 3
            matched_structure = True

    # 3. Filename regex match (+3)
    fn_regex = match.get("filename_regex")
    if fn_regex:
        try:
            if re.search(fn_regex, filename):
                score += 3
                matched_structure = True
        except re.error:
            pass

    # 4. Keywords match in first 5,000 characters (+1 per distinct hit)
    kw_hits = 0
    keywords = match.get("keywords_any", [])
    if keywords:
        sample_lower = text_sample[:5000].lower()
        for kw in keywords:
            if kw.lower() in sample_lower:
                kw_hits += 1
                score += 1

    min_hits = match.get("min_keyword_hits", 2)
    qualifies = matched_structure or (kw_hits >= min_hits)
    return qualifies, score


def _get_classification_tool(card_names: list[str]) -> dict[str, Any]:
    """Compile classify_document tool schema (Section 10 item 5)."""
    return {
        "name": "classify_document",
        "description": "Classify a document into a card type and suggest the most specific document type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "card_type": {"type": "string", "enum": card_names},
                "suggested_type": {
                    "type": "string",
                    "description": "Specific document type in snake_case, e.g. car_insurance_policy, salary_slip",
                },
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            },
            "required": ["card_type", "suggested_type", "confidence"],
        },
    }


def route(
    conn: Any,
    frow: Any,
    registry: Registry,
    settings: Settings,
) -> tuple[str, str]:
    """Determine the card type and routing method for a file (Section 10).

    Returns (card_type, how) where how in:
    'code' | 'profile' | 'rules' | 'llm' | 'fallback' | 'policy'.
    """
    try:
        # 1. Code files or files inside repos -> 'code'
        if frow["repo_id"] or frow["kind"] == "code":
            return "code", "code"

        # Look up root policy
        root_id = frow["root_id"]
        root_row = conn.execute(
            "SELECT profile, llm_policy FROM roots WHERE id = ?", (root_id,)
        ).fetchone()
        policy = root_row["llm_policy"] if root_row else "cloud"
        profile = root_row["profile"] if root_row else "auto"

        # 2. Root llm_policy == 'metadata_only' -> 'generic'
        if policy == "metadata_only":
            return "generic", "policy"

        # 3. Root profile != 'auto' -> that card
        if profile != "auto" and profile in registry:
            return profile, "profile"

        sha256 = frow["sha256"]
        rel_path = frow["rel_path"]
        filename = frow["filename"]
        ext = frow["ext"] or ""

        # Promotion check (between steps 3 and 4):
        # If sha256 has a documents.suggested_type or classification_cache.suggested_type
        # listed in some card's absorbs -> route to that card (how='rules')
        if sha256:
            prev_sugg = None
            doc_row = conn.execute(
                "SELECT suggested_type FROM documents WHERE file_id = ?",
                (frow["id"],),
            ).fetchone()
            if doc_row and doc_row["suggested_type"]:
                prev_sugg = doc_row["suggested_type"]
            else:
                c_row = conn.execute(
                    "SELECT suggested_type FROM classification_cache WHERE sha256 = ?",
                    (sha256,),
                ).fetchone()
                if c_row and c_row["suggested_type"]:
                    prev_sugg = c_row["suggested_type"]

            if prev_sugg:
                for cname, card in registry.cards.items():
                    if card.llm and prev_sugg in card.absorbs:
                        return cname, "rules"

        # 4. Rules-based routing
        units = page_texts(conn, frow["id"])
        text_sample = " ".join(t for _, t in units[:3]) if units else ""

        candidate_scores: list[tuple[int, int, str]] = []  # (priority, score, card_name)
        for cname, card in registry.cards.items():
            if not card.llm:
                continue
            qualifies, score = _matches_rules(card, rel_path, filename, ext, text_sample)
            if qualifies:
                candidate_scores.append((card.priority, score, cname))

        if candidate_scores:
            candidate_scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
            return candidate_scores[0][2], "rules"

        # 5. LLM classification
        if not sha256:
            return "generic", "fallback"

        reg_hash = registry.registry_hash

        # Check classification cache
        cached = conn.execute(
            "SELECT card_type, confidence FROM classification_cache WHERE sha256 = ? AND registry_hash = ?",
            (sha256, reg_hash),
        ).fetchone()
        if cached:
            if cached["confidence"] == "low":
                return "generic", "llm"
            return cached["card_type"], "llm"

        # Call classification model
        llm_cards = [cname for cname, c in registry.cards.items() if c.llm]
        tool = _get_classification_tool(llm_cards)

        email_meta = frow["email_meta"]
        subj = ""
        if email_meta:
            try:
                em = json.loads(email_meta) if isinstance(email_meta, str) else email_meta
                subj = em.get("subject", "")
            except Exception:
                pass

        sample_chars = text_sample[:3000]
        prompt = (
            f"Classify this document into one of the card types: {', '.join(llm_cards)}.\n"
            f"Path: {rel_path}\n"
            f"Extension: {ext}\n"
        )
        if subj:
            prompt += f"Subject: {subj}\n"
        prompt += f"Sample text:\n{sample_chars}\n"

        model_key = model_for_policy(policy, "classify")
        try:
            res_args, _ = call_tool(model_key, tool, prompt)
            chosen_card = res_args.get("card_type", "generic")
            sugg_type = res_args.get("suggested_type", "document")
            conf = res_args.get("confidence", "medium")

            if chosen_card not in registry:
                chosen_card = "generic"

            # Cache classification
            conn.execute(
                """
                INSERT INTO classification_cache (sha256, registry_hash, card_type, suggested_type, confidence, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sha256, registry_hash) DO UPDATE SET
                    card_type = excluded.card_type,
                    suggested_type = excluded.suggested_type,
                    confidence = excluded.confidence,
                    model = excluded.model,
                    created_at = excluded.created_at
                """,
                (sha256, reg_hash, chosen_card, sugg_type, conf, model_key, now()),
            )
            conn.commit()

            if conf == "low":
                return "generic", "llm"
            return chosen_card, "llm"

        except Exception:
            return "generic", "fallback"

    except Exception:
        return "generic", "fallback"
