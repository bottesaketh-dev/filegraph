"""Repository metadata and detection (Section 12.1)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def get_repo_git_meta(repo_dir: Path) -> dict[str, str | None]:
    """Extract git metadata for a repo folder with 10s timeouts (Section 12.1).

    Leaves fields NULL if git is missing, fails, or dir is not a git repo.
    """
    meta: dict[str, str | None] = {
        "remote_url": None,
        "branch": None,
        "head_commit": None,
        "last_commit_at": None,
    }

    git_bin = shutil.which("git")
    if not git_bin or not (repo_dir / ".git").exists():
        return meta

    def _run(args: list[str]) -> str | None:
        try:
            res = subprocess.run(
                [git_bin] + args,
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0:
                val = res.stdout.strip()
                return val if val else None
        except Exception:
            pass
        return None

    meta["remote_url"] = _run(["config", "--get", "remote.origin.url"])
    meta["branch"] = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    meta["head_commit"] = _run(["rev-parse", "HEAD"])
    meta["last_commit_at"] = _run(["log", "-1", "--format=%cI"])

    return meta


def update_repos(
    conn: Any,
    root_id: int,
    root_path: Path,
    detected_repos: list[Path],
) -> dict[str, int]:
    """Upsert detected repos and compute language counts (Section 12.1).

    Returns a map of {rel_path: repo_id}.
    """
    repo_id_map: dict[str, int] = {}
    seen_rel_paths: set[str] = set()

    for rdir in detected_repos:
        try:
            rel = rdir.relative_to(root_path).as_posix()
        except ValueError:
            rel = "."
        if rel == "":
            rel = "."

        seen_rel_paths.add(rel)
        name = rdir.name if rel != "." else root_path.name
        git_meta = get_repo_git_meta(rdir)

        # Upsert repo row
        conn.execute(
            """
            INSERT INTO repos (root_id, rel_path, name, remote_url, branch, head_commit, last_commit_at, missing)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(root_id, rel_path) DO UPDATE SET
                name = excluded.name,
                remote_url = excluded.remote_url,
                branch = excluded.branch,
                head_commit = excluded.head_commit,
                last_commit_at = excluded.last_commit_at,
                missing = 0
            """,
            (
                root_id,
                rel,
                name,
                git_meta["remote_url"],
                git_meta["branch"],
                git_meta["head_commit"],
                git_meta["last_commit_at"],
            ),
        )

        row = conn.execute(
            "SELECT id FROM repos WHERE root_id = ? AND rel_path = ?",
            (root_id, rel),
        ).fetchone()
        if row:
            repo_id = row["id"]
            repo_id_map[rel] = repo_id

            # Update languages JSON map from files in this repo
            lang_rows = conn.execute(
                """
                SELECT language, COUNT(*) as count
                FROM files
                WHERE repo_id = ? AND kind = 'code' AND missing = 0 AND language IS NOT NULL
                GROUP BY language
                """,
                (repo_id,),
            ).fetchall()
            lang_dict = {r["language"]: r["count"] for r in lang_rows}
            conn.execute(
                "UPDATE repos SET languages = ? WHERE id = ?",
                (json.dumps(lang_dict), repo_id),
            )

    # Repos previously registered for this root that were not detected again get missing=1
    if seen_rel_paths:
        placeholders = ",".join("?" for _ in seen_rel_paths)
        conn.execute(
            f"UPDATE repos SET missing = 1 WHERE root_id = ? AND rel_path NOT IN ({placeholders})",
            [root_id] + list(seen_rel_paths),
        )
    else:
        conn.execute("UPDATE repos SET missing = 1 WHERE root_id = ?", (root_id,))

    return repo_id_map


TOOL_RECORD_REPO = {
    "name": "record_repo",
    "description": "Record repository summary and architectural metadata.",
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "2-4 sentences explaining what this repo does and its purpose."},
            "purpose": {"type": "string", "description": "High level purpose of the repository."},
            "languages": {"type": "array", "items": {"type": "string"}},
            "frameworks": {"type": "array", "items": {"type": "string"}},
            "entry_points": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["path", "description"],
                },
            },
            "how_to_run": {"type": "string", "description": "Commands or instructions to run this project."},
            "main_components": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["path", "description"],
                },
            },
            "external_services": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": [
            "summary",
            "purpose",
            "languages",
            "frameworks",
            "entry_points",
            "how_to_run",
            "main_components",
            "external_services",
            "confidence",
        ],
    },
}

REPO_SYSTEM_PROMPT = """\
You summarise a source code repository for its owner, who will later ask questions like "which project handles payments"
or "how do I run this". Use only the README, manifests, directory tree and symbol list provided. Name real paths.
If something is unclear (for example how to run it), say "not stated" rather than guessing. Call record_repo exactly once.
"""


def summarize_repo(
    conn: Any,
    repo_name: str,
    settings: Any,
    refresh: bool = False,
) -> None:
    """Generate and store structured LLM summary for a repository (Section 12.4)."""
    import hashlib
    from filegraph.llm import call_tool, model_for_policy
    from filegraph.security import redact

    row = conn.execute(
        """
        SELECT r.id, r.rel_path, r.name, r.summary, r.summary_input_sha,
               rt.path as root_path, rt.llm_policy
        FROM repos r
        JOIN roots rt ON rt.id = r.root_id
        WHERE r.name = ?
        """,
        (repo_name,),
    ).fetchone()

    if not row:
        return

    repo_id = row["id"]
    root_path = Path(row["root_path"])
    repo_rel = row["rel_path"]
    repo_dir = root_path / repo_rel if repo_rel != "." else root_path

    if not repo_dir.is_dir():
        return

    # 1. README
    readme_text = ""
    for rname in ("README.md", "README.rst", "README.txt", "readme.md", "README"):
        r_path = repo_dir / rname
        if r_path.is_file():
            try:
                readme_text = r_path.read_text(encoding="utf-8", errors="replace")[:8000]
                break
            except Exception:
                pass

    # 2. Manifests
    manifest_names = (
        "package.json", "pyproject.toml", "setup.py", "requirements.txt",
        "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "Gemfile",
        "composer.json", "Dockerfile", "docker-compose.yml", "Makefile"
    )
    manifest_texts: list[str] = []
    for mname in manifest_names:
        mpath = repo_dir / mname
        if mpath.is_file():
            try:
                content = mpath.read_text(encoding="utf-8", errors="replace")[:3000]
                manifest_texts.append(f"--- Manifest: {mname} ---\n{content}")
            except Exception:
                pass

    # 3. Directory tree to depth 3 (max 300 lines)
    tree_lines: list[str] = []
    try:
        def _walk_tree(cur_dir: Path, depth: int, prefix: str = ""):
            if depth > 3 or len(tree_lines) >= 300:
                return
            entries = sorted(cur_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name))
            for e in entries:
                if e.name.startswith(".") or e.name in ("node_modules", "__pycache__", "venv", ".git"):
                    continue
                tree_lines.append(f"{prefix}{e.name}{'/' if e.is_dir() else ''}")
                if len(tree_lines) >= 300:
                    break
                if e.is_dir():
                    _walk_tree(e, depth + 1, prefix + "  ")

        _walk_tree(repo_dir, 1)
    except Exception:
        pass

    # 4. Up to 200 symbol qualnames with file paths
    sym_rows = conn.execute(
        """
        SELECT cs.qualname, f.rel_path
        FROM code_symbols cs
        JOIN files f ON f.id = cs.file_id
        WHERE cs.repo_id = ?
        LIMIT 200
        """,
        (repo_id,),
    ).fetchall()
    symbols_text = "\n".join(f"{r['rel_path']}: {r['qualname']}" for r in sym_rows)

    # Assemble raw input and redact
    raw_input = (
        f"Repository Name: {repo_name}\n\n"
        f"=== README ===\n{readme_text}\n\n"
        f"=== Manifests ===\n" + "\n\n".join(manifest_texts) + "\n\n"
        f"=== Directory Tree ===\n" + "\n".join(tree_lines) + "\n\n"
        f"=== Symbols ===\n{symbols_text}"
    )
    redacted_input, _ = redact(raw_input)
    input_sha = hashlib.sha256(redacted_input.encode("utf-8")).hexdigest()

    if not refresh and row["summary_input_sha"] == input_sha and row["summary"]:
        return

    model_key = model_for_policy(row["llm_policy"] or "cloud", "extract") or "GPT_4_1"
    res_args, _ = call_tool(
        model_key,
        tool=TOOL_RECORD_REPO,
        content=redacted_input,
        system=REPO_SYSTEM_PROMPT,
    )

    summary_text = res_args.get("summary") or ""
    summary_json_str = json.dumps(res_args)

    conn.execute(
        """
        UPDATE repos
        SET summary = ?, summary_json = ?, summary_input_sha = ?
        WHERE id = ?
        """,
        (summary_text, summary_json_str, input_sha, repo_id),
    )
    conn.commit()
