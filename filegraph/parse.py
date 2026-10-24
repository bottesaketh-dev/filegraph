"""Document parsers for all supported file kinds (Section 8)."""

from __future__ import annotations

import email
import email.policy
import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from filegraph.security import redact

# Extension mappings (Section 8.1)
DOCUMENT_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".rtf", ".txt", ".md", ".markdown", ".html", ".htm", ".odt"
}
EMAIL_EXTENSIONS = {".msg", ".eml"}
DATA_EXTENSIONS = {".xlsx", ".xlsm", ".csv", ".tsv", ".json", ".yaml", ".yml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".heic"}
CODE_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt",
    ".go", ".rs", ".rb", ".php", ".cs", ".cpp", ".cc", ".cxx", ".hpp",
    ".h", ".c", ".swift", ".scala", ".sh", ".bash", ".ps1", ".sql", ".r",
    ".m", ".dart", ".lua", ".pl", ".ipynb", ".vue", ".svelte"
}
CONFIG_LIKE_IN_REPOS = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md", ".rst",
    ".txt", ".xml", ".gradle"
}
SPECIAL_CODE_FILENAMES = {"dockerfile", "makefile"}


def classify_kind(ext: str, filename: str = "", inside_repo: bool = False) -> str:
    """Classify file kind from extension and context (Section 8.1)."""
    ext_l = ext.lower()
    fname_l = filename.lower()

    if inside_repo:
        if ext_l in CODE_EXTENSIONS or ext_l in CONFIG_LIKE_IN_REPOS or fname_l in SPECIAL_CODE_FILENAMES:
            return "code"

    if ext_l in DOCUMENT_EXTENSIONS:
        return "document"
    if ext_l in EMAIL_EXTENSIONS:
        return "email"
    if ext_l in DATA_EXTENSIONS:
        return "data"
    if ext_l in IMAGE_EXTENSIONS:
        return "image"
    if ext_l in CODE_EXTENSIONS or fname_l in SPECIAL_CODE_FILENAMES:
        return "code"

    return "other"


def chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    """Chunk text into ~max_chars pieces, breaking at newline after half size (Section 8.3)."""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    half = max_chars // 2

    while len(remaining) > max_chars:
        window = remaining[half:max_chars]
        newline_pos = window.rfind("\n")
        if newline_pos != -1:
            split_at = half + newline_pos + 1
        else:
            split_at = max_chars

        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\r\n")

    if remaining.strip():
        chunks.append(remaining.strip())

    return chunks


def normalize_text(text: str, flags: dict[str, Any] | None = None) -> str:
    """Normalize text per Section 8.4: strip \\x00, normalize \\r\\n, collapse blank lines, redact."""
    if not text:
        return ""

    # Strip NULL characters
    text = text.replace("\x00", "")

    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Collapse runs of more than 3 blank lines (\n\n\n\n+ -> \n\n\n)
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    # Redact secrets
    redacted_text, n_redacted = redact(text)
    if n_redacted > 0 and flags is not None:
        flags["redacted"] = flags.get("redacted", 0) + n_redacted

    return redacted_text


# ---------------------------------------------------------------------------
# Individual parsers
# ---------------------------------------------------------------------------

def _parse_pdf(path: Path) -> dict[str, Any]:
    """Parse PDF with pymupdf (Section 8.3)."""
    import pymupdf

    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        err_msg = str(exc)
        if "password" in err_msg.lower() or "encrypted" in err_msg.lower():
            return {"ok": False, "error": "encrypted PDF (password required)"}
        return {"ok": False, "error": f"Failed to open PDF: {exc}"}

    try:
        if doc.is_encrypted and doc.needs_pass:
            return {"ok": False, "error": "encrypted PDF (password required)"}

        page_count = len(doc)
        pages: list[tuple[int, str]] = []
        labels: dict[int, str] = {}
        scan_pages: list[int] = []
        flags: dict[str, Any] = {}
        total_chars = 0

        first_3_text = []

        for i, page in enumerate(doc, start=1):
            try:
                raw_text = page.get_text()
            except Exception:
                raw_text = ""

            clean_text = normalize_text(raw_text, flags)
            non_space_chars = len(re.sub(r"\s+", "", clean_text))

            if non_space_chars < 25:
                # Scanned page
                clean_text = ""
                scan_pages.append(i)

            pages.append((i, clean_text))
            labels[i] = f"Page {i}"
            total_chars += len(clean_text)

            if i <= 3:
                first_3_text.append(clean_text)

        # Check draft marker in first 3 pages
        joined_3 = " ".join(first_3_text)
        if re.search(r"\bDRAFT\b", joined_3):
            flags["draft_marker"] = True

        is_scanned = 1 if page_count > 0 and len(scan_pages) >= (page_count / 2) else 0

        return {
            "ok": True,
            "error": None,
            "pages": pages,
            "labels": labels,
            "lines": {},
            "page_count": page_count,
            "is_scanned": is_scanned,
            "scan_pages": scan_pages,
            "text_chars": total_chars,
            "flags": flags,
            "email_meta": None,
            "attachments": [],
        }
    finally:
        doc.close()


def _parse_docx(path: Path) -> dict[str, Any]:
    """Parse DOCX with python-docx (Section 8.3)."""
    import docx

    doc = docx.Document(str(path))
    flags: dict[str, Any] = {}

    # Header and footer text
    header_footer_parts: list[str] = []
    for section in doc.sections:
        for p in section.header.paragraphs:
            if p.text.strip():
                header_footer_parts.append(p.text.strip())
        for p in section.footer.paragraphs:
            if p.text.strip():
                header_footer_parts.append(p.text.strip())

    prefix = ""
    if header_footer_parts:
        hf_str = " | ".join(header_footer_parts)
        prefix = f"[Header/footer text: {hf_str}]\n\n"

    # Inspect XML for tracked changes and comments
    xml_str = doc._element.xml
    if "<w:ins " in xml_str or "<w:del " in xml_str or "<w:delText" in xml_str:
        flags["tracked_changes"] = True
    if "<w:commentReference" in xml_str or len(getattr(doc, "comments", [])) > 0:
        flags["has_comments"] = True

    # Body paragraphs
    body_paragraphs: list[str] = []
    for p in doc.paragraphs:
        # Extract w:t skipping w:delText
        p_text_parts = []
        for t_elem in p._element.xpath(".//w:t[not(ancestor::w:del)]"):
            if t_elem.text:
                p_text_parts.append(t_elem.text)
        para_text = "".join(p_text_parts) if p_text_parts else p.text
        if para_text:
            body_paragraphs.append(para_text)

    full_body = "\n\n".join(body_paragraphs)
    if re.search(r"\bDRAFT\b", full_body):
        flags["draft_marker"] = True

    full_text = prefix + full_body
    norm = normalize_text(full_text, flags)
    chunks = chunk_text(norm, 4000)

    pages = [(i, c) for i, c in enumerate(chunks, start=1)]
    labels = {i: f"Section {i}" for i in range(1, len(chunks) + 1)}

    return {
        "ok": True,
        "error": None,
        "pages": pages,
        "labels": labels,
        "lines": {},
        "page_count": len(pages),
        "is_scanned": 0,
        "scan_pages": [],
        "text_chars": len(norm),
        "flags": flags,
        "email_meta": None,
        "attachments": [],
    }


def _parse_doc(path: Path) -> dict[str, Any]:
    """Parse legacy .doc via LibreOffice soffice conversion (Section 8.3)."""
    soffice = shutil.which("soffice")
    if not soffice:
        return {
            "ok": False,
            "unsupported": True,
            "error": "legacy .doc needs LibreOffice installed for conversion",
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            cmd = [
                soffice,
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                tmpdir,
                str(path),
            ]
            subprocess.run(cmd, check=True, timeout=180, capture_output=True)
            converted = Path(tmpdir) / (path.stem + ".docx")
            if converted.is_file():
                return _parse_docx(converted)
            return {"ok": False, "error": "LibreOffice conversion produced no output file"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "LibreOffice conversion timed out (180s)"}
        except Exception as exc:
            return {"ok": False, "error": f"LibreOffice conversion failed: {exc}"}


def _parse_msg(path: Path) -> dict[str, Any]:
    """Parse Outlook .msg with extract_msg (Section 8.3)."""
    import extract_msg

    msg = extract_msg.openMsg(str(path))
    try:
        flags: dict[str, Any] = {}
        subj = msg.subject or ""
        sender = msg.sender or ""
        to = msg.to or ""
        cc = msg.cc or ""
        date_str = str(msg.date) if msg.date else ""

        email_meta = {
            "subject": subj.replace("\x00", "").strip(),
            "from": sender.replace("\x00", "").strip(),
            "to": to.replace("\x00", "").strip(),
            "cc": cc.replace("\x00", "").strip(),
            "date": date_str.replace("\x00", "").strip(),
        }

        # Header lines
        header_lines = []
        if subj:
            header_lines.append(f"Subject: {subj}")
        if sender:
            header_lines.append(f"From: {sender}")
        if to:
            header_lines.append(f"To: {to}")
        if cc:
            header_lines.append(f"Cc: {cc}")
        if date_str:
            header_lines.append(f"Date: {date_str}")
        headers_text = "\n".join(header_lines) + "\n\n" if header_lines else ""

        # Body
        body = msg.body or ""
        if not body and msg.htmlBody:
            # Simple tag stripping fallback
            body = re.sub(r"<[^>]+>", "", msg.htmlBody.decode("utf-8", errors="replace"))

        full_text = headers_text + body
        norm = normalize_text(full_text, flags)
        chunks = chunk_text(norm, 4000)
        pages = [(i, c) for i, c in enumerate(chunks, start=1)]
        labels = {i: f"Section {i}" for i in range(1, len(chunks) + 1)}

        # Attachments
        attachments: list[tuple[str, bytes]] = []
        for att in getattr(msg, "attachments", []):
            try:
                name = (
                    getattr(att, "longFilename", None)
                    or getattr(att, "shortFilename", None)
                    or getattr(att, "name", None)
                    or "attachment"
                )
                data = att.data
                if not data:
                    continue

                # Skip images under 40 KB (signatures / logos)
                ext = Path(name).suffix.lower()
                if ext in IMAGE_EXTENSIONS and len(data) < 40 * 1024:
                    continue

                # Note embedded messages
                if getattr(att, "type", "") == "msg" or isinstance(att, extract_msg.Message):
                    embedded = email_meta.setdefault("embedded_messages_not_unpacked", [])
                    embedded.append(name)
                    continue

                attachments.append((name, data))
            except Exception:
                continue

        return {
            "ok": True,
            "error": None,
            "pages": pages,
            "labels": labels,
            "lines": {},
            "page_count": len(pages),
            "is_scanned": 0,
            "scan_pages": [],
            "text_chars": len(norm),
            "flags": flags,
            "email_meta": email_meta,
            "attachments": attachments,
        }
    finally:
        msg.close()


def _parse_eml(path: Path) -> dict[str, Any]:
    """Parse standard .eml with email stdlib (Section 8.3)."""
    flags: dict[str, Any] = {}

    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)

    subj = msg.get("Subject", "") or ""
    sender = msg.get("From", "") or ""
    to = msg.get("To", "") or ""
    cc = msg.get("Cc", "") or ""
    date_str = msg.get("Date", "") or ""

    email_meta = {
        "subject": subj.strip(),
        "from": sender.strip(),
        "to": to.strip(),
        "cc": cc.strip(),
        "date": date_str.strip(),
    }

    headers_text = (
        f"Subject: {subj}\nFrom: {sender}\nTo: {to}\nCc: {cc}\nDate: {date_str}\n\n"
    )

    body_text = ""
    attachments: list[tuple[str, bytes]] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_disposition = part.get_content_disposition()
            content_type = part.get_content_type()
            filename = part.get_filename()

            if content_disposition == "attachment" or filename:
                name = filename or "attachment"
                try:
                    payload = part.get_content()
                    if isinstance(payload, str):
                        payload_bytes = payload.encode("utf-8")
                    elif isinstance(payload, bytes):
                        payload_bytes = payload
                    else:
                        payload_bytes = part.get_payload(decode=True) or b""
                    ext = Path(name).suffix.lower()
                    if ext in IMAGE_EXTENSIONS and len(payload_bytes) < 40 * 1024:
                        continue
                    attachments.append((name, payload_bytes))
                except Exception:
                    continue
            elif content_type == "text/plain" and not body_text:
                try:
                    body_text = part.get_content()
                except Exception:
                    body_text = str(part.get_payload(decode=True) or b"", errors="replace")
            elif content_type == "text/html" and not body_text:
                try:
                    raw_html = part.get_content()
                    body_text = re.sub(r"<[^>]+>", "", raw_html)
                except Exception:
                    pass
    else:
        try:
            body_text = msg.get_content()
        except Exception:
            body_text = str(msg.get_payload(decode=True) or b"", errors="replace")

    full_text = headers_text + body_text
    norm = normalize_text(full_text, flags)
    chunks = chunk_text(norm, 4000)
    pages = [(i, c) for i, c in enumerate(chunks, start=1)]
    labels = {i: f"Section {i}" for i in range(1, len(chunks) + 1)}

    return {
        "ok": True,
        "error": None,
        "pages": pages,
        "labels": labels,
        "lines": {},
        "page_count": len(pages),
        "is_scanned": 0,
        "scan_pages": [],
        "text_chars": len(norm),
        "flags": flags,
        "email_meta": email_meta,
        "attachments": attachments,
    }


def _parse_xlsx(path: Path) -> dict[str, Any]:
    """Parse XLSX / XLSM with openpyxl (Section 8.3)."""
    import openpyxl

    flags: dict[str, Any] = {}
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    lines: list[str] = []

    try:
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            lines.append(f"## Sheet: {sheet_name}")
            row_count = 0
            for row in sheet.iter_rows(values_only=True):
                row_count += 1
                if row_count > 300:
                    continue
                cell_vals = [str(c) if c is not None else "" for c in row]
                if any(cell_vals):
                    lines.append(" | ".join(cell_vals))

            if row_count > 300:
                lines.append(f"... ({row_count - 300} more rows)")
            lines.append("")

        full_text = "\n".join(lines)
        norm = normalize_text(full_text, flags)
        chunks = chunk_text(norm, 4000)
        pages = [(i, c) for i, c in enumerate(chunks, start=1)]
        labels = {i: f"Section {i}" for i in range(1, len(chunks) + 1)}

        return {
            "ok": True,
            "error": None,
            "pages": pages,
            "labels": labels,
            "lines": {},
            "page_count": len(pages),
            "is_scanned": 0,
            "scan_pages": [],
            "text_chars": len(norm),
            "flags": flags,
            "email_meta": None,
            "attachments": [],
        }
    finally:
        wb.close()


def _read_text_with_fallback(path: Path, max_bytes: int = 400 * 1024) -> str:
    """Read file content trying utf-8-sig, cp1252, latin-1 encodings."""
    with open(path, "rb") as f:
        raw_bytes = f.read(max_bytes)

    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _parse_csv_tsv(path: Path) -> dict[str, Any]:
    """Parse CSV / TSV file, max 300 lines (Section 8.3)."""
    flags: dict[str, Any] = {}
    text = _read_text_with_fallback(path)
    all_lines = text.splitlines()

    if len(all_lines) > 300:
        kept = all_lines[:300]
        kept.append(f"... ({len(all_lines) - 300} more lines)")
        processed_text = "\n".join(kept)
    else:
        processed_text = text

    norm = normalize_text(processed_text, flags)
    chunks = chunk_text(norm, 4000)
    pages = [(i, c) for i, c in enumerate(chunks, start=1)]
    labels = {i: f"Section {i}" for i in range(1, len(chunks) + 1)}

    return {
        "ok": True,
        "error": None,
        "pages": pages,
        "labels": labels,
        "lines": {},
        "page_count": len(pages),
        "is_scanned": 0,
        "scan_pages": [],
        "text_chars": len(norm),
        "flags": flags,
        "email_meta": None,
        "attachments": [],
    }


def _parse_txt_md(path: Path, is_md: bool = False) -> dict[str, Any]:
    """Parse TXT or Markdown file, capped at 400 KB (Section 8.3)."""
    flags: dict[str, Any] = {}
    raw_text = _read_text_with_fallback(path, max_bytes=400 * 1024)
    norm = normalize_text(raw_text, flags)

    pages: list[tuple[int, str]] = []
    labels: dict[int, str] = {}
    lines_map: dict[int, tuple[int, int]] = {}

    if is_md:
        # Split at headings (^#{1,3} )
        heading_re = re.compile(r"^(#{1,3}\s+.+)$", re.MULTILINE)
        splits = list(heading_re.finditer(norm))

        if splits:
            sections: list[tuple[str, str, int, int]] = []  # (label, text, start_l, end_l)
            line_offsets = [0] + [m.start() for m in re.finditer(r"\n", norm)]

            def _get_line_no(char_idx: int) -> int:
                import bisect
                return bisect.bisect_right(line_offsets, char_idx)

            curr_label = "Introduction"
            curr_start = 0

            for m in splits:
                if m.start() > curr_start:
                    sec_text = norm[curr_start:m.start()].strip()
                    if sec_text:
                        s_l = _get_line_no(curr_start)
                        e_l = _get_line_no(m.start())
                        sections.append((curr_label, sec_text, s_l, e_l))
                curr_label = m.group(1).lstrip("#").strip()
                curr_start = m.start()

            # Final section
            if curr_start < len(norm):
                sec_text = norm[curr_start:].strip()
                if sec_text:
                    s_l = _get_line_no(curr_start)
                    e_l = _get_line_no(len(norm))
                    sections.append((curr_label, sec_text, s_l, e_l))

            # If any section is > 4000 chars, chunk it
            unit_idx = 1
            for lbl, stext, sl, el in sections:
                if len(stext) <= 4000:
                    pages.append((unit_idx, stext))
                    labels[unit_idx] = lbl
                    lines_map[unit_idx] = (sl, el)
                    unit_idx += 1
                else:
                    sub_chunks = chunk_text(stext, 4000)
                    for k, sc in enumerate(sub_chunks, start=1):
                        pages.append((unit_idx, sc))
                        labels[unit_idx] = f"{lbl} (part {k})" if len(sub_chunks) > 1 else lbl
                        lines_map[unit_idx] = (sl, el)
                        unit_idx += 1

    if not pages:
        # Default chunking
        chunks = chunk_text(norm, 4000)
        pages = [(i, c) for i, c in enumerate(chunks, start=1)]
        labels = {i: f"Section {i}" for i in range(1, len(chunks) + 1)}

    return {
        "ok": True,
        "error": None,
        "pages": pages,
        "labels": labels,
        "lines": lines_map,
        "page_count": len(pages),
        "is_scanned": 0,
        "scan_pages": [],
        "text_chars": len(norm),
        "flags": flags,
        "email_meta": None,
        "attachments": [],
    }


def _parse_html(path: Path) -> dict[str, Any]:
    """Parse HTML file: strip scripts, convert blocks, strip tags, unescape (Section 8.3)."""
    flags: dict[str, Any] = {}
    raw_text = _read_text_with_fallback(path, max_bytes=400 * 1024)

    # Strip script and style
    cleaned = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", "", raw_text, flags=re.IGNORECASE)

    # Convert br, p, div, tr, li to newlines
    cleaned = re.sub(r"<(br|p|div|tr|li)[^>]*>", "\n", cleaned, flags=re.IGNORECASE)

    # Strip remaining tags
    cleaned = re.sub(r"<[^>]+>", "", cleaned)

    # Unescape HTML entities
    unescaped = html.unescape(cleaned)

    norm = normalize_text(unescaped, flags)
    chunks = chunk_text(norm, 4000)
    pages = [(i, c) for i, c in enumerate(chunks, start=1)]
    labels = {i: f"Section {i}" for i in range(1, len(chunks) + 1)}

    return {
        "ok": True,
        "error": None,
        "pages": pages,
        "labels": labels,
        "lines": {},
        "page_count": len(pages),
        "is_scanned": 0,
        "scan_pages": [],
        "text_chars": len(norm),
        "flags": flags,
        "email_meta": None,
        "attachments": [],
    }


def _parse_rtf(path: Path) -> dict[str, Any]:
    """Parse RTF file: strip control words (Section 8.3)."""
    flags: dict[str, Any] = {}
    raw_text = _read_text_with_fallback(path, max_bytes=400 * 1024)

    # Remove RTF control words
    stripped = re.sub(r"\\[a-zA-Z]+(-?\d+)? ?", " ", raw_text)
    stripped = re.sub(r"[{}]", "", stripped)

    norm = normalize_text(stripped, flags)
    chunks = chunk_text(norm, 4000)
    pages = [(i, c) for i, c in enumerate(chunks, start=1)]
    labels = {i: f"Section {i}" for i in range(1, len(chunks) + 1)}

    return {
        "ok": True,
        "error": None,
        "pages": pages,
        "labels": labels,
        "lines": {},
        "page_count": len(pages),
        "is_scanned": 0,
        "scan_pages": [],
        "text_chars": len(norm),
        "flags": flags,
        "email_meta": None,
        "attachments": [],
    }


def _parse_json_yaml(path: Path) -> dict[str, Any]:
    """Parse JSON or YAML outside repos, pretty-printing first 300 lines (Section 8.3)."""
    flags: dict[str, Any] = {}
    raw_text = _read_text_with_fallback(path, max_bytes=400 * 1024)
    lines = raw_text.splitlines()

    if len(lines) > 300:
        lines = lines[:300] + [f"... ({len(lines) - 300} more lines)"]

    processed = "\n".join(lines)
    norm = normalize_text(processed, flags)
    chunks = chunk_text(norm, 4000)
    pages = [(i, c) for i, c in enumerate(chunks, start=1)]
    labels = {i: f"Section {i}" for i in range(1, len(chunks) + 1)}

    return {
        "ok": True,
        "error": None,
        "pages": pages,
        "labels": labels,
        "lines": {},
        "page_count": len(pages),
        "is_scanned": 0,
        "scan_pages": [],
        "text_chars": len(norm),
        "flags": flags,
        "email_meta": None,
        "attachments": [],
    }


def _parse_image(path: Path) -> dict[str, Any]:
    """Parse image with Pillow: units are empty scanned pages (Section 8.3)."""
    from PIL import Image

    try:
        with Image.open(str(path)) as img:
            n_frames = getattr(img, "n_frames", 1)
            pages = [(i, "") for i in range(1, n_frames + 1)]
            labels = {i: f"Page {i}" for i in range(1, n_frames + 1)}
            return {
                "ok": True,
                "error": None,
                "pages": pages,
                "labels": labels,
                "lines": {},
                "page_count": n_frames,
                "is_scanned": 1,
                "scan_pages": list(range(1, n_frames + 1)),
                "text_chars": 0,
                "flags": {},
                "email_meta": None,
                "attachments": [],
            }
    except Exception as exc:
        return {"ok": False, "error": f"Failed to open image: {exc}"}


# ---------------------------------------------------------------------------
# Public parse entry point
# ---------------------------------------------------------------------------

def parse_file(path: Path, kind: str) -> dict[str, Any]:
    """Parse a file and return the parse result dict contract (Section 8.2).

    Never raises an unhandled exception.
    """
    ext = path.suffix.lower()

    # Default fallback result
    def _fallback(ok: bool, error: str | None = None, unsupported: bool = False) -> dict[str, Any]:
        return {
            "ok": ok,
            "error": error,
            "unsupported": unsupported,
            "ext": ext,
            "pages": [],
            "labels": {},
            "lines": {},
            "page_count": 0,
            "is_scanned": 0,
            "scan_pages": [],
            "text_chars": 0,
            "flags": {},
            "email_meta": None,
            "attachments": [],
        }

    try:
        if kind == "other":
            return _fallback(ok=False, error="Unsupported file format", unsupported=True)

        if kind == "image":
            res = _parse_image(path)
        elif ext == ".pdf":
            res = _parse_pdf(path)
        elif ext == ".docx":
            res = _parse_docx(path)
        elif ext == ".doc":
            res = _parse_doc(path)
        elif ext == ".msg":
            res = _parse_msg(path)
        elif ext == ".eml":
            res = _parse_eml(path)
        elif ext in (".xlsx", ".xlsm"):
            res = _parse_xlsx(path)
        elif ext in (".csv", ".tsv"):
            res = _parse_csv_tsv(path)
        elif ext in (".md", ".markdown"):
            res = _parse_txt_md(path, is_md=True)
        elif ext in (".html", ".htm"):
            res = _parse_html(path)
        elif ext == ".rtf":
            res = _parse_rtf(path)
        elif ext in (".json", ".yaml", ".yml"):
            res = _parse_json_yaml(path)
        elif kind == "code":
            # Delegate to codeparse if available, else plain text with line numbers
            try:
                from filegraph.codeparse import parse_code
                res = parse_code(path)
            except Exception:
                res = _parse_txt_md(path, is_md=False)
        else:
            # Plain text / documents
            res = _parse_txt_md(path, is_md=False)

        # Merge ext and standard defaults
        res.setdefault("ok", True)
        res.setdefault("error", None)
        res.setdefault("unsupported", False)
        res["ext"] = ext
        res.setdefault("pages", [])
        res.setdefault("labels", {})
        res.setdefault("lines", {})
        res.setdefault("page_count", len(res.get("pages", [])))
        res.setdefault("is_scanned", 0)
        res.setdefault("scan_pages", [])
        res.setdefault("text_chars", sum(len(t) for _, t in res.get("pages", [])))
        res.setdefault("flags", {})
        res.setdefault("email_meta", None)
        res.setdefault("attachments", [])
        return res

    except Exception as exc:
        res = _fallback(ok=False, error=str(exc))
        return res
