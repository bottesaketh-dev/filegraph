"""Multi-root file walker with ignore rules, deny list, and repo detection (Section 7.2)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import pathspec

from filegraph.config import Settings
from filegraph.security import ALLOWED_FILENAMES, compile_deny_spec, is_denied

# System junk filenames to skip unconditionally (Section 7.2 item 2)
JUNK_FILENAMES = {"thumbs.db", ".ds_store", "desktop.ini"}

# Default ignore patterns if none provided
DEFAULT_IGNORE_PATTERNS = [
    ".git/",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "dist/",
    "build/",
    ".idea/",
    ".vscode/",
    "*.lock",
    "*.min.js",
    "*.map",
]


@dataclass
class WalkEntry:
    """Detailed file entry produced during walk."""
    path: Path
    rel_path: str
    repo_rel_path: str | None
    is_denied: bool = False


def _compile_pathspec(lines: list[str]) -> pathspec.PathSpec:
    try:
        return pathspec.PathSpec.from_lines("gitignore", lines)
    except Exception:
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def _read_gitignore(path: Path) -> pathspec.PathSpec | None:
    """Read a .gitignore file and return a PathSpec, or None on error."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return _compile_pathspec(lines)
    except Exception:
        return None


def find_enclosing_repo(file_path: Path, root_path: Path) -> Path | None:
    """Find the nearest enclosing git repo directory for a file, bounded by root_path."""
    try:
        curr = file_path.parent.resolve()
        bound = root_path.resolve()
        while True:
            git_marker = curr / ".git"
            if git_marker.exists():
                return curr
            if curr == bound or curr.parent == curr:
                break
            curr = curr.parent
    except Exception:
        pass
    return None


def walk_root(
    root_path: Path,
    settings: Settings | None = None,
    *,
    denied: list[Path] | None = None,
    repos: list[Path] | None = None,
) -> Iterator[Path]:
    """Walk a root folder yielding non-ignored, non-denied files (Section 7.2).

    - Prunes ignored directories (node_modules, .git, etc.) in place.
    - Applies settings.ignore_patterns + subtree .gitignore rules.
    - Applies built-in + config deny list. Denied files are appended to *denied*
      if provided, but never yielded.
    - Detects repos containing .git and records them in *repos* if provided.
    - Never follows symlinks.
    """
    root_p = Path(root_path).resolve()
    if not root_p.is_dir():
        return

    # Compile ignore patterns
    ignore_patterns = list(DEFAULT_IGNORE_PATTERNS)
    if settings and settings.ignore_patterns:
        ignore_patterns.extend(settings.ignore_patterns)
    root_ignore_spec = _compile_pathspec(ignore_patterns)

    # Compile deny patterns
    extra_deny = settings.deny_patterns if settings else None
    deny_spec = compile_deny_spec(extra_deny)

    # Subtree gitignore specs: {dir_path_str: PathSpec}
    gitignore_specs: dict[str, pathspec.PathSpec] = {}
    detected_repos: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(str(root_p), topdown=True, followlinks=False):
        curr_dir = Path(dirpath)
        try:
            rel_dir = os.path.relpath(dirpath, str(root_p)).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""
        except ValueError:
            rel_dir = ""

        # --- Repo detection (Section 7.2) ---
        # A directory containing .git (dir or file) is a repo.
        if ".git" in dirnames or ".git" in filenames or (curr_dir / ".git").exists():
            if str(curr_dir) not in detected_repos:
                detected_repos.add(str(curr_dir))
                if repos is not None:
                    repos.append(curr_dir)

        # --- Read .gitignore in this directory if present (Section 7.2 item 4) ---
        if ".gitignore" in filenames:
            gi_path = curr_dir / ".gitignore"
            spec = _read_gitignore(gi_path)
            if spec:
                gitignore_specs[str(curr_dir)] = spec

        # --- Prune dirnames in place ---
        pruned_dirs: list[str] = []
        for d in list(dirnames):
            d_path = curr_dir / d
            # Skip symlinked directories
            if d_path.is_symlink():
                dirnames.remove(d)
                continue

            # Skip junk directory names
            d_lower = d.lower()
            if d_lower in JUNK_FILENAMES or d.startswith("~$"):
                dirnames.remove(d)
                continue

            # Always prune .git folder so we never traverse into git internals
            if d == ".git":
                dirnames.remove(d)
                continue

            rel_d = f"{rel_dir}/{d}/" if rel_dir else f"{d}/"

            # Check root ignore spec
            if root_ignore_spec.match_file(rel_d) or root_ignore_spec.match_file(f"{d}/"):
                dirnames.remove(d)
                continue

            # Check ancestor .gitignore specs
            ignored_by_gi = False
            for gi_dir_str, gi_spec in gitignore_specs.items():
                if str(curr_dir).startswith(gi_dir_str):
                    sub_rel = os.path.relpath(str(d_path), gi_dir_str).replace("\\", "/") + "/"
                    if gi_spec.match_file(sub_rel) or gi_spec.match_file(f"{d}/"):
                        ignored_by_gi = True
                        break
            if ignored_by_gi:
                dirnames.remove(d)
                continue

        # --- Process filenames ---
        for fname in filenames:
            fname_lower = fname.lower()

            # Skip junk files
            if fname_lower in JUNK_FILENAMES or fname.startswith("~$"):
                continue

            # Skip .gitignore and .git markers
            if fname in (".gitignore", ".git"):
                continue

            file_path = curr_dir / fname

            # Skip symlinks
            if file_path.is_symlink():
                continue

            rel_file = f"{rel_dir}/{fname}" if rel_dir else fname

            # Check ignore rules first
            if root_ignore_spec.match_file(rel_file) or root_ignore_spec.match_file(fname):
                continue

            ignored_by_gi = False
            for gi_dir_str, gi_spec in gitignore_specs.items():
                if str(curr_dir).startswith(gi_dir_str):
                    sub_rel = os.path.relpath(str(file_path), gi_dir_str).replace("\\", "/")
                    if gi_spec.match_file(sub_rel) or gi_spec.match_file(fname):
                        ignored_by_gi = True
                        break
            if ignored_by_gi:
                continue

            # Check deny rules (Section 7.2 item 5, Section 7.4)
            if is_denied(rel_file, fname, spec=deny_spec):
                if denied is not None:
                    denied.append(file_path)
                continue

            # Skip other hidden files starting with . (except allowed ones like .env.example)
            if fname.startswith(".") and fname not in ALLOWED_FILENAMES:
                continue

            yield file_path
