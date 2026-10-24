# FileGraph — Build Specification

**A local knowledge index and question-answering agent for an entire personal file system: documents, emails, spreadsheets, scans and code repositories.**

This document is a complete, self-contained specification. It is written for an AI code editor (Claude Code, Cursor, Copilot, Windsurf, etc.) or a human developer. Following it phase by phase must produce working code that behaves like the original *LegalGraph* project, generalized from a legal archive to any local files.

## Contents

Sections: 0 How to use this document · 1. Concepts · 2. Tech stack · 3. Project layout · 4. Configuration · 5. LLM layer ·
6. Database · 7. Ingestion · 8. Document parsers · 9. Card registry · 10. Routing · 11. Extraction · 12. Code profile ·
13. Embeddings and hybrid search · 14. Linking · 15. Agent · 16. Discovering new card types · 17. Filing · 18. CLI ·
19. Exports · 20. MCP server · 21. README · 22. Build phases and acceptance checks · 23. Error handling ·
24. Porting map from LegalGraph · 25. Testing · 26. Known limits · Appendix A Prompts · Appendix B Card files ·
Appendix C Definition of done

---

## 0. How to use this document (read first)

### 0.1 Role

You are a senior Python engineer building a production-quality local tool. You write clear, well-structured, tested code. You follow this specification exactly. Where it is silent, you choose the simplest robust option and record the choice in `DECISIONS.md`.

### 0.2 Objective

Build **FileGraph**: a command-line tool (plus an optional MCP server) that

1. scans several folders on the user's computer,
2. reads every supported file once and stores its text,
3. fills in a structured **card** for each document using an LLM (or deterministic analysis for code),
4. links documents to each other and to people, organizations and repositories (a knowledge graph),
5. answers plain-English questions through a tool-using agent that queries the index with SQL, hybrid search and graph traversal,
6. exports an Obsidian vault and an interactive `graph.html`,
7. suggests folders for new files, and
8. lets the user add new card types without changing the database schema.

### 0.3 How to work

- Build in the **phase order of Section 22**. Do not start a phase until the previous phase's acceptance checks pass.
- After each phase, run `pytest -q` with `FG_FAKE_LLM=1` (no network) and the phase's acceptance commands.
- Keep the module layout of Section 3. Keep function names stable once created; later phases import them.
- When the original LegalGraph source is available, port behaviour from it using the mapping table in Section 24. This spec wins wherever the two differ.

### 0.4 Suggested kickoff prompt for your editor

```text
Read FILEGRAPH_BUILD_SPEC.md completely before writing code. Then implement phase P1 from Section 22 exactly as specified,
write its tests, run them with FG_FAKE_LLM=1, and stop. Report what you built, the test results, and anything you added to
DECISIONS.md. Do not start the next phase until I say "continue".
```

Repeat with "continue" for each phase. Working phase by phase keeps each change reviewable and catches mistakes early.

### 0.5 Strict rules (never violate)

1. **No secrets in code.** API keys and tokens come only from environment variables / `.env`. Never print, log or store them. `.env` is in `.gitignore`.
2. **Never modify user files.** The only code path that writes inside a user root is `fg file --apply` after explicit confirmation (or `--yes`). Everything else writes only to the database directory.
3. **The agent's database connection is read-only** (`mode=ro`). Its SQL tool accepts exactly one `SELECT`/`WITH` statement.
4. **All LLM calls go through `filegraph/llm.py`.** No other module imports a provider SDK.
5. **One bad file never stops a run.** Every per-file parse, extraction or embedding failure is caught, recorded in the database, and the run continues.
6. **Respect privacy policies.** A root with `llm: local` never sends content to a cloud model; `metadata_only` never sends content to any model. Files on the deny list are never opened.
7. **Schema is fixed.** Adding a card type must never require a database migration (Section 6, Section 9).
8. **Cross-platform.** Windows, macOS and Linux. Store relative paths with `/` separators. Use `pathlib` everywhere.
9. **Tests run offline.** With `FG_FAKE_LLM=1` and no embedding model installed, the full test suite passes without network access.
10. **Python 3.10+**, standard library `sqlite3` (FTS5 must be available; check at startup and fail with a clear message if not).

---

## 1. Concepts and glossary

| Term | Meaning |
|---|---|
| **Root** | A top-level folder the user registers (e.g. `~/Documents`, `~/code`). Each root has a profile and an LLM policy. |
| **File** | Any file found under a root, or an attachment extracted from an email. One row in `files`. |
| **Unit** | A citable piece of a file: a PDF page, a ~4,000-character section of a text document, or a code symbol (function/class). One row in `pages`. |
| **Card** | The structured record extracted from a document: title, summary, category, entities, labelled dates, facts and references. One row in `documents` plus rows in `doc_dates`, `doc_facts`, `doc_entities`. |
| **Card type** | A template that defines what a card contains for a kind of document (`generic`, `identity`, `finance`, `legal`, `code`, ...). Defined in YAML files in `cards/`. |
| **Entity** | A person, organization, place or product mentioned in documents, merged across spellings. One row in `entities`. |
| **Edge** | A link between two files (`amends`, `attachment_of`, `imports`, ...). One row in `edges`. |
| **Self entity** | The user and their own organizations (configured), never treated as "the other party". |
| **Tier** | How much processing a file gets: 0 metadata only, 1 text + search, 2 LLM card. |

---

## 2. Tech stack

### 2.1 `requirements.txt`

```
# core
langchain-core>=0.3
langchain-openai>=0.2
langchain-anthropic>=0.3
pymupdf>=1.24,<2        # pymupdf 2.0 segfaulted on import on Windows/Python 3.12 in LegalGraph; keep <2 unless verified
python-docx>=1.1
extract-msg>=0.48
openpyxl>=3.1
pillow>=10.0
tqdm>=4.66
python-dotenv>=1.0
pyyaml>=6.0
pathspec>=0.12
jsonschema>=4.21
numpy>=1.26
pytest>=8.0
```

### 2.2 `requirements-optional.txt`

```
langchain-google-genai>=2.0       # Gemini via native client (OpenAI-compatible endpoint also works)
sentence-transformers>=3.0        # local embeddings (recommended)
sqlite-vec>=0.1                   # fast vector search inside SQLite
tree-sitter-language-pack>=0.2    # better symbol extraction for non-Python code
mcp>=1.2                          # MCP server for Claude Desktop / Claude Code
watchdog>=4.0                     # `fg watch`
```

Every optional dependency must be imported lazily inside the function that uses it, with a graceful fallback (Section 23).

### 2.3 External tools (optional, detected at runtime)

- `git` (repo metadata), `rg` / ripgrep (fast `grep` tool), LibreOffice `soffice` (legacy `.doc`).

---

## 3. Project layout

```
filegraph/
├── fg.py                      # CLI entry point (Section 18)
├── llm_instances.py           # model registry, secrets from env (Section 5.2)
├── filegraph.yaml.example     # user configuration template (Section 4.2)
├── .env.example
├── .gitignore                 # .env, *.db, *.db-*, *_attachments/, vault/, graph.html, __pycache__/
├── requirements.txt
├── requirements-optional.txt
├── README.md                  # user guide (Section 21)
├── DECISIONS.md               # choices made where this spec was silent
├── cards/                     # card registry (Section 9)
│   ├── generic.yaml
│   ├── identity.yaml
│   ├── finance.yaml
│   ├── legal.yaml
│   ├── code.yaml
│   └── proposed/              # drafts written by `fg propose-card`
├── filegraph/
│   ├── __init__.py
│   ├── config.py              # settings from .env + filegraph.yaml (Section 4)
│   ├── db.py                  # schema, connect, helpers (Section 6)
│   ├── llm.py                 # LLM access layer + fake LLM (Section 5)
│   ├── security.py            # deny list, secret redaction (Section 7.4)
│   ├── walk.py                # multi-root walker with ignore rules (Section 7)
│   ├── parse.py               # document parsers (Section 8)
│   ├── codeparse.py           # code parsers: symbols, imports (Section 12)
│   ├── ingest.py              # tier 0/1 ingestion, inventory (Section 7)
│   ├── cards.py               # card registry, schema compiler (Section 9)
│   ├── router.py              # decides card type (Section 10)
│   ├── inputs.py              # page selection + content blocks (Section 11.2)
│   ├── extract.py             # tier 2 extraction + materialize (Section 11)
│   ├── repos.py               # repo detection + repo summaries (Section 12)
│   ├── embed.py               # embeddings (Section 13)
│   ├── search.py              # hybrid search (Section 13)
│   ├── link.py                # entities + edges (Section 14)
│   ├── agent.py               # question answering agent (Section 15)
│   ├── discover.py            # new card type discovery (Section 16)
│   ├── filing.py              # folder suggestions + backtest (Section 17)
│   ├── export.py              # vault, graph, review csv, report (Section 19)
│   ├── graph_template.py      # HTML template for graph.html (Section 19.2)
│   ├── mcp_server.py          # MCP server (Section 20)
│   └── watch.py               # optional file watcher (Section 18)
└── tests/
    ├── conftest.py
    ├── make_fixtures.py       # builds a sample file tree (Section 25)
    ├── fake_responses.json    # canned LLM outputs for FG_FAKE_LLM=1
    └── test_*.py
```

---

## 4. Configuration

Two sources: `.env` for secrets and model choices, `filegraph.yaml` for roots and policies. Both live next to `fg.py` by default; `--config` and `--db` override paths.

### 4.1 `.env.example`

```env
# --- Credentials (examples; use whichever provider you have) ---
LLMFOUNDRY_TOKEN=
LLM_PROJECT_NAME=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# --- Model keys: names must exist in llm_instances.LLM_INSTANCES ---
FG_CLASSIFY_MODEL=GPT_4_1_MINI
FG_EXTRACT_MODEL=GPT_4_1
FG_ESCALATE_MODEL=GPT_5
FG_AGENT_MODEL=GPT_4_1
FG_FILING_MODEL=GPT_5
FG_LOCAL_MODEL=OLLAMA_LOCAL          # used for roots with llm: local

# --- Embeddings ---
FG_EMBED_PROVIDER=local              # local | none | <model key in LLM_INSTANCES with an embeddings client>
FG_EMBED_MODEL=BAAI/bge-small-en-v1.5

# --- Paths ---
FG_DB=./filegraph.db
FG_CONFIG=./filegraph.yaml

# --- Testing ---
FG_FAKE_LLM=0
```

### 4.2 `filegraph.yaml.example`

```yaml
me:
  names: ["Your Full Name", "Y. Name"]           # the user; treated as a self entity
  organizations: ["Your Company LLC"]            # user's own companies; self entities

roots:
  - path: ~/Documents
    name: documents
    profile: auto          # auto | <card name> (force one card type for the whole root)
    llm: cloud             # cloud | local | metadata_only
  - path: ~/Documents/Medical
    name: medical
    profile: auto
    llm: local
  - path: ~/code
    name: code
    profile: auto
    llm: cloud

ignore:                    # gitignore-style patterns, applied to every root (plus each repo's .gitignore)
  - "node_modules/"
  - ".venv/"
  - "venv/"
  - "__pycache__/"
  - "dist/"
  - "build/"
  - ".git/"
  - ".idea/"
  - ".vscode/"
  - "*.lock"
  - "*.min.js"
  - "*.map"

deny:                      # never opened, never stored (Section 7.4); merged with built-in deny list
  - "**/.ssh/**"
  - "**/*.kdbx"

limits:
  max_file_mb: 50          # larger files: tier 0 only
  max_text_chars_per_file: 2000000
  confirm_tokens_over: 2000000   # extract asks for confirmation above this estimate

extraction:
  head_pages: 8
  tail_pages: 4
  max_image_pages: 8
  max_text_chars: 60000
  image_dpi: 110
  workers: 4

code:
  per_file_llm: false      # files inside repos get deterministic code cards only
  repo_summaries: true

search:
  rrf_k: 60
  bm25_candidates: 50
  vector_candidates: 50
```

### 4.3 `config.py`

- Load `.env` with `python-dotenv`, then YAML. Expand `~` and environment variables in paths. Resolve roots to absolute paths.
- Expose a frozen dataclass `Settings` with typed fields for everything above, plus `db_path`, `attachments_dir = db_path.parent / f"{db_path.stem}_attachments"`, `cards_dir`.
- `load_settings(config_path=None, db_path=None) -> Settings`. Validate: root paths exist (warn and skip missing ones), `llm` values valid, model keys present in `LLM_INSTANCES` only when a command needs them.
- If `filegraph.yaml` does not exist, commands that need roots print: `No filegraph.yaml found. Copy filegraph.yaml.example to filegraph.yaml and list your folders under roots:` and exit with code 2.

---

## 5. LLM layer

### 5.1 Public API of `filegraph/llm.py`

```python
def get_chat(model_key: str):                     # LangChain chat model from LLM_INSTANCES (memoised)
def call_tool(model_key, system: str, content, tool: dict, max_tokens=4096) -> tuple[dict, dict]:
    """Force one tool call. Returns (args, usage={'input': int, 'output': int})."""
def chat_with_tools(model_key, tools: list[dict]):  # returns a chat model bound to tools, for the agent loop
def supports_vision(model_key) -> bool             # from LLM_INSTANCES[key].get("VISION", True)
def model_for_policy(policy: str, purpose: str) -> str | None
    # purpose in {"classify","extract","escalate","agent","filing"}; policy local -> FG_LOCAL_MODEL; metadata_only -> None
def check_credentials(model_key) -> None           # fail early with a clear message
```

`tool` dicts use the Anthropic shape `{"name", "description", "input_schema"}`. Content is a string or a list of blocks `{"type":"text","text":...}` / `{"type":"image","source":{"type":"base64","media_type":...,"data":...}}`. Port `_convert_content` and `_to_openai_tool` from LegalGraph to convert these for LangChain/OpenAI-style models.

**Forced tool calling with fallback (required).** Some providers reject `tool_choice=<name>`. Implement:

1. `bind_tools([tool], tool_choice=tool_name)` and invoke.
2. If that raises a "tool_choice not supported" style error, or the response has no tool call: retry once with `tool_choice="any"` (or `"required"` for OpenAI-style).
3. If still no tool call: look for a JSON object in the text response (strip code fences), parse it, and use it as args.
4. Otherwise raise `ValueError("model did not call <tool>")`.

Record token usage from `usage_metadata` when available, else zeros.

**Errors.** Treat authentication/permission/not-found errors (`AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, HTTP 401/403/404) as **fatal**: callers stop the run and print a hint to check credentials and model names. Retry rate-limit and 5xx errors with exponential backoff (3 attempts, 2s/8s/30s).

### 5.2 `llm_instances.py`

A registry dict, built at import time, **reading every secret from environment variables**:

```python
LLM_INSTANCES = {
  "GPT_4_1":      {"MODEL": ChatOpenAI(model="gpt-4.1", temperature=0, api_key=env("OPENAI_KEY_OR_FOUNDRY"), base_url=env("OPENAI_BASE_URL", None)), "VISION": True},
  "GPT_4_1_MINI": {...},
  "GPT_5":        {...},
  "CLAUDE_SONNET":{"MODEL": ChatAnthropic(model=..., api_key=env("ANTHROPIC_API_KEY"), base_url=env("ANTHROPIC_BASE_URL", None)), "VISION": True},
  "OLLAMA_LOCAL": {"MODEL": ChatOpenAI(model=env("OLLAMA_MODEL","qwen2.5:14b"), api_key="ollama", base_url="http://localhost:11434/v1", temperature=0), "VISION": False},
}
```

- If the user already has an `llm_instances.py` (e.g. LLM Foundry), keep its keys and structure but make sure no key literal appears in the file.
- Constructing a client must not make a network call. Entries whose credentials are missing are skipped (not an error) so tests can import the module.

### 5.3 Fake LLM (`FG_FAKE_LLM=1`)

When set, `call_tool` never touches the network:

- Load `tests/fake_responses.json`: a list of `{"tool": "<tool name>", "match": "<substring of the user content>", "args": {...}}`.
- Return the first entry whose `tool` matches and whose `match` occurs in the flattened text content; otherwise return a minimal valid args object generated from the tool's JSON schema (required fields only: strings `"unknown"`, enums first value, arrays empty, booleans false, numbers 0, nullable fields null).
- `chat_with_tools` returns a fake chat model that answers with a scripted sequence when `FG_FAKE_AGENT_SCRIPT` points to a JSON file, else returns a plain text message `"(fake answer)"`.

---

## 6. Database

SQLite file at `FG_DB`. `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON` on write connections. `db.connect(readonly=True)` opens `file:<path>?mode=ro` with `uri=True`. All row access uses `sqlite3.Row`. `db.SCHEMA_VERSION = 1` is stored in `meta`; on mismatch, print a message telling the user to rebuild with a new `--db` (no automatic migrations in v1).

### 6.1 Full schema (`db.SCHEMA`)

```sql
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS roots (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,          -- absolute path
  name TEXT UNIQUE NOT NULL,
  profile TEXT NOT NULL DEFAULT 'auto',
  llm_policy TEXT NOT NULL DEFAULT 'cloud',
  added_at TEXT
);

CREATE TABLE IF NOT EXISTS repos (
  id INTEGER PRIMARY KEY,
  root_id INTEGER NOT NULL,
  rel_path TEXT NOT NULL,             -- repo folder relative to root, '/' separators, '.' for root itself
  name TEXT,
  remote_url TEXT, branch TEXT, head_commit TEXT, last_commit_at TEXT,
  languages TEXT DEFAULT '{}',        -- JSON {"python": 42, "typescript": 10} (file counts)
  summary TEXT,                       -- short LLM summary (Section 12.4)
  summary_json TEXT,                  -- full LLM output
  summary_input_sha TEXT,             -- cache key for the summary
  missing INTEGER DEFAULT 0,
  UNIQUE(root_id, rel_path)
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  root_id INTEGER NOT NULL,
  rel_path TEXT NOT NULL,             -- relative to root; attachments: 'a/b/mail.msg::contract.pdf'
  folder TEXT NOT NULL,               -- rel folder, '.' for root
  filename TEXT NOT NULL,
  ext TEXT,
  kind TEXT,                          -- document | data | email | image | code | other
  size INTEGER, mtime REAL, sha256 TEXT,
  source TEXT DEFAULT 'disk',         -- disk | attachment
  cache_path TEXT,                    -- where attachment bytes were saved
  parent_id INTEGER,                  -- email the attachment came from
  repo_id INTEGER,                    -- set when the file is inside a git repo
  language TEXT,                      -- code language when kind='code'
  tier INTEGER DEFAULT 0,             -- 0 metadata, 1 text indexed, 2 card extracted
  page_count INTEGER DEFAULT 0,
  is_scanned INTEGER DEFAULT 0,
  scan_pages TEXT DEFAULT '[]',
  text_chars INTEGER DEFAULT 0,
  flags TEXT DEFAULT '{}',            -- parser flags, e.g. {"draft_marker": true, "tracked_changes": true}
  email_meta TEXT,
  parse_status TEXT,                  -- ok | error | unsupported | skipped_large | redacted
  parse_error TEXT,
  missing INTEGER DEFAULT 0,
  ingested_at TEXT,
  UNIQUE(root_id, rel_path)
);
CREATE INDEX IF NOT EXISTS ix_files_sha ON files(sha256);
CREATE INDEX IF NOT EXISTS ix_files_folder ON files(root_id, folder);
CREATE INDEX IF NOT EXISTS ix_files_parent ON files(parent_id);
CREATE INDEX IF NOT EXISTS ix_files_repo ON files(repo_id);

-- Units: pages, sections, or code symbols
CREATE TABLE IF NOT EXISTS pages (
  file_id INTEGER NOT NULL,
  page INTEGER NOT NULL,              -- unit number, 1-based
  label TEXT,                         -- 'Page 3', 'Section 2', 'def login_user', 'class Session'
  start_line INTEGER, end_line INTEGER,   -- for code and text units, else NULL
  text TEXT,
  PRIMARY KEY (file_id, page)
);

-- One FTS row per unit
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  file_id UNINDEXED, page UNINDEXED, path, label, text, tokenize='porter unicode61');

CREATE TABLE IF NOT EXISTS embeddings (
  file_id INTEGER NOT NULL,
  page INTEGER NOT NULL,              -- 0 = the card (title + summary); >=1 = unit
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL,               -- float32 little-endian, L2-normalised
  PRIMARY KEY (file_id, page, model)
);

CREATE TABLE IF NOT EXISTS classification_cache (
  sha256 TEXT NOT NULL, registry_hash TEXT NOT NULL,
  card_type TEXT, suggested_type TEXT, confidence TEXT, model TEXT, created_at TEXT,
  PRIMARY KEY (sha256, registry_hash)
);

CREATE TABLE IF NOT EXISTS extraction_cache (
  sha256 TEXT NOT NULL, card_type TEXT NOT NULL, card_version INTEGER NOT NULL,
  model TEXT, data TEXT, input_tokens INTEGER, output_tokens INTEGER, created_at TEXT,
  PRIMARY KEY (sha256, card_type, card_version)
);
CREATE TABLE IF NOT EXISTS extraction_errors (file_id INTEGER, stage TEXT, error TEXT, at TEXT);

-- The card: fixed core columns + JSON extras
CREATE TABLE IF NOT EXISTS documents (
  file_id INTEGER PRIMARY KEY,
  card_type TEXT NOT NULL,
  card_version INTEGER,
  category TEXT,
  title TEXT,
  summary TEXT,
  status TEXT,
  main_entity_name TEXT,
  main_entity_id INTEGER,
  main_date TEXT,                     -- ISO YYYY-MM-DD
  main_date_label TEXT,
  confidence TEXT,                    -- high | medium | low
  needs_review INTEGER DEFAULT 0,
  review_reasons TEXT DEFAULT '[]',
  suggested_type TEXT,                -- most specific type the model saw, snake_case (Section 16)
  extras TEXT DEFAULT '{}',           -- JSON: card-specific typed fields
  refs TEXT DEFAULT '[]',             -- JSON: references to other documents (Section 14.3)
  model TEXT,
  extracted_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_docs_type ON documents(card_type);
CREATE INDEX IF NOT EXISTS ix_docs_main_date ON documents(main_date);
CREATE INDEX IF NOT EXISTS ix_docs_entity ON documents(main_entity_id);

CREATE TABLE IF NOT EXISTS doc_dates (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  label TEXT NOT NULL,                -- from the card's date_labels, or 'other'
  label_detail TEXT,                  -- free text when label='other' or to qualify it
  date TEXT NOT NULL,                 -- ISO YYYY-MM-DD
  basis TEXT,                         -- stated | inferred | signature | partial
  quote TEXT, page INTEGER
);
CREATE INDEX IF NOT EXISTS ix_dates_label ON doc_dates(label, date);
CREATE INDEX IF NOT EXISTS ix_dates_file ON doc_dates(file_id);

CREATE TABLE IF NOT EXISTS doc_facts (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  key TEXT NOT NULL,                  -- snake_case, normalised (Section 9.5)
  value_text TEXT,
  value_num REAL,                     -- parsed number when the value is numeric/amount
  unit TEXT,                          -- currency code or unit, e.g. 'INR', 'USD', 'kg'
  quote TEXT, page INTEGER
);
CREATE INDEX IF NOT EXISTS ix_facts_key ON doc_facts(key);
CREATE INDEX IF NOT EXISTS ix_facts_file ON doc_facts(file_id);

CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                 -- person | organization | place | product | other
  name TEXT NOT NULL,                 -- best display name
  norm_key TEXT NOT NULL,
  aliases TEXT DEFAULT '[]',
  is_self INTEGER DEFAULT 0,
  UNIQUE(kind, norm_key)
);

CREATE TABLE IF NOT EXISTS doc_entities (
  file_id INTEGER NOT NULL,
  entity_id INTEGER NOT NULL,
  role TEXT NOT NULL DEFAULT 'mentioned',   -- e.g. counterparty, issuer, holder, sender, recipient, author
  raw_name TEXT,
  PRIMARY KEY (file_id, entity_id, role)
);
CREATE INDEX IF NOT EXISTS ix_de_entity ON doc_entities(entity_id);

CREATE TABLE IF NOT EXISTS code_symbols (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL, repo_id INTEGER,
  name TEXT NOT NULL, qualname TEXT, kind TEXT,     -- function | class | method | interface | const
  start_line INTEGER, end_line INTEGER, signature TEXT, docstring TEXT
);
CREATE INDEX IF NOT EXISTS ix_sym_name ON code_symbols(name);
CREATE INDEX IF NOT EXISTS ix_sym_file ON code_symbols(file_id);

CREATE TABLE IF NOT EXISTS code_imports (
  file_id INTEGER NOT NULL, module TEXT NOT NULL, resolved_file_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_imp_file ON code_imports(file_id);

CREATE TABLE IF NOT EXISTS edges (
  id INTEGER PRIMARY KEY,
  src_id INTEGER NOT NULL,            -- file id
  dst_id INTEGER,                     -- file id; NULL when the referenced document was not found
  edge_type TEXT NOT NULL,            -- Section 14.2
  method TEXT NOT NULL,               -- structural | stated | code
  confidence REAL, evidence TEXT, page INTEGER,
  ref TEXT                            -- JSON of the original reference (for unresolved ones)
);
CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges(dst_id);

CREATE TABLE IF NOT EXISTS filing_log (
  id INTEGER PRIMARY KEY, at TEXT, source_path TEXT, root_name TEXT, suggested_folder TEXT,
  is_new_folder INTEGER, confidence REAL, alternatives TEXT, reasoning TEXT, action TEXT, dest_path TEXT);

CREATE TABLE IF NOT EXISTS run_log (
  id INTEGER PRIMARY KEY, at TEXT, command TEXT, stats TEXT);

DROP VIEW IF EXISTS catalog;
CREATE VIEW catalog AS
SELECT f.id AS file_id, r.name AS root, f.rel_path AS path, f.folder, f.filename, f.ext, f.kind,
       f.source, f.parent_id, f.repo_id, f.language,
       d.card_type, d.category, d.title, d.summary, d.status,
       d.main_entity_name, d.main_entity_id, e.name AS main_entity,
       d.main_date, d.main_date_label, d.confidence, d.needs_review, d.review_reasons,
       d.suggested_type, d.extras,
       (SELECT MIN(f2.id) FROM files f2 WHERE f2.sha256 = f.sha256 AND f2.id < f.id AND f2.missing = 0) AS duplicate_of
FROM files f
JOIN roots r ON r.id = f.root_id
JOIN documents d ON d.file_id = f.id
LEFT JOIN entities e ON e.id = d.main_entity_id
WHERE f.missing = 0;
```

### 6.2 Helpers in `db.py`

`now()` (UTC ISO seconds), `connect(path=None, readonly=False)`, `get_meta/set_meta`, `root_path(conn, root_id) -> Path`, `abs_path(conn, frow) -> Path` (attachment → `cache_path`, else root path + rel_path), `page_texts(conn, file_id) -> list[(page, text)]`, `jloads(s, default)`, `ensure_fts5()` (create a temp FTS5 table; on failure exit with: `Your Python's SQLite lacks FTS5. Install Python from python.org or use a build with FTS5.`).

### 6.3 Example queries the design must support

```sql
-- Everything that expires before 2028, across all card types
SELECT c.title, d.date, c.path FROM doc_dates d JOIN catalog c USING (file_id)
WHERE d.label = 'expires' AND d.date < '2028-01-01' AND c.duplicate_of IS NULL ORDER BY d.date;

-- Contracts effective in June 2026 (legal card)
SELECT title, main_date, path FROM catalog
WHERE card_type = 'legal' AND main_date_label = 'effective' AND substr(main_date,1,7) = '2026-06'
  AND status = 'executed' AND duplicate_of IS NULL;

-- Insurance premiums over 10,000
SELECT c.title, f.value_num, f.unit FROM doc_facts f JOIN catalog c USING (file_id)
WHERE f.key = 'premium' AND f.value_num > 10000;

-- Card-specific typed field
SELECT title FROM catalog WHERE card_type='legal' AND json_extract(extras,'$.auto_renewal') = 1;

-- Which generic documents might deserve their own card type
SELECT suggested_type, COUNT(*) n FROM documents WHERE card_type='generic'
GROUP BY suggested_type ORDER BY n DESC;
```

---

## 7. Ingestion (tier 0 and tier 1, no AI)

### 7.1 Command

`fg ingest [--root NAME] [--workers N]` ingests every configured root (or one). `fg roots` lists roots with file counts. Roots from `filegraph.yaml` are upserted into `roots` on every run; a root removed from YAML is marked by setting all its files `missing=1` (data kept, hidden from `catalog`).

### 7.2 Walking (`walk.py`)

`walk_root(root_path, settings) -> Iterator[Path]`:

1. Use `os.walk` with `topdown=True` and prune `dirnames` in place.
2. Skip: names starting with `.` or `~` (except none), Office lock files `~$*`, `thumbs.db`, `.ds_store`, `desktop.ini`.
3. Apply ignore patterns from config with `pathspec` (gitwildmatch) relative to the root.
4. When entering a directory that contains a `.gitignore`, add its patterns for that subtree (stack of `PathSpec` objects keyed by directory). Patterns apply relative to the directory holding the `.gitignore`.
5. Apply the deny list (Section 7.4) before yielding. Denied files are counted (`denied`) but never opened or stored.
6. Never follow symlinks (`followlinks=False`) and skip symlinked files.

Repo detection happens during the walk: a directory containing `.git` (directory or file) is a repo. Record `(root_id, rel_path)` for each repo; files under it get that `repo_id` (nearest enclosing repo wins for nested repos).

### 7.3 Ingest algorithm (`ingest.py`)

Port LegalGraph's `ingest()` with these changes:

1. For each root: load known files `{rel_path: (id, size, mtime)}` for `source='disk'`. A file is skipped when size matches and `abs(mtime_diff) < 1s`. Collect `todo`.
2. Files no longer seen get `missing=1` (and their attachments).
3. Classify `kind` by extension (Section 8.1). Files over `max_file_mb` get `parse_status='skipped_large'`, tier 0, no hashing of content beyond size+mtime (sha256 left NULL).
4. Parse `todo` in a `ProcessPoolExecutor` (`workers = min(8, cpu_count-1)` by default). Worker function `parse_job(path_str, kind) -> (path_str, sha256, result)` is a top-level function (picklable). Commit every 200 files.
5. Store the file row, then replace its `pages` and `fts` rows. FTS gets one row per unit: `(file_id, page, path, label, text)`. Text stored per file is capped at `max_text_chars_per_file`.
6. Email attachments: port `_store_attachments` (depth ≤ 2, cache under `attachments_dir/<sha[:16]>/<safe name>`, `rel_path = parent_rel + '::' + safe_name`, `source='attachment'`).
7. Code files go through `codeparse.parse_code` (Section 12) and additionally fill `code_symbols` and `code_imports`.
8. Set `tier=1` when text was stored, else `tier=0`.
9. Update `repos` metadata (Section 12.1).
10. Write a `run_log` row with counts.

### 7.4 Security (`security.py`)

**Built-in deny list** (always applied, in addition to config `deny`), matched on the path relative to the root and on the filename:

```
**/.env  **/.env.*  **/*.pem  **/*.key  **/*.p12  **/*.pfx  **/id_rsa*  **/id_ed25519*  **/id_ecdsa*
**/.ssh/**  **/.gnupg/**  **/.aws/credentials  **/.netrc  **/.npmrc  **/.pypirc  **/*.kdbx  **/*.keychain*
**/Login Data  **/Cookies  **/*.sqlite-wal  **/AppData/**/Browser*/**  **/Library/Keychains/**
```

`.env.example` and `.env.sample` are allowed (they contain no secrets by convention), but still pass through redaction.

**Redaction** `redact(text) -> (text, n_redacted)`: replace matches with `[REDACTED]` before any text is stored in `pages`/`fts` *and* before any content is sent to an LLM. Patterns (case-insensitive where sensible):

- `-----BEGIN [A-Z ]*PRIVATE KEY-----` … `-----END [A-Z ]*PRIVATE KEY-----`
- AWS access keys `AKIA[0-9A-Z]{16}`, GitHub tokens `gh[pousr]_[A-Za-z0-9]{36,}`, Slack `xox[abprs]-[A-Za-z0-9-]{10,}`, OpenAI-style `sk-[A-Za-z0-9_-]{20,}`, Google `AIza[0-9A-Za-z_-]{35}`
- JWTs `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`
- Assignments: `(api[_-]?key|secret|token|password|passwd|pwd|client[_-]?secret)\s*[:=]\s*['"]?([^\s'"]{8,})` → keep the key name, redact the value.

If redaction fires, set `flags.redacted = n`. Never store the original value anywhere.

### 7.5 Inventory

`fg inventory` (also printed after `ingest`): per root, a table of `kind / ext / files / scanned / pages / errors`, counts of attachments, exact duplicates, denied files, large files skipped, repos found, and the **extraction estimate** (Section 11.7).

---

## 8. Document parsers (`parse.py`)

### 8.1 Kinds by extension

| kind | extensions |
|---|---|
| document | `.pdf .docx .doc .rtf .txt .md .markdown .html .htm .odt*` |
| email | `.msg .eml` |
| data | `.xlsx .xlsm .csv .tsv .json* .yaml* .yml*` (`*` = only outside repos; inside repos these are code) |
| image | `.png .jpg .jpeg .tif .tiff .webp .heic*` |
| code | Section 12.2 extension map |
| other | everything else (tier 0, `parse_status='unsupported'`) |

`.odt` and `.heic` are optional: parse if a library is available, otherwise `unsupported`.

### 8.2 Parse result contract

Every parser returns a dict (never raises; `parse_file` wraps exceptions):

```python
{
  "ok": bool, "error": str|None, "unsupported": bool (optional),
  "ext": ".pdf",
  "pages": [(1, "text"), (2, "")],          # units; "" for scanned pages
  "labels": {1: "Page 1"},                  # optional; default "Page N" for pdf/images, "Section N" otherwise
  "lines": {1: (1, 120)},                   # optional start/end line per unit (text and code)
  "page_count": int, "is_scanned": 0|1, "scan_pages": [int],
  "text_chars": int, "flags": {}, "email_meta": dict|None,
  "attachments": [(name, bytes)]
}
```

### 8.3 Parsers to implement (port from LegalGraph unless noted)

- **PDF** (`pymupdf`): per-page text; a page with fewer than 25 non-space characters is a scanned page (`text=""`, added to `scan_pages`); `is_scanned=1` when ≥ 50% of pages are scanned; `encrypted PDF (password required)` error when locked; `draft_marker` flag when `\bDRAFT\b` appears in the first 3 pages.
- **DOCX** (`python-docx`): iterate `w:p` in body, join `w:t` (includes tracked insertions, skips `w:delText`); include header/footer text as a prefix line `[Header/footer text: ...]`; flags `tracked_changes`, `has_comments`, `draft_marker`. Chunk with `chunk_text` (4,000 chars, break at newline after half the size).
- **DOC**: convert with LibreOffice (`soffice --headless --convert-to docx`) in a temp dir, timeout 180s; if LibreOffice is absent → `unsupported` with message `legacy .doc needs LibreOffice installed for conversion`.
- **MSG** (`extract_msg`): body (fallback to stripped HTML), header lines Subject/From/To/Cc/Date, strip `\x00` from all fields, attachments as bytes; skip images under 40 KB (signatures/logos); note embedded messages in `email_meta.embedded_messages_not_unpacked`.
- **EML** (`email` stdlib, `policy.default`): same output shape as MSG.
- **XLSX/XLSM** (`openpyxl`, `read_only=True, data_only=True`): `## Sheet: <name>` then rows joined with ` | `, max 300 rows per sheet with a truncation line.
- **CSV/TSV**: first 300 lines + `... (N more lines)`.
- **TXT/MD**: decode with `utf-8-sig`, then `cp1252`, then `latin-1`; cap 400 KB; chunk. For `.md`, prefer splitting at headings (`^#{1,3} `) when sections are ≤ 4,000 chars; label units with the heading text.
- **HTML**: strip `script/style`, convert `br/p/div/tr/li` to newlines, strip tags, unescape entities; chunk.
- **RTF**: port the regex stripper.
- **JSON/YAML (outside repos)**: pretty-print the first 300 lines; chunk.
- **Images**: one empty unit per frame (TIFF multi-frame), all scanned.

### 8.4 Text normalisation

Before storing: remove `\x00`, normalise `\r\n` to `\n`, collapse runs of more than 3 blank lines, apply `security.redact`.

---

## 9. Card registry (`cards.py`, `cards/*.yaml`)

Cards are data, not code. Adding a file to `cards/` adds a card type. No schema change, no code change.

### 9.1 YAML format

```yaml
name: insurance_policy           # snake_case, unique; equals the file stem
version: 1                       # bump to force re-extraction of documents of this type
description: Insurance policies of any kind (car, health, home, life, travel).
llm: true                        # false = deterministic card (only 'code' uses this)
priority: 50                     # higher wins when several cards match by rules
absorbs: [insurance_policy, car_insurance, health_insurance]   # suggested_type values this card takes over
match:                           # rule-based routing (Section 10); all keys optional
  extensions: [.pdf, .docx]
  path_globs: ["**/Insurance/**", "**/*policy*"]
  filename_regex: "(?i)polic(y|ies)|insurance"
  keywords_any: ["policy number", "sum insured", "premium", "insured"]
  min_keyword_hits: 2
categories: [vehicle_insurance, health_insurance, home_insurance, life_insurance, travel_insurance, other]
statuses: [active, expired, cancelled, quote, unknown]
main_entity: {role: insurer, kind: organization}
main_date: expires               # label used for documents.main_date (or a list in priority order)
date_labels: [start, expires, renewal_due, issued, claim]
entity_roles: [insurer, policy_holder, insured_person, agent, other]
preferred_fact_keys: [policy_number, premium, sum_insured, deductible, vehicle_registration, plan_name]
fact_key_aliases:                # normalisation map applied after extraction
  policy_no: policy_number
  sum_assured: sum_insured
fields:                          # typed card-specific fields, stored in documents.extras
  coverage_type: {type: enum, values: [comprehensive, third_party, individual, family_floater, other], description: Kind of cover}
  no_claim_bonus_percent: {type: number}
relations: [renews_or_extends, amends, references]
instructions: |
  The insurer is the main entity. Premium is the total payable for the policy period, with currency.
review_rules:
  required_dates:                # reason added when the label is missing
    - {label: expires, unless_status: [quote]}            # optional keys: unless_status, for_categories
  flag_values:                   # reason added when a field has one of the values
    - {field: coverage_type, values: [other], reason: "coverage type unclear"}
```

### 9.2 Field types → JSON Schema

| type | JSON Schema |
|---|---|
| `text` | `{"type": ["string","null"]}` |
| `number` | `{"type": ["number","null"]}` |
| `boolean` | `{"type": ["boolean","null"]}` |
| `date` | `{"type": ["string","null"], "description": "ISO YYYY-MM-DD or null"}` |
| `amount` | `{"type": ["object","null"], "properties": {"value": {"type":"number"}, "currency": {"type":"string"}, "as_written": {"type":"string"}}}` |
| `enum` | `{"type": ["string","null"], "enum": [...values, null]}` |
| `list` | `{"type": "array", "items": {"type": "string"}}` |

### 9.3 Compiled extraction tool

`compile_tool(card) -> dict` builds a tool named `record_card`. **Core properties are identical for every LLM card** (this is what keeps the database schema fixed). The block below is a template: values in `<angle brackets>` are filled from the card.

```jsonc
{
 "category":   {"type":"string","enum": <card.categories>},
 "title":      {"type":"string","description":"Short human title, e.g. 'ICICI Lombard - Car insurance policy (2026)'"},
 "summary":    {"type":"string","description":"2-3 sentences: what this is and what it does"},
 "status":     {"type":"string","enum": <card.statuses>},
 "language":   {"type":["string","null"],"description":"ISO 639-1 code of the main language"},
 "entities":   {"type":"array","items":{"type":"object","properties":{
                   "name":{"type":"string"},
                   "kind":{"type":"string","enum":["person","organization","place","product","other"]},
                   "role":{"type":["string","null"],"description":"One of: <card.entity_roles>"}},
                 "required":["name","kind"]}},
 "main_entity":{"type":["string","null"],"description":"The <card.main_entity.role>: full name as written; never the user or the user's own organizations"},
 "dates":      {"type":"array","items":{"type":"object","properties":{
                   "label":{"type":"string","enum": <card.date_labels + ["other"]>},
                   "label_detail":{"type":["string","null"]},
                   "date":{"type":"string","description":"ISO YYYY-MM-DD; YYYY-MM if only month known"},
                   "basis":{"type":"string","enum":["stated","inferred","signature","partial"]},
                   "quote":{"type":"string","description":"Exact words, max 300 chars"},
                   "page":{"type":["integer","null"]}},
                 "required":["label","date","quote"]}},
 "facts":      {"type":"array","items":{"type":"object","properties":{
                   "key":{"type":"string","description":"snake_case. Prefer: <card.preferred_fact_keys>"},
                   "value":{"type":"string"},
                   "number":{"type":["number","null"]},
                   "unit":{"type":["string","null"],"description":"ISO currency code or unit"},
                   "quote":{"type":["string","null"]},
                   "page":{"type":["integer","null"]}},
                 "required":["key","value"]}},
 "references": {"type":"array","description":"Other documents this one points to","items":{"type":"object","properties":{
                   "relation":{"type":"string","enum": <card.relations + ["references"]>},
                   "title":{"type":"string"},
                   "date":{"type":["string","null"]},
                   "entity":{"type":["string","null"]},
                   "quote":{"type":"string"},
                   "page":{"type":["integer","null"]}},
                 "required":["relation","title","quote"]}},
 "fields":     <object built from card.fields, omitted if the card has none>,
 "suggested_type": {"type":"string","description":"The most specific document type in snake_case, e.g. car_insurance_policy, salary_slip, rental_agreement"},
 "confidence": {"type":"string","enum":["high","medium","low"]},
 "review_reasons": {"type":"array","items":{"type":"string"}}
}
```

Required: `category, title, summary, status, entities, dates, facts, references, suggested_type, confidence, review_reasons` (plus `fields` when present).

### 9.4 Compiled system prompt

`system_prompt(card, settings) -> str` concatenates:

1. The **core prompt** (Appendix A.1), with `{me}` = configured names and organizations.
2. A card section: `Card type: <name> - <description>`, the allowed date labels, entity roles, preferred fact keys, field descriptions.
3. `card.instructions` verbatim.

### 9.5 Registry behaviour

- `load_registry(cards_dir) -> Registry` reads every `*.yaml` (not `proposed/`), validates against a JSON Schema for the YAML format, and exits with a clear message naming the file and the problem when invalid.
- `registry_hash` = sha256 of the sorted `(name, version, match, absorbs)` of all cards; used as the classification cache key so adding a card re-routes documents.
- Fact key normalisation after extraction: lowercase, non-alphanumerics → `_`, collapse repeats, strip `_`, then apply `fact_key_aliases` of the card and of `generic`.
- The `generic` card must always exist. If it is missing, write the built-in default from Appendix B.1 and warn.
- `main_date` may be a single label or a list of labels in priority order.
- Cards with `llm: false` may leave LLM-related keys empty or null; they are never offered to the classifier and never compiled into a tool.

### 9.6 Built-in cards (ship these files)

Full YAML in Appendix B: `generic` (fallback, broad categories), `identity` (passports, ID cards, licences, visas, certificates), `finance` (bank statements, payslips, invoices, receipts, tax documents, loan papers), `legal` (port of LegalGraph's legal schema), `code` (deterministic, `llm: false`, never sent to the router's LLM step). Repo summaries are not a card file: they use the fixed `record_repo` tool defined in `repos.py` (Section 12.4).

---

## 10. Routing (`router.py`)

`route(conn, frow, registry, settings) -> (card_type, how)` where `how` ∈ `code|profile|rules|llm|fallback|policy`. Order:

1. `frow.repo_id` is set, or `kind == 'code'` → `code` (`how='code'`).
2. Root `llm_policy == 'metadata_only'` → `generic` with no LLM (`how='policy'`, Section 11.5).
3. Root `profile != 'auto'` → that card (`how='profile'`).
4. **Rules:** for each LLM card with a `match` block, compute a score: extension match required if `extensions` given; +3 for any `path_globs` match on `rel_path`; +3 for `filename_regex` match; +1 per distinct `keywords_any` hit (case-insensitive) in the first 5,000 characters of text; card qualifies when (glob or regex matched) or keyword hits ≥ `min_keyword_hits` (default 2). Highest `priority`, then highest score, wins (`how='rules'`).
5. **LLM classification** (cached in `classification_cache` by `sha256 + registry_hash`): tool `classify_document` with properties `card_type` (enum of LLM card names), `suggested_type` (snake_case string), `confidence` (high/medium/low). Input: file path, extension, email subject if any, and the first 3,000 characters (or the first page image when fully scanned and the model supports vision). Use `FG_CLASSIFY_MODEL` (or the local model per policy). Low confidence → `generic` (`how='llm'`).
6. Any failure → `generic` (`how='fallback'`).

**Promotion check (between steps 3 and 4):** if the file's sha256 has a `documents.suggested_type` or `classification_cache.suggested_type` listed in some card's `absorbs`, route it to that card (`how='rules'`). This is how a newly added card takes over documents that were previously `generic` (Section 16).

---

## 11. Extraction (tier 2, `extract.py`, `inputs.py`)

### 11.1 Command

```
fg extract [--limit N] [--workers N] [--model KEY] [--root NAME] [--card NAME]
           [--redo-low] [--refresh] [--yes] [--dry-run]
```

- Default: extract every pending document.
- `--card NAME --refresh`: re-route and re-extract documents currently of type `NAME` **or** whose `suggested_type` is in `NAME.absorbs`.
- `--redo-low`: re-extract `confidence='low'` documents with `FG_ESCALATE_MODEL`.
- `--dry-run`: print the estimate and the routing decisions (`card_type`, `how`) for the first 50 pending files, then exit.
- When the estimate exceeds `limits.confirm_tokens_over`, ask `Proceed? [y/N]` unless `--yes`.

### 11.2 Pending set

One representative file per unique `sha256` (lowest `id` among non-missing, `parse_status='ok'` files) where: `kind in ('document','email','data','image')`, `repo_id IS NULL`, (`text_chars>0` or has scanned pages), root policy is not `metadata_only`, and no `extraction_cache` row exists for `(sha256, routed card_type, card.version)`.

### 11.3 Content blocks (`inputs.py`)

Port LegalGraph's `_plan`, `select_pages`, `_image_budget` and `build_blocks` exactly, generalised:

- PDFs and scanned files: first `head_pages` + last `tail_pages`; plus up to 2 extra pages matching the signature regex `IN WITNESS WHEREOF|DocuSigned by|Signature Page|/s/|\bBy:\s*_{4,}` (keep for legal-like documents; harmless otherwise).
- Text formats: units from the start up to 75% of `max_text_chars`, then units from the end within the remaining budget.
- Scanned pages are sent as JPEG images (PDF pages rendered at `image_dpi`; image files thumbnailed to 1600 px) within `max_image_pages` (keep first two-thirds and last third when over budget).
- If the chosen model does not support vision, omit images, add the line `Scanned pages were not sent (text-only model).` and force a review reason.
- Header lines: `Today: YYYY-MM-DD`, `File path`, `Root`, `File type | pages`, which pages are included, parser flags, email metadata (JSON, ≤ 1,500 chars), parent email if an attachment.
- Unit markers: `=== Page N ===` for PDFs/images, `=== Section N ===` otherwise.
- Close with `--- END OF CONTENT ---\nRecord this document with the record_card tool.`
- All text passes through `security.redact` again (defence in depth).

### 11.4 Run loop

Port `extract_sync`: thread pool (`workers`), one read-only connection per worker for building content, results written on the main thread, commit after each result, fatal errors stop the pool and keep finished work, other errors go to `extraction_errors(stage='extract')`. Model per file: `llm.model_for_policy(root.llm_policy, 'extract')` unless `--model` given (a cloud `--model` is ignored for `local` roots, with a warning).

### 11.5 Validation and materialisation

`materialize(conn, file_id, card, data, model)`:

1. Validate `data` with `jsonschema` against the compiled tool schema. On errors, **repair rather than fail**: drop unknown keys, replace invalid enum values with `other`/`unknown`, drop malformed list items, and add a review reason `schema repair: <field>`.
2. Clean dates with the ported `_clean_date`: `YYYY-MM` → `YYYY-MM-01` + reason `"<label> date: only month and year known"` and `basis='partial'`; invalid → dropped + reason.
3. Normalise fact keys (Section 9.5). Parse `value_num` from `number` or from the value string (strip currency symbols, thousands separators including Indian grouping `1,23,456.78`).
4. Pick `main_date`: first date whose label equals `card.main_date` (or first match in its priority list). For `generic`, priority `expires, due, effective, start, issued, event, signed`.
5. Pick `main_entity_name` from `data.main_entity`; if empty, the first entity whose role equals `card.main_entity.role`. Never a self entity.
6. Apply review rules: `required_dates` (label missing, status not in `unless_status`, and category in `for_categories` when given → reason `no <label> date found`), `flag_values` (a core field such as `status`/`category`, or a card field from `fields`, has a listed value → its `reason`, default `<field> is <value>`), confidence `low` → reason `low extraction confidence`. `needs_review = 1` when any reason exists.
7. Delete then insert: `documents` row (card fields go to `extras` as JSON), `doc_dates`, `doc_facts`, and raw entities into a staging JSON inside `extras._entities` (linking resolves them, Section 14). Store references in `documents.refs`.
8. `materialize_all(conn)` fills `documents` for every non-missing file whose sha256 has a cache row for its routed card (duplicates get identical cards for free) and deletes cards of missing files. Port from LegalGraph.

**Deterministic cards (no LLM):**

- `code` card: see Section 12.3.
- `metadata_only` policy: `card_type='generic'`, `title=filename`, `summary=NULL`, `category='other'`, `status='unknown'`, `confidence='low'`, review reason `not analysed (metadata_only root)`.

Mark processed files `tier=2`.

### 11.6 Caching rules

- `extraction_cache` key: `(sha256, card_type, card_version)`. Changing a card's `version` makes its documents pending again; other cards are unaffected.
- `--redo-low` deletes cache rows of low-confidence documents before running.
- The cache stores raw model output; materialisation is always re-runnable from cache without calling the model.

### 11.7 Estimate

Port `estimate_tokens`: per pending file, text chars / 4 + images × 1,500 + 2,500 overhead input tokens; 900 output tokens. Add classification calls (≈ 1,200 input / 60 output) for files not routed by rules. Print totals per model and the number of image pages.

---

## 12. Code profile (`codeparse.py`, `repos.py`)

### 12.1 Repo metadata

For each repo found during the walk: `name` = folder name; if `git` is on PATH, run with a 10 s timeout in the repo dir: `git config --get remote.origin.url`, `git rev-parse --abbrev-ref HEAD`, `git rev-parse HEAD`, `git log -1 --format=%cI`. Missing git → leave NULL. `languages` = JSON count of code files per language. Repos not found again → `missing=1`.

### 12.2 Language map

```
.py python | .js .mjs .cjs javascript | .ts .tsx typescript | .jsx javascript | .java java | .kt kotlin
.go go | .rs rust | .rb ruby | .php php | .cs csharp | .cpp .cc .cxx .hpp .h .c c_cpp | .swift swift
.scala scala | .sh .bash shell | .ps1 powershell | .sql sql | .r .R r | .m matlab_objc | .dart dart
.lua lua | .pl perl | .ipynb notebook | .vue vue | .svelte svelte
config-like (inside repos only): .json .yaml .yml .toml .ini .cfg .md .rst .txt .xml .gradle Dockerfile Makefile
```

### 12.3 Parsing code

`parse_code(path, language) -> result` (same contract as Section 8.2 plus `symbols` and `imports` lists):

- **Python** (required, stdlib `ast`): top-level functions and classes, and methods (`Class.method`). Each symbol: `name, qualname, kind, start_line, end_line (node.end_lineno), signature (first line of the def), docstring (ast.get_docstring, ≤ 500 chars)`. Imports: `import x`, `from x import y` (keep relative level).
- **Other languages**: if `tree_sitter_language_pack` is installed, use it to get function/class/method nodes and import statements; otherwise regex heuristics:
  - JS/TS: `^\s*(export\s+)?(default\s+)?(async\s+)?function\s+(\w+)`, `^\s*(export\s+)?class\s+(\w+)`, `^\s*(export\s+)?const\s+(\w+)\s*=\s*(async\s*)?\(`; imports `import .* from ['"](.+)['"]`, `require\(['"](.+)['"]\)`.
  - Java/Kotlin/C#: class/interface declarations and method signatures with a body brace.
  - Go: `^func\s+(.*?\s*)?(\w+)`; imports in `import (...)` blocks.
  - Others: no symbols; plain chunking.
  - End line for regex symbols = line before the next symbol start (or EOF).
- **Notebooks** (`.ipynb`): code and markdown cells concatenated with `# %% [cell N]` markers, then parsed as Python.
- **Units:** one unit per symbol (label `def name` / `class Name` / `method Class.name`), plus one unit `module` for code outside symbols. Split any unit over 4,000 chars at line boundaries (`label` gets ` (part k)`).
- **Config-like files and docs inside repos:** plain text chunking (Markdown by heading).

**Import resolution:** Python: map `a.b.c` to `<repo>/a/b/c.py` or `<repo>/a/b/c/__init__.py`, searching from the repo root and from common source roots (`src/`); relative imports resolve from the importing file's package. JS/TS: relative specifiers `./x`, `../x` → try `x.ts, x.tsx, x.js, x.jsx, x/index.*`. Resolved imports produce `edges(edge_type='imports', method='code')` in linking.

**Deterministic `code` card** (materialised during `fg extract` or `fg link`, no LLM): `card_type='code'`, `category=<language>`, `title=<repo name>/<rel path in repo>`, `summary` = module docstring / first comment block (≤ 400 chars) or NULL, `status='unknown'`, `confidence='high'`, `extras = {"language":..., "symbols": n, "imports": [...first 50], "repo": name}`.

### 12.4 Repo summaries (fixed `record_repo` tool, LLM)

`fg repos summarize [--repo NAME] [--refresh]` (also run at the end of `fg extract` when `code.repo_summaries: true`).

Input (redacted): README (first 8,000 chars), each manifest found at the repo root (`package.json, pyproject.toml, setup.py, requirements.txt, go.mod, Cargo.toml, pom.xml, build.gradle, Gemfile, composer.json, Dockerfile, docker-compose.yml, Makefile`) first 3,000 chars each, directory tree to depth 3 (max 300 lines, ignored paths excluded), and up to 200 symbol qualnames with file paths. Cache key `summary_input_sha` = sha256 of that input; skip when unchanged.

Tool `record_repo`: `summary` (2-4 sentences), `purpose`, `languages[]`, `frameworks[]`, `entry_points[{path, description}]`, `how_to_run`, `main_components[{path, description}]`, `external_services[]`, `confidence`. Store `summary` and the full JSON in `repos`. Respect the root's LLM policy.

---

## 13. Embeddings and hybrid search (`embed.py`, `search.py`)

### 13.1 Embeddings

`fg embed [--root NAME] [--rebuild]` (also run at the end of `ingest` and `extract` when a provider is available).

- Provider `local`: `sentence-transformers` with `FG_EMBED_MODEL` (default `BAAI/bge-small-en-v1.5`, 384 dims), CPU, batch size 64, `normalize_embeddings=True`.
- Provider = a model key: use its LangChain embeddings client (`LLM_INSTANCES[key]["EMBEDDINGS"]`) — **only for roots with `llm: cloud`**; roots with `local` fall back to the local provider or are skipped.
- Provider `none` or library missing: embeddings disabled; search uses FTS only; print once: `Semantic search disabled (install sentence-transformers to enable).`
- What gets embedded: unit text (first 2,000 chars, prefixed with `path` and `label`) as `page>=1`, and the card text `title + summary + main entity + main date` as `page=0` (re-embedded whenever the card changes). Skip units already embedded with the same model unless `--rebuild`.
- Store float32 L2-normalised bytes. If `sqlite-vec` is importable, also maintain a `vec0` virtual table for fast KNN; otherwise do brute-force cosine with numpy over a memory-mapped matrix built at query time (acceptable up to a few hundred thousand vectors).

### 13.2 Hybrid search

`hybrid_search(conn, query, limit=10, kind=None, root=None, card_type=None) -> list[dict]`:

1. BM25: `SELECT file_id, page, snippet(fts, 4, '[', ']', ' … ', 18), bm25(fts) FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT 50`. On FTS syntax error, retry with the query's words OR-ed and quoted (port from LegalGraph).
2. Vector: embed the query, top 50 by cosine.
3. Reciprocal Rank Fusion: `score(unit) = Σ 1/(rrf_k + rank)` over both lists (`rrf_k = 60`).
4. Apply filters (join `files`/`documents`), drop missing files, group by `file_id` keeping the best unit, return the top `limit`:
   `{file_id, path, root, kind, card_type, title, unit, label, lines, snippet, score, matched_by: ["bm25","vector"]}`.

---

## 14. Linking: entities and edges (`link.py`)

`fg link` runs both steps; `extract`, `ingest` (for code) and `run` call it at the end.

### 14.1 Entity resolution (generalises LegalGraph's counterparty merge)

- `norm_key(name, kind)`:
  - organization: lowercase, drop parenthetical asides, non `[a-z0-9& ]` → space, drop corporate suffixes (port LegalGraph's `SUFFIXES` and add `pvt, private, limited, llp, gmbh, ag, sa, bv, nv, plc, pte, pty, inc, corp, co, company, the, and, &`).
  - person: lowercase, drop honorifics (`mr mrs ms dr prof sri shri smt`), drop initials-only tokens' dots, sort nothing (keep order).
  - place/product/other: lowercase, collapse spaces and punctuation.
- Resolve every staged entity (`extras._entities` + `main_entity_name`) of every card: exact `norm_key` match within the same `kind`, else `difflib.get_close_matches(cutoff=0.9)` among keys of that kind, else create. Maintain `aliases` (sorted unique spellings) and the best display name (most frequent spelling).
- `is_self = 1` when the norm_key equals a norm_key of `me.names` (person) or `me.organizations` (organization).
- Fill `doc_entities(file_id, entity_id, role, raw_name)` and `documents.main_entity_id` (never a self entity; if the model chose the user, fall back to the first non-self entity with the card's main role).
- Delete entities no document points to.

### 14.2 Edge types

| edge_type | method | meaning |
|---|---|---|
| `attachment_of` | structural | attachment → email it came from |
| `duplicate_of` | structural | later identical file → earliest copy |
| `imports` | code | code file → code file it imports |
| `amends`, `amends_and_restates`, `supersedes`, `issued_under`, `side_letter_to`, `guarantees`, `terminates`, `renews_or_extends`, `assigns`, `replies_to`, `pays`, `references` | stated | relations extracted from document text (cards declare which ones they allow) |

Repo membership is not an edge; it is `files.repo_id`.

### 14.3 Building edges

Port `build_edges` from LegalGraph:

1. Delete edges with method `structural`, `stated`, `code`, then rebuild.
2. Structural: attachments and exact duplicates (SQL from LegalGraph).
3. Code: one `imports` edge per resolved `code_imports` row.
4. Stated: for each canonical (non-duplicate) document and each reference in `refs`:
   - candidate set = documents sharing the reference's entity (resolve `ref.entity` via `norm_key`; fall back to the document's own main entity); if no entity at all, candidates = documents in the same folder;
   - score with the ported `_score`, generalised: compare `ref.title` with candidate `title` + `extras.doc_type` (if present); numbers mismatch penalty (`Amendment No. 2` ≠ `No. 1`) −0.3; date bonus +0.4 when `ref.date` equals any of the candidate's `doc_dates`, +0.1 same year; relation bonuses as in LegalGraph mapped to card categories; +0.05 when candidate status is `executed`/`final`/`signed`;
   - threshold 0.55; below threshold insert the edge with `dst_id=NULL` and the original reference JSON in `ref` (a **missing document**).
5. Print: entities by kind, structural / code / stated-matched / unresolved counts.

---

## 15. Question-answering agent (`agent.py`)

### 15.1 Loop

Port LegalGraph's `Agent` class: a LangChain chat model bound to tools, a message list starting with the system prompt, `ask(question, max_steps=15)` that invokes the model, executes every tool call, appends `ToolMessage`s (output JSON truncated at 40,000 chars), and returns the first response without tool calls. After `max_steps`: `(Stopped after too many steps; try a narrower question.)`. `-v` prints each tool call (`  · name: args[:200]`). The agent uses a **read-only** connection and `llm.model_for_policy`: if any root is `local`, the agent uses `FG_AGENT_MODEL` only when the user passes `--allow-cloud`, otherwise `FG_LOCAL_MODEL` (because tool results contain content from local roots).

### 15.2 Tools

| Tool | Input | Behaviour |
|---|---|---|
| `run_sql` | `sql` | One `SELECT`/`WITH` statement only (reject `;` inside, reject other verbs). Up to 300 rows `{columns, rows, note?}`. |
| `search` | `query, limit?, kind?, root?, card_type?` | Hybrid search (Section 13.2); limit ≤ 30. |
| `get_related` | `file_id, depth? (1-2)` | Port LegalGraph: edges both directions, linked documents, unresolved references, plus `same_entity`: other documents sharing the main entity (≤ 60), plus `repo` info for code files. |
| `read_document` | `file_id, pages?` (`"1-3,7"`) | Stored unit texts with `=== label ===` headers, 20,000-char budget, scanned pages shown as `(scanned page - no text layer)`. |
| `read_file` | `file_id` or `path` + `root`, `start_line?, end_line?` | Live read from disk for text/code files, ≤ 400 lines per call, line-numbered. Refuses paths outside roots, denied paths and binaries. Output passes through `redact`. |
| `find_symbol` | `name, repo?` | `code_symbols` LIKE match (exact first), returns path, qualname, kind, lines, signature, docstring (≤ 50 rows). |
| `grep` | `pattern, root?, glob?, max_results? (≤ 100)` | Live regex search. Use `rg --json` when available, else Python `re` over the walker (respecting ignore + deny). Returns `path:line: text` (redacted). Timeout 20 s. |
| `list_dir` | `root, path?` | Lists entries (name, type, size) of a folder inside a root, ≤ 200 entries, ignore + deny respected. |
| `describe_cards` | none | Card registry summary: names, descriptions, categories, statuses, date labels, preferred fact keys, field names/types, and the top 50 distinct `doc_facts.key` and `doc_dates.label` values actually present with counts. |

### 15.3 System prompt

The full text is in Appendix A.2. It must contain: today's date, the user's self entities, the database description (the `catalog` view, `doc_dates`, `doc_facts`, `doc_entities`, `entities`, `edges`, `repos`, `code_symbols`, `files`), the list of card types with one-line descriptions, and the answering rules.

---

## 16. Discovering new card types (`discover.py`)

Implements the "n+1th kind of document" workflow.

- `fg discover [--min N=10]`: prints `suggested_type` counts among `card_type='generic'` documents (and among all documents with `--all`), with 3 example titles each, and marks types already absorbed by a card.
- `fg propose-card SUGGESTED_TYPE [--samples 15] [--name NAME]`: picks up to `samples` documents with that `suggested_type`, collects their titles, categories, date labels, fact keys/values (truncated), entity roles and summaries, and calls the extract model with tool `draft_card` whose output is the YAML fields of Section 9.1 (`name, description, categories, statuses, main_entity, main_date, date_labels, entity_roles, preferred_fact_keys, fact_key_aliases, fields, relations, instructions, absorbs, match`). Write `cards/proposed/<name>.yaml` with `version: 1`, validate it, print the path and the next steps:

  ```
  Review cards/proposed/insurance_policy.yaml, then move it to cards/ and run:
    fg extract --card insurance_policy --refresh
  ```

- Moving the file into `cards/` changes `registry_hash`; `fg extract --card NAME --refresh` re-routes absorbed documents and extracts only them. Text is already stored, so no re-parsing happens.

---

## 17. Filing (`filing.py`)

Port LegalGraph's `filing.py` with these generalisations:

- `fg file PATH [--root NAME] [--apply] [--move] [--yes]`. Target root = `--root`, else the root whose documents best match (highest candidate score), else the first root.
- Extraction of the new file uses the router and card registry (rules → classification → card), results cached by sha256 like any document.
- `counterparty` becomes `main_entity`; `match_counterparty` becomes `match_entity(kind, name)`.
- `candidates()`: same scoring (entity's folders ×3 share, FTS on title/main entity/suggested type words, folder-name contains entity first word, fallback by `card_type`+`category`), restricted to the target root, never inside repos.
- `folder_profile()`: counts by main entity, card type/category, date range of `main_date`, example filenames.
- Tool `choose_folder` and its system prompt are ported (Appendix A.3), with "legal archive" replaced by "the user's files".
- `--apply` copies (or moves) after confirmation, avoids overwriting (`name (2).ext`), logs to `filing_log`, then re-ingests that root, materialises and links.
- `fg backtest [-n 20] [--root NAME]`: port unchanged (hide each document and its duplicates, predict, report exact/top-3 accuracy and accuracy at confidence ≥ 0.8, write `backtest_results.csv` next to the DB).

---

## 18. CLI (`fg.py`)

`argparse` with subcommands; global `--db`, `--config`, `-q/--quiet`. Reconfigure stdout to UTF-8 (`errors="replace"`). Exit codes: 0 ok, 1 runtime error, 2 configuration error.

| Command | What it does | AI? |
|---|---|---|
| `fg init` | Copies `filegraph.yaml.example` → `filegraph.yaml` and `.env.example` → `.env` if absent; checks FTS5; prints next steps | no |
| `fg roots` | Lists configured roots, policy, file counts, last ingest time | no |
| `fg ingest [--root R] [--workers N]` | Section 7 | no |
| `fg inventory` | Section 7.5 | no |
| `fg extract [...]` | Section 11 (runs repo summaries, link and embed at the end unless `--no-post`) | yes |
| `fg repos [list\|summarize] [--repo R] [--refresh]` | Section 12 | summarize: yes |
| `fg embed [--root R] [--rebuild]` | Section 13 | local model |
| `fg link` | Section 14 | no |
| `fg run [--root R] [--limit N] [--yes]` | ingest → extract → repos summarize → link → embed → vault → graph → review-csv | yes |
| `fg search "query" [--kind K] [--root R] [--card C] [-n 10]` | Hybrid search from the command line, no LLM | no |
| `fg ask "question" [-v] [--allow-cloud]` | One question | yes |
| `fg chat [-v] [--allow-cloud]` | Conversation; empty line or Ctrl+C quits | yes |
| `fg discover [--min N] [--all]` | Section 16 | no |
| `fg propose-card TYPE [--samples N] [--name NAME]` | Section 16 | yes |
| `fg cards` | Lists card types with version, document counts, absorbs | no |
| `fg file PATH [--root R] [--apply] [--move] [--yes]` | Section 17 | yes |
| `fg backtest [-n N] [--root R] [--model M]` | Section 17 | yes |
| `fg report` | Section 19.4 | no |
| `fg review-csv [out.csv]` | Section 19.3 | no |
| `fg vault [dir]` | Section 19.1 | no |
| `fg graph [out.html]` | Section 19.2 | no |
| `fg mcp` | Starts the MCP server on stdio (Section 20) | no |
| `fg watch [--interval 300]` | Section 18.1 | yes (incremental) |

### 18.1 Freshness

`fg watch` uses `watchdog` when installed (debounce 30 s), otherwise polls every `--interval` seconds. Each cycle: `ingest` (incremental), `extract --yes` limited to new content (respects `confirm_tokens_over` by skipping and printing a notice when exceeded), `link`, `embed`. Document scheduling with cron / Windows Task Scheduler in the README.

---

## 19. Exports (`export.py`)

### 19.1 Obsidian vault

Port `vault()`. Folders: `Documents/`, `Entities/` (one note per entity with `is_self=0` and at least one document, grouped by kind in the frontmatter tag `entity/<kind>`), `Repos/` (one note per repo with summary, languages, entry points and a list of top files), `_Needs review.md`. Document notes: YAML frontmatter (`title, card_type, category, status, main_entity, main_date, main_date_label, confidence, needs_review, path, root, file_id, tags: [type/<card_type>, cat/<category>, status/<status>, needs-review?]`), link to the main entity, `[Open original file](file://...)`, summary, a "Dates" list, a "Facts" table, "Related" (port FWD/REV verbs, add `imports`/`imported by`, `replies to`, `pays`), "To check". Code files are **not** exported as notes by default (`--include-code` to add them).

### 19.2 `graph.html`

Port LegalGraph's `graph_template.py` design (vis-network 9.1.9 from `https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js`, Inter font, light/dark palette, ForceAtlas2 `springLength 140, gravitationalConstant -70, avoidOverlap 0.9, centralGravity 0.008`, curved edges, labels only for hubs/hovered/selected/zoom ≥ 1.6×, "All labels" and "Focus mode" toggles, `/` focuses search, zoom cluster, hover tooltip, slide-in detail panel with kind badge, status pill, evidence quote, connected list and "Open the original file ↗", mobile layout ≤ 820 px, `prefers-reduced-motion`). Generalise the data:

- Node kinds: `doc` (colour by `card_type`), `entity` (size `min(26, 12 + sqrt(degree)*3)`), `repo`, `missing`.
- Legend chips per card type present, plus Entity, Repo, Missing; toggling hides that group.
- Edges: doc→entity (`party`, from `doc_entities` main role only by default; "All mentions" toggle adds others), doc→doc edges (excluding `duplicate_of`), code file→code file `imports` only when "Show code" is on, repo→file hidden by default.
- Initial view: the 25 busiest entities and repos with their documents; search reveals any node.
- Data embedded as JSON with `</` escaped as `<\/`.

### 19.3 Review CSV

Columns: `file_id, root, path, card_type, category, title, status, main_entity, main_date_label, main_date, dates (label=date; ...), top facts (key=value; ... first 8), confidence, needs_review, review_reasons, duplicate_of, card type correct?, main entity correct?, main date correct?, notes`. UTF-8 with BOM.

### 19.4 Report

Port `report()`: files vs cards, counts by `card_type`/`category`/`status`, routing method counts, documents flagged for review (first 25), unresolved references (first 25), extraction errors not yet retried, top `suggested_type` among generic (hint to run `fg discover`), denied/redacted/large-file counts, tokens spent per model.

---

## 20. MCP server (`mcp_server.py`)

Using the official `mcp` Python SDK (`FastMCP`), stdio transport. Expose the agent tools of Section 15.2 as MCP tools with the same names, inputs and outputs, backed by a read-only connection, plus a resource `filegraph://schema` returning the database description used in the agent prompt. No write tools. Start with `fg mcp`.

README snippet for Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "filegraph": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/fg.py", "--db", "/absolute/path/to/filegraph.db", "mcp"]
    }
  }
}
```

---

## 21. README

Write a user guide covering: what FileGraph does (table of commands with an "AI?" column, like LegalGraph's README), setup on Windows/macOS/Linux (venv, `pip install -r requirements.txt`, optional extras, `fg init`), configuring roots and privacy policies, the **first run on a small test folder** (copy ~100 mixed files; `fg ingest`, `fg extract --limit 5`, `fg report`, `fg extract`, `fg review-csv` and check accuracy), example questions (Section 25.4), adding a card type by hand and via `fg discover`/`fg propose-card`, the vault and graph, filing and backtest, MCP setup, keeping it fresh, known limits (Section 26), and a privacy section stating exactly what leaves the machine.

---

## 22. Build phases and acceptance checks

Run `pytest -q` (with `FG_FAKE_LLM=1`) after every phase. Each phase lists what must work before moving on.

| Phase | Build | Acceptance |
|---|---|---|
| **P1 Skeleton** | `config.py`, `db.py`, `llm.py` (incl. fake LLM), `llm_instances.py`, `fg.py` with `init`, `roots` | `fg init` creates config files; `fg roots` lists roots from YAML; DB created with all tables; FTS5 check passes; `test_db.py` verifies schema and the `catalog` view |
| **P2 Ingest** | `walk.py`, `security.py`, `parse.py`, `ingest.py`, `inventory` | On the fixture tree: `node_modules/` and `.git/` ignored; `.env` and `id_rsa` denied (counted, not stored); fake secret in `config.py` fixture shows `[REDACTED]` in `pages`; email attachment stored as `mail.eml::contract.pdf`; duplicate detected; large file `skipped_large`; second `ingest` reports 0 new/changed; deleting a file marks it missing |
| **P3 Cards + routing + extraction** | `cards.py`, built-in YAMLs, `router.py`, `inputs.py`, `extract.py` | Registry loads and compiles tools; invalid YAML gives a clear error; routing: passport → `identity` via rules, contract → `legal`, unknown → `generic` via (fake) classification; extraction writes `documents`, `doc_dates`, `doc_facts`; duplicates get cards without extra LLM calls; re-run extracts nothing; version bump re-extracts only that card's documents; schema repair works on a malformed fake response |
| **P4 Code** | `codeparse.py`, `repos.py`, code card | Fixture repo detected with name and (if git present) commit; Python symbols with correct line ranges; `imports` resolved within the repo; code cards present with `confidence='high'`; `.md` inside the repo not LLM-extracted; repo summary stored (fake) and skipped on second run |
| **P5 Link** | `link.py` | Entities merged across spellings (`Acme Facility Services Inc.` = `ACME FACILITY SERVICES INC`); self entity never a main entity; `attachment_of`, `duplicate_of`, `imports` edges; amendment fixture linked to its MSA with `amends`; a reference to a missing agreement stored with `dst_id NULL` |
| **P6 Search** | `embed.py`, `search.py`, `fg search` | With embeddings disabled, FTS-only search returns the passport for `passport expiry`; with sentence-transformers installed, a paraphrase query (`travel document renewal`) also finds it; filters work |
| **P7 Agent** | `agent.py`, `fg ask/chat` | Each tool tested directly; `run_sql` rejects `DELETE` and multi-statements; `read_file` refuses paths outside roots and denied files; scripted fake agent run completes and returns text |
| **P8 Discover** | `discover.py`, `fg cards` | Fixture insurance policy initially `generic` with `suggested_type='insurance_policy'`; `fg discover --min 1` lists it; `fg propose-card insurance_policy` writes a valid YAML; after moving it to `cards/`, `fg extract --card insurance_policy --refresh` re-extracts exactly that document and its `card_type` changes |
| **P9 Exports** | `export.py`, `graph_template.py` | Vault notes for documents, entities, repos; `graph.html` opens and contains embedded JSON; review CSV has the listed columns; `fg report` runs |
| **P10 Filing** | `filing.py` | `fg file` on a new fixture invoice suggests the folder that holds other invoices from the same entity (fake `choose_folder` response); `--apply --yes` copies without overwriting and logs; `backtest -n 5` runs |
| **P11 MCP** | `mcp_server.py` | Server starts and lists the tools (test with the SDK's in-memory client if available, else skip when `mcp` is not installed) |
| **P12 Watch** (optional) | `watch.py` | A new file appears in the catalog after one cycle |

---

## 23. Error handling and graceful fallbacks

| Situation | Behaviour |
|---|---|
| Optional library missing (sentence-transformers, sqlite-vec, tree-sitter, mcp, watchdog) | Feature disabled with one clear message; core keeps working |
| `git`, `rg`, LibreOffice missing | Metadata left NULL / Python grep / `.doc` marked unsupported |
| Model lacks vision | Scanned pages omitted, review reason added |
| Provider rejects forced tool choice | Fallback chain of Section 5.1 |
| Malformed model output | Schema repair (Section 11.5) |
| Auth / unknown model | Stop the run, keep finished work, print the model key and which env var to check |
| Rate limit / 5xx | Backoff retries, then record in `extraction_errors` and continue |
| Corrupt or encrypted file | `parse_status='error'` with message; listed in inventory |
| Root path missing | Warning, root skipped; its files are not marked missing (prevents mass "deletion" when a drive is unplugged) |
| Database belongs to an older schema version | Clear message: rebuild with a new `--db` |
| Ctrl+C during extract | Finished results already committed; print how many were saved |

---

## 24. Porting map from LegalGraph

| LegalGraph | FileGraph | Change |
|---|---|---|
| `config.py` | `config.py` | `.env` + YAML; roots, policies, limits |
| `db.py` | `db.py` | New schema (Section 6); `root(conn)` → `root_path(conn, root_id)` |
| `parse.py` | `parse.py` | Same parsers + md/html/json; units with labels and lines; redaction |
| — | `codeparse.py`, `repos.py` | New |
| `ingest.py` | `walk.py`, `ingest.py` | Multiple roots, ignore/deny, kinds, repos, per-unit FTS |
| `inputs.py` | `inputs.py` | Same page plan; vision check; `record_card` closing line |
| `extract.py` (`TOOL`, `system_prompt`, `CATEGORIES`) | `cards/*.yaml` + `cards.py` | Schema becomes data; legal schema lives in `cards/legal.yaml` |
| `extract.py` (`extract_sync`, `materialize`, `_clean_date`, `_save`) | `extract.py` | Card-aware cache key, schema repair, side tables |
| batch API functions | — | Dropped (provider-dependent); keep the sync path |
| `link.py` (`norm_key`, `resolve_counterparties`, `_score`, `build_edges`) | `link.py` | Entities of all kinds; code edges; generalised scoring |
| `agent.py` | `agent.py` | More tools; new prompt; policy-aware model |
| — | `embed.py`, `search.py` | New (hybrid search) |
| `filing.py` | `filing.py` | Main entity instead of counterparty; root-aware |
| `export.py`, `graph_template.py` | same | Entities/repos, card-type colours |
| `llm.py`, `llm_instances.py` | same | Fallback tool forcing, fake LLM, policies, no secrets in code |
| `lg.py` | `fg.py` | New commands |

---

## 25. Testing

### 25.1 Fixtures (`tests/make_fixtures.py`)

Creates `tests/_tree/` (git-ignored) deterministically, plus `tests/_tree/filegraph.yaml` with three roots (`personal`, `legal` with `profile: auto`, `code`), `me: {names: [Ravi Sharma], organizations: [Harborline Events LLC]}` and `limits.max_file_mb: 1`:

```
personal/
  Scan_0042.pdf            # "REPUBLIC OF INDIA PASSPORT ... Passport No Z1234567 ... Surname SHARMA Given Names RAVI ... Nationality INDIAN ... Date of Birth ... Date of Issue 14/03/2019 ... Date of Expiry 13/03/2029"  -> identity by rules
  car/policy_2026.pdf      # "ICICI Lombard ... Policy No 3001/123456 ... Period of Insurance 01/04/2026 to 31/03/2027 ... Total Premium Rs. 14,200 ... Registration TS09 AB 1234"  -> generic via classification
  salary_slip_aug.pdf      # "Payslip for August 2026 ... Employer Northwind Ltd ... Gross Salary 1,20,000 ... Net Pay 98,500"  -> finance by rules
  notes.md
  budget.csv
  big.bin                  # > max_file_mb (set limit to 1 MB in test config)
  .env                     # FAKE_TOKEN=abc123  (must be denied)
legal/
  Acme MSA 2023.docx       # "Master Services Agreement ... WHEREAS ... effective as of March 1, 2023 ... between Harborline Events LLC and Acme Facility Services Inc. ... Governing Law ... IN WITNESS WHEREOF"  -> legal by rules
  Acme Amendment 1.docx    # "Amendment No. 1 to the Master Services Agreement dated March 1, 2023 between Harborline Events LLC and ACME FACILITY SERVICES INC ... WHEREAS ... effective June 1, 2026 ... IN WITNESS WHEREOF"  -> legal by rules
  Acme MSA 2023 copy.docx  # byte-identical duplicate
  mail/signed.eml          # email with the amendment PDF attached
code/
  demo_repo/               # git init if git exists, else mkdir .git
    README.md
    pyproject.toml
    app/__init__.py
    app/auth/session.py    # def login_user(...), def verify_token(...), class Session
    app/api.py             # from app.auth.session import login_user
    config.py              # API_KEY = "sk-test-0123456789abcdefghij"  (must be redacted)
    node_modules/x/index.js  # must be ignored
  scripts/
    tool.js                # export function signIn() {}
```

PDFs are generated with `pymupdf` (`page.insert_text`), DOCX with `python-docx`, EML with the `email` stdlib (attachment = a generated PDF).

### 25.2 Fake LLM responses (`tests/fake_responses.json`)

Provide entries for: classification of each document; `record_card` for passport (identity: `dates=[issued 2019-03-14, expires 2029-03-13]`, facts `passport_number`), policy (generic first with `suggested_type='insurance_policy'`, facts `policy_number`, `premium` 14200 INR, `vehicle_registration`; dates `start`, `expires`), payslip (finance), MSA and amendment (legal, amendment references the MSA with relation `amends` and date `2023-03-01`, plus a reference to a non-existent "Statement of Work No. 3"), `record_repo`, `draft_card` for `insurance_policy`, and `choose_folder`. Include one deliberately malformed `record_card` (unknown enum, missing `confidence`) to test schema repair.

### 25.3 Tests to write

`test_db.py, test_walk_security.py, test_parse.py, test_ingest.py, test_cards.py, test_router.py, test_extract.py, test_codeparse.py, test_link.py, test_search.py, test_agent_tools.py, test_discover.py, test_export.py, test_filing.py, test_cli_smoke.py` (runs the full `fg run --yes` on the fixture tree with the fake LLM and checks the example queries of Section 6.3 return the expected rows).

### 25.4 Manual acceptance on real files

With a real model configured, on a 100-file folder:

1. `fg ask "When does my passport expire?" -v` → answer cites the file; the trace shows `run_sql` on `doc_dates`.
2. `fg ask "Which contracts became effective in June 2026?" -v` → complete list via SQL, drafts and duplicates excluded and counted.
3. `fg ask "Where do I handle user login across my projects?" -v` → `find_symbol`/`search` results with paths and line numbers from more than one repo.
4. `fg ask "What are our current terms with Acme, taking amendments into account?"` → uses `get_related`.
5. `fg ask "List everything that expires or is due in the next 90 days."` → one SQL over `doc_dates`.
6. `fg discover` → shows plausible new types; `fg propose-card` produces a sensible YAML.

---

## 26. Known limits (state them in the README)

- Only exact duplicates are detected; a draft and the signed version are separate documents distinguished by `status`.
- Entity merging is name-based; a parent company and its subsidiary stay separate.
- Precise answers exist only for what cards extract (dates, facts, entities, fields); everything else is answered from search over text, which is not guaranteed complete.
- Not read in v1: old `.xls`, PowerPoint, password-protected PDFs, emails embedded inside emails, audio/video.
- Code understanding is structural (symbols, imports, summaries), not a full call graph.
- Single user, local. The SQLite catalog can later back a web UI or a shared server without schema changes.

---

## Appendix A. Prompts

Use these texts verbatim (placeholders in `{braces}`).

### A.1 Core extraction prompt (prefix of every card's system prompt)

```text
You catalogue documents from a person's computer so they can later be found and answered about precisely.
The user (never "the other party"): {me_names}. The user's own organizations: {me_orgs}.

Read the document content you are given and call record_card exactly once.

General
- The file path and folder names are hints only; the content decides.
- Use only what the document says. Never invent names, numbers or dates. If something is not in the document, leave it out.
- title: short and human, "<Who> - <What> (<year>)", e.g. "ICICI Lombard - Car insurance policy (2026)".
- summary: 2-3 plain sentences: what this is, who it involves, what it does or asks for.
- suggested_type: the most specific kind of document in snake_case (car_insurance_policy, salary_slip, rental_agreement, electricity_bill, passport, offer_letter). Always fill it, even when the card type already fits.

Entities
- List the people and organizations that matter (parties, issuer, sender, recipient, employer, provider). Full names as written.
- main_entity is the most important party that is NOT the user or the user's own organizations.

Dates
- Every date as ISO YYYY-MM-DD. If only month and year are given, use YYYY-MM and it will be flagged.
- Give each date the best label from the allowed list; use "other" with label_detail when none fits.
- basis: "stated" when the document states the date for that purpose; "signature" when it comes from a signature or stamp; "inferred" when you derived it (say how in review_reasons).
- quote: the exact words the date comes from (max 300 chars), and the page/section number from the "=== Page N ===" or "=== Section N ===" markers. Handwritten or stamped dates on scanned pages count.

Facts
- Record the specific values a person would look up later: numbers, amounts, identifiers, names of plans or products, addresses.
- Use the preferred keys when they fit; otherwise a short snake_case key.
- Amounts: value as written, number as a plain number, unit as an ISO currency code.
- Payment card and bank account numbers: record only the last 4 digits, e.g. "XXXX1234".

References
- List other documents this one points to (amends, renews, replies to, pays, is issued under...), with the referenced document's title and date if stated, and the exact quote.

Quality
- confidence: high only when the key fields are clearly stated; low when the content is partial, illegible or ambiguous.
- review_reasons: anything a careful person should double-check. Empty when nothing.
- Keep quotes verbatim and short.
```

### A.2 Agent system prompt

```text
You answer questions about the user's own files using the tools provided. Today's date: {today}.
The user: {me_names}. The user's own organizations: {me_orgs}.
Roots (top-level folders): {roots_with_policies}.
Card types: {card_list_one_line_each}.

Database (SQLite, read-only):
- View `catalog`, one row per analysed file: file_id, root, path, folder, filename, ext, kind (document|email|data|image|code),
  source ('disk' or 'attachment' = came out of an email), parent_id, repo_id, language, card_type, category, title, summary,
  status, main_entity, main_entity_id, main_date, main_date_label, confidence (high|medium|low), needs_review (0/1),
  review_reasons (JSON), suggested_type, extras (JSON of card-specific fields; use json_extract(extras,'$.field')),
  duplicate_of (file_id of an identical earlier copy, else NULL).
- `doc_dates`(file_id, label, label_detail, date, basis, quote, page): every labelled date. Dates are 'YYYY-MM-DD' strings.
- `doc_facts`(file_id, key, value_text, value_num, unit, quote, page): looked-up values such as policy_number, premium, amount.
- `entities`(id, kind, name, aliases JSON, is_self) and `doc_entities`(file_id, entity_id, role, raw_name).
- `edges`(src_id, dst_id NULL = referenced document not found, edge_type, method, confidence, evidence, ref JSON).
- `repos`(id, name, rel_path, remote_url, branch, head_commit, last_commit_at, languages JSON, summary, summary_json).
- `code_symbols`(file_id, repo_id, name, qualname, kind, start_line, end_line, signature, docstring).
- `files`(id, root_id, rel_path, kind, size, mtime, parse_status, ...) for files without cards.
Call describe_cards to see which date labels, fact keys and fields actually exist before filtering on them.

How to answer
- Lists, counts, totals, filters and anything about dates or amounts: ALWAYS use run_sql. SQL over the tables is complete; search is not.
- Exclude duplicate_of IS NOT NULL unless asked, and say how many duplicates you excluded.
- Match names with LIKE on entities.name and entities.aliases, then join doc_entities.
- Wording, topics or "find the document about...": use search, then read_document to confirm.
- Code questions: use find_symbol and search with kind='code'; use read_file for exact lines and grep for anything that may have changed since indexing. Report paths with line numbers.
- Relationship questions (what amends what, current terms, what is connected): use get_related.
- When a date's basis is not 'stated', or needs_review=1, or confidence is low, say so for those rows. Mention rows with missing dates that might also qualify.
- Never invent files, values or facts. Cite file paths for every claim. If the index has nothing relevant, say exactly: "I couldn't find this in your indexed files." and suggest what to check.
- Keep answers compact. Use a table for lists of documents.
```

### A.3 Filing prompt (`choose_folder`)

```text
You file documents into the user's folders. The folder tree grew organically, so conventions differ between branches.
Decide where a new document belongs by:
1. Keeping related documents together: if the document amends, renews, replies to, or is issued under something already filed, prefer that document's folder (or its sibling subfolder that holds such documents, if that is the convention there).
2. Otherwise follow where documents with the same main entity and of the same kind already live.
3. Follow the local conventions visible in the folder profiles and sibling folders (year subfolders, per-entity folders, naming style).
4. Propose a NEW folder only if no existing folder fits; make it consistent with its would-be siblings.
Use folder paths exactly as written. Be honest about confidence: below 0.6 means a human should decide.
```

Tool `choose_folder`: `folder` (string), `is_new_folder` (boolean), `confidence` (0-1), `reasoning` (1-2 sentences), `alternatives` (≤ 3 existing folders). All required.

### A.4 Classification prompt (`classify_document`)

```text
Pick the card type that best fits this file. Card types:
{for each LLM card: "- <name>: <description>"}
- generic: anything else.
Choose generic when unsure. Also give suggested_type: the most specific kind of document in snake_case.
Use only the content shown. Call classify_document exactly once.
```

### A.5 Repo summary prompt (`record_repo`)

```text
You summarise a source code repository for its owner, who will later ask questions like "which project handles payments"
or "how do I run this". Use only the README, manifests, directory tree and symbol list provided. Name real paths.
If something is unclear (for example how to run it), say "not stated" rather than guessing. Call record_repo exactly once.
```

### A.6 Card drafting prompt (`draft_card`)

```text
You design a card template for one kind of document so its key information can be extracted consistently.
You are given extracted data from {n} example documents that were all identified as "{suggested_type}".
Propose: a snake_case name, a one-line description, 4-10 categories, statuses, the main entity role and kind, the main
date label, date labels, entity roles, preferred fact keys (snake_case, those that recur across examples), aliases that map
variant keys seen in the examples to the preferred ones, up to 8 typed fields only for values that are always present,
allowed relations, short extraction instructions, an absorbs list (include "{suggested_type}" and close synonyms seen),
and match rules (filename regex and 3-8 distinctive keywords). Prefer fewer, well-defined fields over many vague ones.
Call draft_card exactly once.
```

---

## Appendix B. Built-in card files

### B.1 `cards/generic.yaml`

```yaml
name: generic
version: 1
description: Any document that does not fit a more specific card.
llm: true
priority: 0
absorbs: []
categories: [finance, medical, identity, legal, work, education, travel, housing, vehicle, insurance, tax, personal,
             correspondence, receipt_or_invoice, manual_or_reference, notes, data_or_spreadsheet, image_or_photo, other]
statuses: [final, draft, signed, unsigned, not_applicable, unknown]
main_entity: {role: counterparty, kind: organization}
main_date: [expires, due, effective, start, issued, event, signed]
date_labels: [issued, effective, start, end, expires, due, paid, renewal, signed, event, birth]
entity_roles: [sender, recipient, issuer, author, counterparty, holder, provider, customer, employer, employee,
               landlord, tenant, insurer, bank, doctor, patient, other]
preferred_fact_keys: [amount, total_amount, currency, reference_number, invoice_number, order_number, account_number,
                      policy_number, plan_name, address, phone, email, duration, quantity]
fact_key_aliases:
  total: total_amount
  grand_total: total_amount
  ref_no: reference_number
  reference_no: reference_number
  invoice_no: invoice_number
  policy_no: policy_number
fields: {}
relations: [replies_to, pays, amends, renews_or_extends, terminates, references]
instructions: |
  Pick the closest broad category. Use suggested_type to say precisely what the document is.
review_rules:
  required_dates: []
  flag_values: []
```

### B.2 `cards/identity.yaml`

```yaml
name: identity
version: 1
description: Identity and civil documents - passports, national ID cards, driving licences, visas, residence permits, birth/marriage certificates, vehicle registration.
llm: true
priority: 40
absorbs: [passport, national_id, aadhaar_card, pan_card, driving_licence, driving_license, visa, residence_permit,
          birth_certificate, marriage_certificate, vehicle_registration_certificate]
match:
  path_globs: ["**/ID/**", "**/Identity/**", "**/Passport*/**"]
  filename_regex: "(?i)passport|aadhaa?r|\\bpan\\b|licen[cs]e|visa|birth.?cert|marriage.?cert|\\brc\\b"
  keywords_any: ["passport", "date of expiry", "date of birth", "nationality", "driving licence", "visa", "permanent account number", "unique identification"]
  min_keyword_hits: 2
categories: [passport, national_id, tax_id, driving_licence, visa, residence_permit, birth_certificate,
             marriage_certificate, vehicle_registration, other]
statuses: [valid, expired, application, copy, unknown]
main_entity: {role: issuer, kind: organization}
main_date: expires
date_labels: [issued, expires, valid_from, birth]
entity_roles: [holder, issuer, other]
preferred_fact_keys: [document_number, holder_name, nationality, place_of_issue, place_of_birth, vehicle_registration, visa_type, entries]
fact_key_aliases:
  passport_number: document_number
  passport_no: document_number
  licence_number: document_number
  license_number: document_number
fields:
  holder_name: {type: text, description: Full name of the holder as printed}
instructions: |
  The holder is a person (often the user); the issuer is the authority. Status "expired" only if the expiry date is before today's date shown in the header; otherwise "valid" when an expiry exists.
review_rules:
  required_dates:
    - {label: expires, unless_status: [application], for_categories: [passport, driving_licence, visa, residence_permit]}
  flag_values: []
```

The header built in Section 11.3 must therefore include `Today: YYYY-MM-DD`.

### B.3 `cards/finance.yaml`

```yaml
name: finance
version: 1
description: Money documents - bank and card statements, payslips, invoices, bills, receipts, tax returns and notices, loan and investment statements.
llm: true
priority: 30
absorbs: [bank_statement, credit_card_statement, salary_slip, payslip, invoice, utility_bill, electricity_bill,
          receipt, tax_return, income_tax_return, form_16, loan_statement, investment_statement]
match:
  filename_regex: "(?i)statement|payslip|salary|invoice|bill|receipt|itr|form.?16|tax"
  keywords_any: ["statement period", "opening balance", "closing balance", "net pay", "gross salary", "invoice number",
                 "amount due", "due date", "tax invoice", "gstin", "assessment year"]
  min_keyword_hits: 2
categories: [bank_statement, credit_card_statement, payslip, invoice, utility_bill, receipt, tax_return, tax_notice,
             loan, investment_statement, other]
statuses: [paid, unpaid, final, draft, not_applicable, unknown]
main_entity: {role: provider, kind: organization}
main_date: [due, period_end, issued, paid]
date_labels: [issued, due, paid, period_start, period_end]
entity_roles: [provider, bank, employer, employee, customer, payer, payee, tax_authority, other]
preferred_fact_keys: [total_amount, amount_due, net_pay, gross_pay, opening_balance, closing_balance, tax_amount,
                      invoice_number, account_number, gstin, pan, assessment_year]
fact_key_aliases:
  net_salary: net_pay
  take_home: net_pay
  gross_salary: gross_pay
  balance_due: amount_due
fields: {}
relations: [pays, references]
instructions: |
  For statements, period_start and period_end are the statement period. For bills and invoices, record amount_due and the due date.
review_rules:
  required_dates:
    - {label: due, unless_status: [paid, not_applicable], for_categories: [invoice, utility_bill]}
  flag_values: []
```

### B.4 `cards/legal.yaml` (port of LegalGraph)

```yaml
name: legal
version: 1
description: Contracts and legal documents - agreements, amendments, SOWs, order forms, NDAs, side letters, renewals, notices, guarantees, LOIs, powers of attorney, corporate records.
llm: true
priority: 60
absorbs: [contract, agreement, master_services_agreement, amendment, statement_of_work, order_form, nda,
          non_disclosure_agreement, side_letter, lease, rental_agreement, letter_of_intent, power_of_attorney]
match:
  path_globs: ["**/Legal/**", "**/Contracts/**", "**/Agreements/**"]
  keywords_any: ["agreement", "amendment", "statement of work", "non-disclosure", "whereas", "in witness whereof",
                 "governing law", "effective date", "hereinafter"]
  min_keyword_hits: 3
categories: [contract, amendment, statement_of_work, order_form, nda, side_letter, renewal_or_extension,
             termination_or_notice, guarantee_or_security, letter_of_intent, power_of_attorney, corporate_record,
             correspondence, invoice_or_financial, schedule_or_exhibit, tracker_or_register, other]
statuses: [executed, partially_executed, unsigned_final, draft, redline, not_applicable, unknown]
main_entity: {role: counterparty, kind: organization}
main_date: effective
date_labels: [effective, execution, expiration, renewal_notice, termination]
entity_roles: [counterparty, our_entity, guarantor, signatory, other]
preferred_fact_keys: [contract_value, notice_period, payment_terms, term_length]
fact_key_aliases: {}
fields:
  doc_type: {type: text, description: "Specific type as written, e.g. 'Master Services Agreement', 'Amendment No. 2', 'Mutual NDA', 'Email'"}
  our_entity: {type: text, description: Which of the user's own organizations is a party}
  effective_date_basis:
    type: enum
    values: [stated, date_of_last_signature, date_of_first_signature, signature_date_only, retroactive,
             by_reference_to_other_agreement, inferred_from_other_dates, not_found]
  term_summary: {type: text, description: One line on term and renewal}
  auto_renewal: {type: boolean}
  governing_law: {type: text}
  contract_value: {type: amount, description: Headline value with currency, as written}
relations: [amends, amends_and_restates, supersedes, issued_under, side_letter_to, guarantees, terminates,
            renews_or_extends, assigns, references]
instructions: |
  The counterparty is the main party that is NOT one of the user's organizations.
  Effective date (label "effective") = when the document's obligations take effect, as the document defines it:
  * "effective as of June 1, 2026" -> that date (effective_date_basis "stated"), even if signatures are later.
  * A start date earlier than the signatures -> effective_date_basis "retroactive".
  * "effective on the date of the last signature below" -> latest signature date ("date_of_last_signature").
  * No effective-date clause but dated signatures -> latest signature date, "signature_date_only", add a review reason.
  * Amendments, SOWs and order forms have their OWN effective date - never copy the parent agreement's date.
  * Not determinable -> no "effective" date and effective_date_basis "not_found".
  Also record "execution" (latest signature) and "expiration" dates when present.
  Status:
  - executed: all parties have signed (signatures, /s/, e-signature certificates, or clear statements of execution).
  - partially_executed: some but not all signature blocks completed. unsigned_final: clean version with empty signature blocks.
  - draft or redline: DRAFT markers, tracked changes, comments, bracketed blanks. not_applicable: emails, trackers, invoices.
  References: list every other agreement this document amends, restates, supersedes, is issued under (SOWs, order forms,
  purchase orders under a master agreement), is a side letter to, guarantees, terminates, renews or assigns. Use
  "references" for other mentions that matter. Include the referenced agreement's date if stated. For an email,
  reference the agreements it transmits or discusses.
  Spreadsheets: say what the sheet tracks (category tracker_or_register, schedule_or_exhibit or invoice_or_financial).
review_rules:
  required_dates:
    - label: effective
      unless_status: [draft, redline]
      for_categories: [contract, amendment, statement_of_work, order_form, nda, side_letter, renewal_or_extension,
                       guarantee_or_security, letter_of_intent]
  flag_values:
    - {field: effective_date_basis, values: [signature_date_only, inferred_from_other_dates, by_reference_to_other_agreement],
       reason: "effective date not stated directly"}
```

With this card, the LegalGraph question "Which contracts became effective in June 2026?" is answered by the SQL in Section 6.3.

### B.5 `cards/code.yaml`

```yaml
name: code
version: 1
description: Source code and project files inside repositories (deterministic, no LLM per file).
llm: false
priority: 100
absorbs: []
categories: []        # category is the language
statuses: [unknown]
main_entity: null
main_date: null
date_labels: []
entity_roles: []
preferred_fact_keys: []
fact_key_aliases: {}
fields: {}
relations: [imports]
instructions: ""
review_rules: {required_dates: [], flag_values: []}
```

---

## Appendix C. Definition of done

The build is complete when:

1. All phases P1–P11 pass their acceptance checks and `pytest -q` passes offline with `FG_FAKE_LLM=1`.
2. `fg run --yes` on the fixture tree finishes without errors and the five SQL queries of Section 6.3 return the expected fixture rows.
3. No file in the repository contains a secret; `.env`, `*.db` and generated outputs are git-ignored.
4. A new card YAML dropped into `cards/` is picked up with no code or schema change, and `fg extract --card <name> --refresh` re-extracts only the affected documents.
5. The README explains setup, privacy policies and every command, and `DECISIONS.md` lists every choice made where this spec was silent.

