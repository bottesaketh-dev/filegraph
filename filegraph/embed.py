"""Vector embeddings for units and documents (Section 13.1)."""

from __future__ import annotations

import os
from typing import Any

import numpy as np

from filegraph.config import Settings
from filegraph.db import connect

_EMBED_WARNING_SHOWN = False


class LocalEmbedder:
    """Wrapper for sentence-transformers model."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer
        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device="cpu")
        self.dim = self.model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
        )


def get_embedder(settings: Settings) -> Any | None:
    """Get embeddings provider based on settings / env (Section 13.1)."""
    global _EMBED_WARNING_SHOWN

    provider = os.environ.get("FG_EMBED_PROVIDER", "local").lower()
    if provider == "none":
        return None

    if provider == "local":
        model_name = os.environ.get("FG_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
        try:
            return LocalEmbedder(model_name)
        except ImportError:
            if not _EMBED_WARNING_SHOWN:
                print("Semantic search disabled (install sentence-transformers to enable).")
                _EMBED_WARNING_SHOWN = True
            return None
        except Exception as exc:
            if not _EMBED_WARNING_SHOWN:
                print(f"Could not load local embedding model: {exc}")
                _EMBED_WARNING_SHOWN = True
            return None

    # Cloud provider model key from LLM_INSTANCES
    from filegraph.llm import _get_instances
    instances = _get_instances()
    if provider in instances and "EMBEDDINGS" in instances[provider]:
        return instances[provider]["EMBEDDINGS"]

    return None


def embed_query(query: str, settings: Settings) -> np.ndarray | None:
    """Embed search query into a float32 normalized vector."""
    embedder = get_embedder(settings)
    if not embedder:
        return None

    try:
        if isinstance(embedder, LocalEmbedder):
            vecs = embedder.encode([query])
            return vecs[0].astype(np.float32)
        elif hasattr(embedder, "embed_query"):
            vec = embedder.embed_query(query)
            arr = np.array(vec, dtype=np.float32)
            norm = np.linalg.norm(arr)
            return arr / norm if norm > 0 else arr
    except Exception:
        return None
    return None


def embed_units(
    conn: Any,
    settings: Settings,
    root_name: str | None = None,
    rebuild: bool = False,
) -> int:
    """Generate and store embeddings for all pending units and card summaries (Section 13.1)."""
    embedder = get_embedder(settings)
    if not embedder:
        return 0

    model_name = getattr(embedder, "model_name", "local_embed")
    dim = getattr(embedder, "dim", 384)

    if rebuild:
        conn.execute("DELETE FROM embeddings WHERE model = ?", (model_name,))
        conn.commit()

    # 1. Collect pending units (page >= 1)
    query_pages = """
        SELECT p.file_id, p.page, p.label, p.text, f.rel_path
        FROM pages p
        JOIN files f ON f.id = p.file_id
        JOIN roots r ON r.id = f.root_id
        WHERE f.missing = 0
    """
    params_p: list[Any] = []
    if root_name:
        query_pages += " AND r.name = ?"
        params_p.append(root_name)

    query_pages += """
        AND NOT EXISTS (
            SELECT 1 FROM embeddings e
            WHERE e.file_id = p.file_id AND e.page = p.page AND e.model = ?
        )
    """
    params_p.append(model_name)

    pending_pages = conn.execute(query_pages, params_p).fetchall()

    # 2. Collect card summaries (page = 0)
    query_cards = """
        SELECT d.file_id, d.title, d.summary, d.main_entity_name, d.main_date, f.rel_path
        FROM documents d
        JOIN files f ON f.id = d.file_id
        JOIN roots r ON r.id = f.root_id
        WHERE f.missing = 0
    """
    params_c: list[Any] = []
    if root_name:
        query_cards += " AND r.name = ?"
        params_c.append(root_name)

    query_cards += """
        AND NOT EXISTS (
            SELECT 1 FROM embeddings e
            WHERE e.file_id = d.file_id AND e.page = 0 AND e.model = ?
        )
    """
    params_c.append(model_name)

    pending_cards = conn.execute(query_cards, params_c).fetchall()

    # Prepare batches: (file_id, page, text_to_embed)
    items: list[tuple[int, int, str]] = []

    for r in pending_pages:
        txt = (r["text"] or "")[:2000]
        text_to_embed = f"{r['rel_path']} | {r['label'] or ''} | {txt}"
        items.append((r["file_id"], r["page"], text_to_embed))

    for r in pending_cards:
        parts = [
            r["rel_path"],
            r["title"] or "",
            r["summary"] or "",
            r["main_entity_name"] or "",
            r["main_date"] or "",
        ]
        text_to_embed = " | ".join(p for p in parts if p)
        items.append((r["file_id"], 0, text_to_embed))

    if not items:
        return 0

    batch_size = 64
    count = 0

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        batch_texts = [it[2] for it in batch]

        try:
            if isinstance(embedder, LocalEmbedder):
                vecs = embedder.encode(batch_texts)
            elif hasattr(embedder, "embed_documents"):
                raw_vecs = embedder.embed_documents(batch_texts)
                vecs = np.array(raw_vecs, dtype=np.float32)
                # L2 normalize
                norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                vecs = vecs / norms
            else:
                break
        except Exception:
            break

        for (fid, page, _), vec in zip(batch, vecs):
            vec_bytes = np.ascontiguousarray(vec, dtype=np.float32).tobytes()
            conn.execute(
                """
                INSERT OR REPLACE INTO embeddings (file_id, page, model, dim, vector)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fid, page, model_name, dim, vec_bytes),
            )
            count += 1

        conn.commit()

    return count
