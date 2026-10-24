"""Code parsers: symbols, imports, and units (Section 12)."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from filegraph.parse import chunk_text, normalize_text

# Map file extension to language name (Section 12.2)
EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "c_cpp",
    ".cc": "c_cpp",
    ".cxx": "c_cpp",
    ".hpp": "c_cpp",
    ".h": "c_cpp",
    ".c": "c_cpp",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".r": "r",
    ".m": "matlab_objc",
    ".dart": "dart",
    ".lua": "lua",
    ".pl": "perl",
    ".ipynb": "notebook",
    ".vue": "vue",
    ".svelte": "svelte",
}


def detect_language(path: Path) -> str:
    """Detect programming language from file extension."""
    ext = path.suffix.lower()
    return EXTENSION_LANGUAGE_MAP.get(ext, "text")


def _read_file_text(path: Path) -> str:
    """Read text from file with common encodings."""
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_python(
    source: str, path: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[int, str, str, int, int]]]:
    """Parse Python source using stdlib ast.

    Returns (symbols, imports, units):
      - symbols: [{name, qualname, kind, start_line, end_line, signature, docstring}]
      - imports: [{module, name, qualname}]
      - units: [(unit_idx, label, text, start_line, end_line)]
    """
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # Fallback to plain chunking if file has syntax errors
        return [], [], []

    source_lines = source.splitlines()

    def _get_signature(node: ast.AST) -> str:
        s_line = getattr(node, "lineno", 1) - 1
        if 0 <= s_line < len(source_lines):
            return source_lines[s_line].strip()
        return ""

    def _get_docstring(node: ast.AST) -> str | None:
        doc = ast.get_docstring(node)
        return doc[:500] if doc else None

    # Walk AST
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({
                "name": node.name,
                "qualname": node.name,
                "kind": "function",
                "start_line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "signature": _get_signature(node),
                "docstring": _get_docstring(node),
            })
        elif isinstance(node, ast.ClassDef):
            symbols.append({
                "name": node.name,
                "qualname": node.name,
                "kind": "class",
                "start_line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "signature": _get_signature(node),
                "docstring": _get_docstring(node),
            })
            # Methods
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append({
                        "name": item.name,
                        "qualname": f"{node.name}.{item.name}",
                        "kind": "method",
                        "start_line": item.lineno,
                        "end_line": getattr(item, "end_lineno", item.lineno),
                        "signature": _get_signature(item),
                        "docstring": _get_docstring(item),
                    })
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"module": alias.name, "name": alias.asname or alias.name})
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level > 0:
                mod = "." * node.level + mod
            for alias in node.names:
                imports.append({
                    "module": mod,
                    "name": alias.name,
                })

    # Build units: one unit per symbol plus module unit for other lines
    units: list[tuple[int, str, str, int, int]] = []
    symbol_line_set: set[int] = set()

    unit_idx = 1
    for sym in symbols:
        sl = sym["start_line"]
        el = sym["end_line"]
        for ln in range(sl, el + 1):
            symbol_line_set.add(ln)
        sym_lines = source_lines[sl - 1 : el]
        sym_text = "\n".join(sym_lines).strip()
        if sym_text:
            kind_prefix = "def " if sym["kind"] in ("function", "method") else "class "
            label = f"{kind_prefix}{sym['qualname']}"
            if len(sym_text) <= 4000:
                units.append((unit_idx, label, sym_text, sl, el))
                unit_idx += 1
            else:
                chunks = chunk_text(sym_text, 4000)
                for k, c in enumerate(chunks, start=1):
                    lbl = f"{label} (part {k})" if len(chunks) > 1 else label
                    units.append((unit_idx, lbl, c, sl, el))
                    unit_idx += 1

    # Module unit for non-symbol lines (imports, constants, top-level script code)
    module_lines: list[str] = []
    for i, line in enumerate(source_lines, start=1):
        if i not in symbol_line_set:
            module_lines.append(line)

    mod_text = "\n".join(module_lines).strip()
    if mod_text:
        if len(mod_text) <= 4000:
            units.append((unit_idx, "module", mod_text, 1, len(source_lines)))
            unit_idx += 1
        else:
            chunks = chunk_text(mod_text, 4000)
            for k, c in enumerate(chunks, start=1):
                lbl = f"module (part {k})" if len(chunks) > 1 else "module"
                units.append((unit_idx, lbl, c, 1, len(source_lines)))
                unit_idx += 1

    return symbols, imports, units


def _parse_regex_code(
    source: str, language: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[int, str, str, int, int]]]:
    """Parse JS/TS/Go/etc. using regex heuristics (Section 12.3)."""
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    source_lines = source.splitlines()

    # JS/TS regex patterns
    fn_re = re.compile(r"^\s*(export\s+)?(default\s+)?(async\s+)?function\s+(\w+)")
    class_re = re.compile(r"^\s*(export\s+)?class\s+(\w+)")
    arrow_re = re.compile(r"^\s*(export\s+)?const\s+(\w+)\s*=\s*(async\s*)?\(")
    imp_re = re.compile(r"""import\s+.*?from\s+['"](.+)['"]""")
    req_re = re.compile(r"""require\(['"](.+)['"]\)""")

    for i, line in enumerate(source_lines, start=1):
        m_fn = fn_re.match(line)
        if m_fn:
            name = m_fn.group(4)
            symbols.append({
                "name": name,
                "qualname": name,
                "kind": "function",
                "start_line": i,
                "end_line": i,
                "signature": line.strip(),
                "docstring": None,
            })
            continue

        m_cls = class_re.match(line)
        if m_cls:
            name = m_cls.group(2)
            symbols.append({
                "name": name,
                "qualname": name,
                "kind": "class",
                "start_line": i,
                "end_line": i,
                "signature": line.strip(),
                "docstring": None,
            })
            continue

        m_arr = arrow_re.match(line)
        if m_arr:
            name = m_arr.group(2)
            symbols.append({
                "name": name,
                "qualname": name,
                "kind": "function",
                "start_line": i,
                "end_line": i,
                "signature": line.strip(),
                "docstring": None,
            })
            continue

        m_imp = imp_re.search(line)
        if m_imp:
            imports.append({"module": m_imp.group(1), "name": ""})
            continue

        m_req = req_re.search(line)
        if m_req:
            imports.append({"module": m_req.group(1), "name": ""})
            continue

    # Fix end_line for regex symbols: line before next symbol start (or EOF)
    for idx, sym in enumerate(symbols):
        if idx + 1 < len(symbols):
            sym["end_line"] = symbols[idx + 1]["start_line"] - 1
        else:
            sym["end_line"] = len(source_lines)

    # Chunk into units
    chunks = chunk_text(source, 4000)
    units = [
        (i, f"Section {i}", c, 1, len(source_lines))
        for i, c in enumerate(chunks, start=1)
    ]
    return symbols, imports, units


def parse_code(path: Path, language: str | None = None) -> dict[str, Any]:
    """Parse code file: symbols, imports, units, and redaction (Section 12.3)."""
    lang = language or detect_language(path)
    raw_source = _read_file_text(path)

    flags: dict[str, Any] = {}
    norm_source = normalize_text(raw_source, flags)

    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    units: list[tuple[int, str, str, int, int]] = []

    if lang == "python":
        symbols, imports, units = _parse_python(norm_source, path)
    elif lang in ("javascript", "typescript"):
        symbols, imports, units = _parse_regex_code(norm_source, lang)
    else:
        # Generic chunking
        chunks = chunk_text(norm_source, 4000)
        source_lines = norm_source.splitlines()
        units = [
            (i, f"Section {i}", c, 1, len(source_lines))
            for i, c in enumerate(chunks, start=1)
        ]

    # If no units were created (e.g. empty file or syntax error fallback)
    if not units and norm_source.strip():
        chunks = chunk_text(norm_source, 4000)
        source_lines = norm_source.splitlines()
        units = [
            (i, f"Section {i}", c, 1, len(source_lines))
            for i, c in enumerate(chunks, start=1)
        ]

    pages = [(u[0], u[2]) for u in units]
    labels = {u[0]: u[1] for u in units}
    lines_map = {u[0]: (u[3], u[4]) for u in units}

    return {
        "ok": True,
        "error": None,
        "unsupported": False,
        "ext": path.suffix.lower(),
        "language": lang,
        "symbols": symbols,
        "imports": imports,
        "pages": pages,
        "labels": labels,
        "lines": lines_map,
        "page_count": len(pages),
        "is_scanned": 0,
        "scan_pages": [],
        "text_chars": len(norm_source),
        "flags": flags,
        "email_meta": None,
        "attachments": [],
    }
