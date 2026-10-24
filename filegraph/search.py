"""Hybrid BM25 + Vector search with Reciprocal Rank Fusion (Section 13.2)."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import numpy as np

from filegraph.config import Settings
from filegraph.embed import embed_query, get_embedder


def _escape_fts5_query(query: str) -> str:
    """Sanitize query for FTS5 syntax, falling back to quoted words with OR."""
    words = re.findall(r"\w+", query)
    if not words:
        return '""'
    return " OR ".join(f'"{w}"' for w in words)


def hybrid_search(
    conn: Any,
    query: str,
    limit: int = 10,
    kind: str | None = None,
    root: str | None = None,
    card_type: str | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Execute hybrid search combining BM25 and Vector search via RRF (Section 13.2)."""
    if not query.strip():
        return []

    rrf_k = settings.search.rrf_k if settings else 60
    bm25_candidates_limit = settings.search.bm25_candidates if settings else 50
    vec_candidates_limit = settings.search.vector_candidates if settings else 50

    # 1. BM25 Search
    bm25_results: list[tuple[int, int, str, float]] = []  # (file_id, page, snippet, score)
    try:
        sql = """
            SELECT file_id, page, snippet(fts, 4, '[', ']', ' … ', 18) as snip, bm25(fts) as score
            FROM fts
            WHERE fts MATCH ?
            ORDER BY score ASC
            LIMIT ?
        """
        rows = conn.execute(sql, (query, bm25_candidates_limit)).fetchall()
        for r in rows:
            bm25_results.append((r["file_id"], r["page"], r["snip"], float(r["score"])))
    except Exception:
        # Retry with sanitized OR query
        safe_q = _escape_fts5_query(query)
        try:
            rows = conn.execute(sql, (safe_q, bm25_candidates_limit)).fetchall()
            for r in rows:
                bm25_results.append((r["file_id"], r["page"], r["snip"], float(r["score"])))
        except Exception:
            pass

    # 2. Vector Search
    vec_results: list[tuple[int, int, str, float]] = []
    if settings:
        embedder = get_embedder(settings)
        if embedder:
            q_vec = embed_query(query, settings)
            if q_vec is not None:
                model_name = getattr(embedder, "model_name", "local_embed")
                emb_rows = conn.execute(
                    """
                    SELECT e.file_id, e.page, e.vector
                    FROM embeddings e
                    JOIN files f ON f.id = e.file_id
                    WHERE e.model = ? AND f.missing = 0
                    """,
                    (model_name,),
                ).fetchall()

                if emb_rows:
                    fids_pages = [(r["file_id"], r["page"]) for r in emb_rows]
                    vectors = np.frombuffer(b"".join(r["vector"] for r in emb_rows), dtype=np.float32)
                    dim = len(q_vec)
                    matrix = vectors.reshape(-1, dim)

                    # Cosine similarity = dot product with normalised query
                    scores = np.dot(matrix, q_vec)
                    top_indices = np.argsort(scores)[::-1][:vec_candidates_limit]

                    for idx in top_indices:
                        fid, p_num = fids_pages[idx]
                        sc = float(scores[idx])
                        vec_results.append((fid, p_num, "", sc))

    # 3. Reciprocal Rank Fusion (RRF)
    # unit_key -> (rrf_score, snippet, matched_by_set)
    rrf_map: dict[tuple[int, int], dict[str, Any]] = defaultdict(
        lambda: {"score": 0.0, "snippet": "", "matched_by": set()}
    )

    for rank, (fid, page, snip, _) in enumerate(bm25_results, start=1):
        key = (fid, page)
        rrf_map[key]["score"] += 1.0 / (rrf_k + rank)
        if snip:
            rrf_map[key]["snippet"] = snip
        rrf_map[key]["matched_by"].add("bm25")

    for rank, (fid, page, _, _) in enumerate(vec_results, start=1):
        key = (fid, page)
        rrf_map[key]["score"] += 1.0 / (rrf_k + rank)
        rrf_map[key]["matched_by"].add("vector")

    if not rrf_map:
        return []

    # Sort units by RRF score descending
    sorted_units = sorted(rrf_map.items(), key=lambda x: x[1]["score"], reverse=True)

    # 4. Filter and assemble results
    results: list[dict[str, Any]] = []
    seen_files = set()

    for (fid, page), udata in sorted_units:
        if fid in seen_files:
            continue

        # Look up file and document metadata
        frow = conn.execute(
            """
            SELECT f.id, f.rel_path, f.kind, f.missing, r.name as root_name,
                   d.card_type, d.title
            FROM files f
            JOIN roots r ON r.id = f.root_id
            LEFT JOIN documents d ON d.file_id = f.id
            WHERE f.id = ? AND f.missing = 0
            """,
            (fid,),
        ).fetchone()

        if not frow:
            continue

        # Apply filters
        if kind and frow["kind"] != kind:
            continue
        if root and frow["root_name"] != root:
            continue
        if card_type and (frow["card_type"] or "generic") != card_type:
            continue

        seen_files.add(fid)

        # Get page label/lines if page >= 1
        label = ""
        lines = ""
        snippet = udata["snippet"]

        if page >= 1:
            p_row = conn.execute(
                "SELECT label, text FROM pages WHERE file_id = ? AND page = ?", (fid, page)
            ).fetchone()
            if p_row:
                label = p_row["label"] or f"Page {page}"
                if not snippet and p_row["text"]:
                    snippet = p_row["text"][:200]
        else:
            label = "Card Summary"
            if not snippet and frow["title"]:
                snippet = frow["title"]

        results.append({
            "file_id": fid,
            "path": frow["rel_path"],
            "root": frow["root_name"],
            "kind": frow["kind"],
            "card_type": frow["card_type"] or "generic",
            "title": frow["title"] or frow["rel_path"],
            "unit": page,
            "label": label,
            "lines": lines,
            "snippet": snippet,
            "score": round(udata["score"], 4),
            "matched_by": sorted(list(udata["matched_by"])),
        })

        if len(results) >= limit:
            break

    return results
