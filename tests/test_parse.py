"""Tests for document and code parsers (Section 8, Section 12)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from filegraph.codeparse import parse_code
from filegraph.parse import (
    chunk_text,
    classify_kind,
    normalize_text,
    parse_file,
)
from tests.make_fixtures import create_fixture_tree


@pytest.fixture(scope="module")
def fixture_dir():
    """Create fixture tree once for parser tests."""
    return create_fixture_tree()


def test_chunk_text():
    """Verify chunk_text splits at newlines after half size."""
    short = "Hello world"
    assert chunk_text(short, 100) == [short]

    lines = ["Line " + str(i) for i in range(200)]
    long_text = "\n".join(lines)
    chunks = chunk_text(long_text, max_chars=500)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 600  # within window


def test_normalize_text():
    """Verify normalization: strips \\x00, normalizes newlines, collapses blanks, redacts."""
    raw = "Line 1\x00\r\n\r\n\r\n\r\n\r\nLine 2\r\nAPI_KEY = 'sk-proj-0123456789abcdefghijklmnopqrstuvwxyz'"
    flags = {}
    norm = normalize_text(raw, flags)
    assert "\x00" not in norm
    assert "\r" not in norm
    assert "\n\n\n\n" not in norm
    assert "[REDACTED]" in norm
    assert flags.get("redacted", 0) > 0


def test_classify_kind():
    """Verify extension-based kind classification."""
    assert classify_kind(".pdf") == "document"
    assert classify_kind(".docx") == "document"
    assert classify_kind(".eml") == "email"
    assert classify_kind(".msg") == "email"
    assert classify_kind(".xlsx") == "data"
    assert classify_kind(".csv") == "data"
    assert classify_kind(".py") == "code"
    assert classify_kind(".png") == "image"
    assert classify_kind(".unknown") == "other"

    # Inside repo: json/yaml/md are classified as code
    assert classify_kind(".json", inside_repo=False) == "data"
    assert classify_kind(".json", inside_repo=True) == "code"
    assert classify_kind(".md", inside_repo=True) == "code"


def test_parse_pdf(fixture_dir: Path):
    """Verify PDF parsing on generated passport PDF."""
    pdf_path = fixture_dir / "personal" / "Scan_0042.pdf"
    res = parse_file(pdf_path, "document")
    assert res["ok"] is True
    assert res["page_count"] >= 1
    assert len(res["pages"]) >= 1
    text = res["pages"][0][1]
    assert "PASSPORT" in text
    assert "Z1234567" in text
    assert res["labels"][1] == "Page 1"


def test_parse_docx(fixture_dir: Path):
    """Verify DOCX parsing on Acme MSA."""
    docx_path = fixture_dir / "legal" / "Acme MSA 2023.docx"
    res = parse_file(docx_path, "document")
    assert res["ok"] is True
    assert res["page_count"] >= 1
    full_text = " ".join(t for _, t in res["pages"])
    assert "Master Services Agreement" in full_text
    assert "Harborline Events LLC" in full_text
    assert "Acme Facility Services Inc." in full_text


def test_parse_eml_with_attachment(fixture_dir: Path):
    """Verify EML parsing extracts metadata and PDF attachment."""
    eml_path = fixture_dir / "legal" / "mail" / "signed.eml"
    res = parse_file(eml_path, "email")
    assert res["ok"] is True
    assert res["email_meta"] is not None
    assert res["email_meta"]["subject"] == "Signed Amendment No. 1"
    assert "contracts@acme.com" in res["email_meta"]["from"]
    assert len(res["attachments"]) >= 1
    att_name, att_bytes = res["attachments"][0]
    assert att_name == "contract.pdf"
    assert len(att_bytes) > 0


def test_parse_markdown_headings(fixture_dir: Path):
    """Verify Markdown parsing splits by heading when present."""
    md_path = fixture_dir / "personal" / "notes.md"
    res = parse_file(md_path, "document")
    assert res["ok"] is True
    assert len(res["pages"]) >= 1
    # Check that labels capture headings
    labels_list = list(res["labels"].values())
    assert any("Insurance" in l or "Travel" in l for l in labels_list)


def test_parse_csv(fixture_dir: Path):
    """Verify CSV parsing extracts data rows."""
    csv_path = fixture_dir / "personal" / "budget.csv"
    res = parse_file(csv_path, "data")
    assert res["ok"] is True
    text = res["pages"][0][1]
    assert "Rent" in text
    assert "Groceries" in text


def test_parse_python_code(fixture_dir: Path):
    """Verify code parsing extracts AST symbols and imports."""
    py_path = fixture_dir / "code" / "demo_repo" / "app" / "auth" / "session.py"
    res = parse_code(py_path)
    assert res["ok"] is True
    sym_names = {s["name"] for s in res["symbols"]}
    assert "Session" in sym_names
    assert "login_user" in sym_names
    assert "verify_token" in sym_names

    # Check units: Session class, login_user function, etc.
    labels = list(res["labels"].values())
    assert any("class Session" in l for l in labels)
    assert any("def login_user" in l for l in labels)


def test_parse_code_redaction(fixture_dir: Path):
    """Verify secrets inside code files are redacted."""
    cfg_path = fixture_dir / "code" / "demo_repo" / "config.py"
    res = parse_file(cfg_path, "code")
    assert res["ok"] is True
    full_text = " ".join(t for _, t in res["pages"])
    assert "sk-test-0123456789abcdefghij" not in full_text
    assert "[REDACTED]" in full_text
    assert res["flags"].get("redacted", 0) > 0


def test_parse_corrupt_file_never_raises():
    """Verify parse_file returns error dict instead of raising."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"NOT A VALID PDF FILE AT ALL")
        p = Path(f.name)

    try:
        res = parse_file(p, "document")
        assert res["ok"] is False
        assert res["error"] is not None
    finally:
        p.unlink(missing_ok=True)
