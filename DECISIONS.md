# Decisions

Choices made where the build specification was silent.

## P1 — Skeleton

1. **`llm_instances.py` lazy construction**: Model clients are wrapped in a factory function so that importing the module never makes network calls and never fails when credentials are missing. Entries whose environment variables are unset are simply omitted from `LLM_INSTANCES`.

2. **FTS5 check at DB init time**: `ensure_fts5()` is called once when the schema is created. If FTS5 is unavailable, the program exits immediately with a clear message rather than failing later during ingestion.

3. **Settings dataclass is frozen**: Uses `@dataclass(frozen=True)` so settings are immutable after loading. Any code needing different settings must call `load_settings()` again.

4. **`llm_instances.py` structure**: Uses a `build_instances()` function called at module level rather than top-level class constructors, so missing SDK packages (e.g. langchain-google-genai) don't crash on import.

## P2 — Ingest

1. **Deny list evaluation and exceptions**: The deny list is evaluated on normalized relative paths (with `/` separators) and filenames. Exceptions (`.env.example`, `.env.sample`) are explicitly allowed per Section 7.4. Denied files are counted in run stats but never opened or stored.

2. **Single-worker fallback**: When `workers <= 1`, `ingest` processes files in-process rather than spawning `ProcessPoolExecutor` subprocesses, eliminating process creation overhead and simplifying testing on Windows.

3. **Canonical path resolution**: Paths are resolved with `Path.resolve()` to normalize Windows 8.3 short paths (e.g. `SAKETH~1`) to their canonical long forms for reliable repo matching and root relative path computations.

4. **Code unit generation**: Python files parsed with stdlib `ast` create units for functions, classes, and methods with line numbers recorded, plus a `module` unit for remaining top-level code, splitting any unit exceeding 4,000 characters.

5. **Pathspec pattern syntax**: Uses pathspec's `"gitignore"` pattern factory with fallback to `"gitwildmatch"` for modern compatibility and clean output without deprecation warnings.

## P3 — Cards, Routing, and Extraction

1. **Flexible `call_tool` signature**: Supports both `call_tool(model_key, tool, content, system=...)` and `call_tool(model_key, system, content, tool)` transparently by checking argument types at runtime.

2. **Nullable YAML card fields**: Keys `main_entity`, `main_date`, and `match` in `CARD_YAML_SCHEMA` accept `null` for deterministic cards (e.g. `code.yaml` with `llm: false`) per Section 9.5.

3. **Schema repair strategy**: When LLM output contains invalid enum values, it falls back to `"other"` (or the first allowed enum value); unknown keys are stripped, and missing required properties receive type-safe defaults with review reasons recorded.

4. **Multi-location fake responses**: `_load_fake_responses` resolves `fake_responses.json` from the repo root, CWD, or module directory, ensuring offline test runs work reliably regardless of the current working directory.
