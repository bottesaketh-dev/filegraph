"""Settings from .env + filegraph.yaml (Section 4)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RootConfig:
    """One configured root directory."""
    path: Path
    name: str
    profile: str = "auto"
    llm: str = "cloud"  # cloud | local | metadata_only


@dataclass(frozen=True)
class LimitsConfig:
    max_file_mb: int = 50
    max_text_chars_per_file: int = 2_000_000
    confirm_tokens_over: int = 2_000_000


@dataclass(frozen=True)
class ExtractionConfig:
    head_pages: int = 8
    tail_pages: int = 4
    max_image_pages: int = 8
    max_text_chars: int = 60_000
    image_dpi: int = 110
    workers: int = 4


@dataclass(frozen=True)
class CodeConfig:
    per_file_llm: bool = False
    repo_summaries: bool = True


@dataclass(frozen=True)
class SearchConfig:
    rrf_k: int = 60
    bm25_candidates: int = 50
    vector_candidates: int = 50


@dataclass(frozen=True)
class MeConfig:
    names: list[str] = field(default_factory=list)
    organizations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Settings:
    """Frozen, typed settings for the whole application."""
    me: MeConfig = field(default_factory=MeConfig)
    roots: list[RootConfig] = field(default_factory=list)
    ignore_patterns: list[str] = field(default_factory=list)
    deny_patterns: list[str] = field(default_factory=list)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    code: CodeConfig = field(default_factory=CodeConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    db_path: Path = Path("./filegraph.db")
    config_path: Path = Path("./filegraph.yaml")
    cards_dir: Path = Path("./cards")

    @property
    def attachments_dir(self) -> Path:
        return self.db_path.parent / f"{self.db_path.stem}_attachments"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand(p: str) -> Path:
    """Expand ~ and environment variables, then resolve."""
    return Path(os.path.expandvars(os.path.expanduser(p))).resolve()


def _parse_root(raw: dict[str, Any]) -> RootConfig | None:
    """Parse one root entry.  Returns None (with a warning) when the path
    does not exist on disk."""
    p = _expand(str(raw["path"]))
    name = raw["name"]
    profile = raw.get("profile", "auto")
    llm = raw.get("llm", "cloud")
    if llm not in ("cloud", "local", "metadata_only"):
        print(f"Warning: root '{name}' has invalid llm policy '{llm}'; using 'cloud'.",
              file=sys.stderr)
        llm = "cloud"
    if not p.exists():
        print(f"Warning: root '{name}' path does not exist: {p}", file=sys.stderr)
        return None
    return RootConfig(path=p, name=name, profile=profile, llm=llm)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_settings(
    config_path: str | Path | None = None,
    db_path: str | Path | None = None,
    *,
    require_roots: bool = True,
) -> Settings:
    """Load .env then filegraph.yaml and return a frozen Settings object.

    *config_path* and *db_path* override environment / defaults.
    Set *require_roots* to False for commands that don't need roots (e.g. init).
    """
    # --- .env ---
    # Look for .env next to this module's parent (the repo root), or CWD.
    repo_root = Path(__file__).resolve().parent.parent
    for candidate in [repo_root / ".env", Path.cwd() / ".env"]:
        if candidate.is_file():
            load_dotenv(candidate)
            break

    # --- Resolve paths ---
    cfg_path = Path(config_path) if config_path else Path(
        os.environ.get("FG_CONFIG", "./filegraph.yaml")
    )
    cfg_path = cfg_path.resolve()

    d_path = Path(db_path) if db_path else Path(
        os.environ.get("FG_DB", "./filegraph.db")
    )
    d_path = d_path.resolve()

    cards = repo_root / "cards"

    # --- No YAML yet? ---
    if not cfg_path.is_file():
        if require_roots:
            print(
                "No filegraph.yaml found. Copy filegraph.yaml.example to "
                "filegraph.yaml and list your folders under roots:",
                file=sys.stderr,
            )
            sys.exit(2)
        return Settings(db_path=d_path, config_path=cfg_path, cards_dir=cards)

    # --- Parse YAML ---
    with open(cfg_path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # me
    me_raw = raw.get("me", {})
    me = MeConfig(
        names=me_raw.get("names", []),
        organizations=me_raw.get("organizations", []),
    )

    # roots
    roots: list[RootConfig] = []
    seen_names: set[str] = set()
    for r in raw.get("roots", []):
        if r["name"] in seen_names:
            print(f"Warning: duplicate root name '{r['name']}'; skipping.",
                  file=sys.stderr)
            continue
        seen_names.add(r["name"])
        parsed = _parse_root(r)
        if parsed is not None:
            roots.append(parsed)

    if require_roots and not roots:
        print("No valid roots found in filegraph.yaml.", file=sys.stderr)
        sys.exit(2)

    # ignore / deny
    ignore = raw.get("ignore", [])
    deny = raw.get("deny", [])

    # limits
    lim_raw = raw.get("limits", {})
    limits = LimitsConfig(
        max_file_mb=lim_raw.get("max_file_mb", 50),
        max_text_chars_per_file=lim_raw.get("max_text_chars_per_file", 2_000_000),
        confirm_tokens_over=lim_raw.get("confirm_tokens_over", 2_000_000),
    )

    # extraction
    ext_raw = raw.get("extraction", {})
    extraction = ExtractionConfig(
        head_pages=ext_raw.get("head_pages", 8),
        tail_pages=ext_raw.get("tail_pages", 4),
        max_image_pages=ext_raw.get("max_image_pages", 8),
        max_text_chars=ext_raw.get("max_text_chars", 60_000),
        image_dpi=ext_raw.get("image_dpi", 110),
        workers=ext_raw.get("workers", 4),
    )

    # code
    code_raw = raw.get("code", {})
    code_cfg = CodeConfig(
        per_file_llm=code_raw.get("per_file_llm", False),
        repo_summaries=code_raw.get("repo_summaries", True),
    )

    # search
    srch_raw = raw.get("search", {})
    search_cfg = SearchConfig(
        rrf_k=srch_raw.get("rrf_k", 60),
        bm25_candidates=srch_raw.get("bm25_candidates", 50),
        vector_candidates=srch_raw.get("vector_candidates", 50),
    )

    return Settings(
        me=me,
        roots=roots,
        ignore_patterns=ignore,
        deny_patterns=deny,
        limits=limits,
        extraction=extraction,
        code=code_cfg,
        search=search_cfg,
        db_path=d_path,
        config_path=cfg_path,
        cards_dir=cards,
    )
