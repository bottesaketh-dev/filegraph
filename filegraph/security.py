"""Security module: built-in deny list and secret redaction (Section 7.4)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

import pathspec

# Built-in deny list (always applied, in addition to config `deny`)
BUILTIN_DENY_PATTERNS = [
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
    "**/id_rsa*",
    "**/id_ed25519*",
    "**/id_ecdsa*",
    "**/.ssh/**",
    "**/.gnupg/**",
    "**/.aws/credentials",
    "**/.netrc",
    "**/.npmrc",
    "**/.pypirc",
    "**/*.kdbx",
    "**/*.keychain*",
    "**/Login Data",
    "**/Cookies",
    "**/*.sqlite-wal",
    "**/AppData/**/Browser*/**",
    "**/Library/Keychains/**",
    "**/Passwords/**",
    "**/passwords/**",
    "**/*recovery*code*",
    "**/*recovery*codes*",
    "**/*backup*code*",
    "**/*backup*codes*",
    "**/*secret*",
    "**/*secrets*",
]

# Exceptions to the deny list
ALLOWED_FILENAMES = {".env.example", ".env.sample"}

# Compiled pathspec for builtin deny list
def _compile_spec(patterns: Sequence[str]) -> pathspec.PathSpec:
    try:
        return pathspec.PathSpec.from_lines("gitignore", patterns)
    except Exception:
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


_BUILTIN_SPEC = _compile_spec(BUILTIN_DENY_PATTERNS)


def compile_deny_spec(extra_patterns: Sequence[str] | None = None) -> pathspec.PathSpec:
    """Compile builtin + extra deny patterns into a single PathSpec."""
    patterns = list(BUILTIN_DENY_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)
    return _compile_spec(patterns)


def is_denied(
    rel_path: str | Path,
    filename: str | None = None,
    extra_patterns: Sequence[str] | None = None,
    spec: pathspec.PathSpec | None = None,
) -> bool:
    """Check if a file or path matches the deny list.

    .env.example and .env.sample are always allowed.
    Checks both the relative path and the filename against deny patterns.
    """
    p_str = str(rel_path).replace("\\", "/")
    fname = filename if filename is not None else Path(p_str).name

    # Exceptions
    if fname in ALLOWED_FILENAMES:
        return False

    matcher = spec or (
        compile_deny_spec(extra_patterns) if extra_patterns else _BUILTIN_SPEC
    )

    # Check relative path and filename
    if matcher.match_file(p_str):
        return True
    if matcher.match_file(fname):
        return True
    # In case pathspec needs leading slash or dir/ prefix
    if not p_str.startswith("/"):
        if matcher.match_file("/" + p_str):
            return True

    return False


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_RE_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
_RE_AWS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_RE_GITHUB = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")
_RE_SLACK = re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")
_RE_OPENAI = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
_RE_GOOGLE = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")
_RE_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)
_RE_ASSIGNMENT = re.compile(
    r"""(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|client[_-]?secret)(\s*[:=]\s*)(['"]?)([^\s'"]{8,})\3"""
)
# Indian Government IDs & Sensitive Numbers
_RE_AADHAAR = re.compile(r"\b\d{4}[\s-]\d{4}[\s-]\d{4}\b")
_RE_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_RE_BANK_ACC = re.compile(r"(?i)\b(?:a/c|acc(?:ount)?)\s*(?:no|num|number)?\s*[:=.]?\s*(\d{9,18})\b")
_RE_RECOVERY_CODE = re.compile(r"\b[0-9a-f]{4,5}-[0-9a-f]{4,5}\b")


def redact(text: str) -> tuple[str, int]:
    """Replace sensitive strings with `[REDACTED]` (Section 7.4).

    Returns (redacted_text, count_of_redactions).
    Never stores or returns original secret values.
    """
    if not text:
        return text, 0

    n_redacted = 0

    def _replace_simple(match: re.Match[str]) -> str:
        nonlocal n_redacted
        n_redacted += 1
        return "[REDACTED]"

    # 1. Private keys
    text = _RE_PRIVATE_KEY.sub(_replace_simple, text)

    # 2. Token / key patterns and PII
    for pat in (_RE_AWS, _RE_GITHUB, _RE_SLACK, _RE_OPENAI, _RE_GOOGLE, _RE_JWT, _RE_AADHAAR, _RE_PAN, _RE_RECOVERY_CODE):
        text = pat.sub(_replace_simple, text)

    # 3. Bank account numbers
    def _replace_bank(match: re.Match[str]) -> str:
        nonlocal n_redacted
        n_redacted += 1
        full = match.group(0)
        acc_num = match.group(1)
        return full.replace(acc_num, "[REDACTED]")

    text = _RE_BANK_ACC.sub(_replace_bank, text)

    # 4. Key-value assignments: keep key name and assignment operator, redact value
    def _replace_assignment(match: re.Match[str]) -> str:
        nonlocal n_redacted
        key_name = match.group(1)
        sep = match.group(2)
        quote = match.group(3)
        val = match.group(4)
        if val == "[REDACTED]":
            return match.group(0)
        n_redacted += 1
        return f"{key_name}{sep}{quote}[REDACTED]{quote}"

    text = _RE_ASSIGNMENT.sub(_replace_assignment, text)

    return text, n_redacted
