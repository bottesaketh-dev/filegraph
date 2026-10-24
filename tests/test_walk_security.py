"""Tests for security module (deny list, redaction) and walker (Section 7.2, 7.4)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from filegraph.config import Settings
from filegraph.security import is_denied, redact
from filegraph.walk import find_enclosing_repo, walk_root


def test_deny_list_builtin():
    """Verify built-in deny list patterns."""
    assert is_denied(".env")
    assert is_denied("sub/.env")
    assert is_denied(".env.production")
    assert is_denied("id_rsa")
    assert is_denied("id_rsa.pub")
    assert is_denied("id_ed25519")
    assert is_denied("certs/server.pem")
    assert is_denied("certs/server.key")
    assert is_denied("vault.kdbx")
    assert is_denied(".ssh/id_rsa")
    assert is_denied("C:/Users/test/.ssh/config")

    # Exceptions
    assert not is_denied(".env.example")
    assert not is_denied(".env.sample")
    assert not is_denied("sub/.env.example")

    # Normal files should not be denied
    assert not is_denied("document.pdf")
    assert not is_denied("notes.txt")
    assert not is_denied("src/main.py")


def test_redact_secrets():
    """Verify secret redaction patterns (Section 7.4)."""
    # 1. Private key
    pk = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----"
    red, n = redact(f"Before {pk} After")
    assert "[REDACTED]" in red
    assert "MIIEowIBAAKCAQEA0" not in red
    assert n == 1

    # 2. Tokens & keys
    text = (
        "AWS: AKIAIOSFODNN7EXAMPLE\n"
        "GH: ghp_1234567890abcdefghijklmnopqrstuvwxyzAB\n"
        "Slack: xoxb-1234567890-123456789012-abcdefghijklmnop\n"
        "OpenAI: sk-proj-1234567890abcdefghijklmnopqrstuv\n"
        "Google: AIzaSyD12345678901234567890123456789012\n"
        "JWT: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozS6qYZ0_m9P2u8p_fake_jwt_token_here"
    )
    red, n = redact(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in red
    assert "ghp_1234567890" not in red
    assert "xoxb-1234567890" not in red
    assert "sk-proj-1234567890" not in red
    assert "AIzaSyD12345" not in red
    assert "eyJhbGciOiJIUzI1NiJ9" not in red
    assert red.count("[REDACTED]") >= 6
    assert n >= 6

    # 3. Key-value assignments
    assign_text = (
        'api_key = "mySecretTokenVal123"\n'
        "token: 'superSecretValue456'\n"
        'password=VerySecretPassword789\n'
    )
    red_assign, n_assign = redact(assign_text)
    assert 'api_key = "[REDACTED]"' in red_assign or 'api_key = "[REDACTED]' in red_assign
    assert "mySecretTokenVal123" not in red_assign
    assert "superSecretValue456" not in red_assign
    assert "VerySecretPassword789" not in red_assign
    assert n_assign == 3

    # 4. Indian IDs, bank accounts, and recovery codes
    pii_text = (
        "Aadhaar: 1234 5678 9012\n"
        "PAN: ABCDE1234F\n"
        "Bank Acc: A/C No: 1234567890123\n"
        "Recovery: fb780-bb7a3\n"
    )
    red_pii, n_pii = redact(pii_text)
    assert "1234 5678 9012" not in red_pii
    assert "ABCDE1234F" not in red_pii
    assert "1234567890123" not in red_pii
    assert "fb780-bb7a3" not in red_pii
    assert n_pii >= 4


def test_deny_list_passwords_and_recovery():
    """Verify denial of password folders and recovery files."""
    assert is_denied("Passwords/github-recovery-codes.txt")
    assert is_denied("passwords/keys.txt")
    assert is_denied("backup_codes.txt")
    assert is_denied("recovery-codes.txt")
    assert is_denied("my_secret_token.txt")


def test_walk_root_ignore_and_deny():
    """Verify walking skips ignored, junk, and denied files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir).resolve()

        # Create folder structure
        (root / "node_modules" / "pkg").mkdir(parents=True)
        (root / "node_modules" / "pkg" / "index.js").write_text("console.log(1);")

        (root / ".git" / "objects").mkdir(parents=True)
        (root / ".git" / "config").write_text("[core]")

        (root / ".venv" / "lib").mkdir(parents=True)
        (root / ".venv" / "lib" / "site.py").write_text("# venv")

        (root / "docs").mkdir(parents=True)
        (root / "docs" / "readme.md").write_text("# Hello")
        (root / "docs" / "thumbs.db").write_text("junk")
        (root / "docs" / "desktop.ini").write_text("junk")
        (root / "docs" / "~$lock.docx").write_text("lock")

        # Denied files
        (root / ".env").write_text("SECRET=1")
        (root / "id_rsa").write_text("KEY")

        # Subtree gitignore
        sub = root / "sub"
        sub.mkdir()
        (sub / ".gitignore").write_text("*.tmp\nbuild/\n")
        (sub / "file.tmp").write_text("temporary")
        (sub / "file.txt").write_text("keep me")
        (sub / "build").mkdir()
        (sub / "build" / "out.txt").write_text("build output")

        denied_files: list[Path] = []
        repos: list[Path] = []
        yielded = list(walk_root(root, denied=denied_files, repos=repos))

        yielded_names = {p.name for p in yielded}

        # Expected files yielded
        assert "readme.md" in yielded_names
        assert "file.txt" in yielded_names

        # Ignored files should NOT be yielded
        assert "index.js" not in yielded_names
        assert "site.py" not in yielded_names
        assert "config" not in yielded_names
        assert "file.tmp" not in yielded_names
        assert "out.txt" not in yielded_names

        # Junk files should NOT be yielded
        assert "thumbs.db" not in yielded_names
        assert "desktop.ini" not in yielded_names
        assert "~$lock.docx" not in yielded_names

        # Denied files should NOT be yielded, but counted in denied
        assert ".env" not in yielded_names
        assert "id_rsa" not in yielded_names
        denied_names = {p.name for p in denied_files}
        assert ".env" in denied_names
        assert "id_rsa" in denied_names

        # Repo detection
        assert len(repos) >= 1
        assert root in repos
