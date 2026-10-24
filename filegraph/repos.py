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
