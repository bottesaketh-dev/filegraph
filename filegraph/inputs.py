"""Page selection, content blocks, and input budget management (Section 11.3)."""

from __future__ import annotations

import base64
import datetime
import io
import json
import re
from pathlib import Path
from typing import Any

from filegraph.config import Settings
from filegraph.db import abs_path, page_texts
from filegraph.llm import supports_vision
from filegraph.security import redact

SIGNATURE_REGEX = re.compile(
    r"IN WITNESS WHEREOF|DocuSigned by|Signature Page|/s/|\bBy:\s*_{4,}",
    re.IGNORECASE,
)


def _render_pdf_page_image(doc: Any, page_idx: int, dpi: int = 110) -> str:
    """Render a PDF page to base64 JPEG string using pymupdf."""
    page = doc[page_idx - 1]
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("jpeg")
    return base64.b64encode(img_bytes).decode("ascii")


def _render_image_file(path: Path, max_dim: int = 1600) -> str:
    """Thumbnail an image file and return base64 JPEG string."""
    from PIL import Image

    with Image.open(str(path)) as img:
        img = img.convert("RGB")
        img.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")


def select_units(
    units: list[tuple[int, str]],
    is_pdf_or_image: bool,
    settings: Settings,
) -> list[tuple[int, str]]:
    """Select units (pages or sections) within limits (Section 11.3)."""
    if not units:
        return []

    if is_pdf_or_image:
        head = settings.extraction.head_pages
        tail = settings.extraction.tail_pages
        total = len(units)

        if total <= (head + tail):
            return units

        selected_indices = set(range(1, min(head + 1, total + 1)))
        selected_indices.update(range(max(1, total - tail + 1), total + 1))

        # Check for signature pages in the middle
        sig_count = 0
        for p_num, text in units:
            if p_num not in selected_indices:
                if SIGNATURE_REGEX.search(text):
                    selected_indices.add(p_num)
                    sig_count += 1
                    if sig_count >= 2:
                        break

        return [u for u in units if u[0] in selected_indices]

    else:
        # Text formats: up to 75% max_text_chars from start, remainder from end
        max_chars = settings.extraction.max_text_chars
        head_budget = int(max_chars * 0.75)
        tail_budget = max_chars - head_budget

        head_units: list[tuple[int, str]] = []
        head_len = 0
        for u in units:
            t_len = len(u[1])
            if head_len + t_len <= head_budget:
                head_units.append(u)
                head_len += t_len
            else:
                break

        tail_units: list[tuple[int, str]] = []
        tail_len = 0
        head_set = {u[0] for u in head_units}
        for u in reversed(units):
            if u[0] in head_set:
                break
            t_len = len(u[1])
            if tail_len + t_len <= tail_budget:
                tail_units.append(u)
                tail_len += t_len
            else:
                break
        tail_units.reverse()

        return head_units + tail_units


def build_blocks(
    conn: Any,
    frow: Any,
    root_name: str,
    model_key: str,
    settings: Settings,
) -> tuple[list[dict[str, Any]] | str, list[str]]:
    """Build LangChain content blocks or text prompt for document extraction (Section 11.3).

    Returns (content, forced_review_reasons).
    """
    forced_reasons: list[str] = []
    file_id = frow["id"]
    rel_path = frow["rel_path"]
    ext = frow["ext"] or ""
    page_count = frow["page_count"] or 0
    is_scanned = frow["is_scanned"] or 0
    flags_raw = frow["flags"] or "{}"
    flags = json.loads(flags_raw) if isinstance(flags_raw, str) else flags_raw
    email_meta_raw = frow["email_meta"]

    is_pdf_or_image = ext.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")
    units = page_texts(conn, file_id)

    selected = select_units(units, is_pdf_or_image, settings)
    selected_pages = [u[0] for u in selected]

    today_str = datetime.date.today().isoformat()
    header_lines = [
        f"Today: {today_str}",
        f"File path: {rel_path}",
        f"Root: {root_name}",
        f"File type: {ext} | Total pages: {page_count}",
        f"Included pages: {selected_pages if len(selected_pages) != page_count else 'all'}",
    ]

    if flags:
        header_lines.append(f"Parser flags: {json.dumps(flags)}")

    if email_meta_raw:
        meta_str = email_meta_raw if isinstance(email_meta_raw, str) else json.dumps(email_meta_raw)
        if len(meta_str) > 1500:
            meta_str = meta_str[:1500] + "...}"
        header_lines.append(f"Email metadata: {meta_str}")

    if frow["parent_id"]:
        parent = conn.execute("SELECT rel_path FROM files WHERE id = ?", (frow["parent_id"],)).fetchone()
        if parent:
            header_lines.append(f"Parent email: {parent['rel_path']}")

    has_vision = supports_vision(model_key)
    file_abs = abs_path(conn, frow)

    # Scanned pages handling
    scan_pages_raw = frow["scan_pages"] or "[]"
    scan_pages = json.loads(scan_pages_raw) if isinstance(scan_pages_raw, str) else scan_pages_raw
    scanned_in_selection = [p for p in selected_pages if p in scan_pages]

    images_to_send: list[tuple[int, str]] = []  # (page_num, base64)

    if scanned_in_selection:
        if not has_vision:
            header_lines.append("Scanned pages were not sent (text-only model).")
            forced_reasons.append("scanned pages omitted (text-only model)")
        elif file_abs and file_abs.is_file():
            max_img = settings.extraction.max_image_pages
            # Budget image pages
            if len(scanned_in_selection) <= max_img:
                img_pages = scanned_in_selection
            else:
                first_k = int(max_img * (2 / 3))
                last_k = max_img - first_k
                img_pages = scanned_in_selection[:first_k] + scanned_in_selection[-last_k:]

            try:
                if ext.lower() == ".pdf":
                    import pymupdf
                    doc = pymupdf.open(str(file_abs))
                    for pnum in img_pages:
                        b64 = _render_pdf_page_image(doc, pnum, dpi=settings.extraction.image_dpi)
                        images_to_send.append((pnum, b64))
                    doc.close()
                elif ext.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"):
                    b64 = _render_image_file(file_abs)
                    images_to_send.append((1, b64))
            except Exception:
                forced_reasons.append("failed to render scanned page images")

    # Build textual body
    body_lines = ["\n".join(header_lines), "\n--- DOCUMENT CONTENT ---\n"]
    marker_name = "Page" if is_pdf_or_image else "Section"

    for p_num, text in selected:
        body_lines.append(f"=== {marker_name} {p_num} ===")
        if text.strip():
            body_lines.append(text)
        elif p_num in scan_pages:
            body_lines.append("[Scanned / image content]")
        body_lines.append("")

    body_lines.append("--- END OF CONTENT ---")
    body_lines.append("Record this document with the record_card tool.")

    full_text = "\n".join(body_lines)
    full_text, _ = redact(full_text)

    # Budget safeguard for local models (e.g. Ollama 4k context limits)
    if model_key and ("OLLAMA" in model_key.upper() or "LOCAL" in model_key.upper()):
        if len(full_text) > 2800:
            full_text = full_text[:2000] + "\n\n...[content truncated for local context window]...\n\n" + full_text[-800:]

    # Format as blocks if images are included, else string
    if images_to_send:
        blocks: list[dict[str, Any]] = [{"type": "text", "text": full_text}]
        for pnum, b64 in images_to_send:
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64,
                },
            })
        return blocks, forced_reasons

    return full_text, forced_reasons
