# CodePlane — Unified System Specification

## 1. Problem Statement

Modern AI coding agents are not limited by reasoning ability. They are limited by **how they interact with repositories**.

Dominant sources of friction:

- Exploratory thrash  
  Agents build a mental model of a repo via repeated grep, file opens, and retries.

- Terminal mediation  
  Trivial deterministic actions (git status, diff, run test, cat file) are executed through terminals, producing unstructured text, retries, hangs, and loops.

- Editor state mismatch  
  IDE buffers, file watchers, and undo/keep UX drift from on-disk and Git truth.

- Missing deterministic refactors  
  Renames and moves that IDEs do in seconds take agents minutes via search-and-edit loops.

- No convergence control  
  Agents repeat identical failure modes with no enforced strategy changes or iteration caps.

- Wasteful context acquisition  
  Agents repeatedly ask for information that is already computable.

Result: **Small fixes take 5–10 minutes instead of seconds**, due to orchestration and I/O inefficiency, not model capability.

---

## 2. Core Idea

Introduce a **local repository control plane** that sits beneath agents and turns a repository into a **deterministic, queryable system**.

Key reframing:

- Agents plan and decide.
- CodePlane executes deterministically.
- Anything deterministic is computed once, not reasoned about repeatedly.
- Every state mutation returns complete structured context in a single call.

This replaces:

- grep + terminal + retries

with:

- indexed query → structured result → next action

---

## 3. Explicit Non-Goals

CodePlane is **not**:

- a chatbot
- an agent
- a semantic reasoning engine
- embedding-first
- a Git or IDE replacement
- an orchestrator

CodePlane does not plan, retry, or decide strategies. Its role is deterministic execution and deterministic context.

---

## 4. Architecture Overview

### 4.1 Components

- **CodePlane daemon (Python)**
  - Maintains deterministic indexes.
  - Owns file, Git, test, and refactor operations.
  - Exposes endpoints.

- **Agent client**
  - Copilot, Claude Code, Cursor, Continue, etc.
  - For operations CodePlane covers, agents should prefer CodePlane tools over direct file edits or shell commands.
  - Agents will still use terminals and other tools for tasks outside CodePlane's scope.

- **Git**
  - Authoritative history and audit layer.
  - Primary signal for detecting external mutations on tracked files.

Operational viewpoint:

- VS Code is a viewer, not a state manager.

### 4.2 CLI, Daemon Lifecycle, and Operability

CodePlane uses a single operator CLI: `cpl`. It is explicitly **not agent-facing**.

Core commands (idempotent; human output derivable from structured JSON via `--json`):

| Command | Description |
|---|---|
| `cpl init` | One-time repo setup: write `.codeplane/`, generate `.cplignore`, bind repo ID, build first index (or schedule immediately). |
| `cpl up` | Start the repo's daemon if not running. Idempotent, safe to run repeatedly. |
| `cpl down` | Gracefully stop the repo's daemon. Useful for upgrades, debugging, releasing locks. |
| `cpl status` | Single human-readable view: daemon running, repo fingerprint, index version, last reconcile, last error. |
| `cpl doctor` | Single "tell me what's wrong and how to fix it" command. Output suitable for pasting into issues. |

Humans learn: `init` once, then `up/status/doctor`.

### Folded and Removed Commands

The following capabilities are folded into core commands or removed to avoid surface area bloat:

| Capability | Disposition |
|---|---|
| Logs | Folded into `status --verbose` (last N log lines) and `doctor --logs` (bundled diag report). Optional alias `cpl logs` → `cpl status --follow` is acceptable but not a stable interface. |
| Inspect | Folded into `status --json` for machine-readable introspection. |
| Config CLI | Removed in v1. Use files: global config in user dir, repo config in `.codeplane/config`. One-off overrides via `cpl up --set key=value` if needed. |
| Rebuild index | Automatic when integrity checks fail; no manual trigger. |

Daemon model:

- **Repo-scoped daemon** — one daemon per repository; no multi-repo mode.
- `cpl up` in a repo directory starts/ensures a daemon for that repo only.
- Transport: **HTTP localhost** with ephemeral port.
  - Cross-platform with identical code (no socket vs named pipe divergence).
  - MCP clients can connect directly via HTTP/SSE transport (no stdio proxy needed).
- Request validation:
  - All HTTP requests must include `X-CodePlane-Repo: <absolute-path>` header.
  - Daemon validates header matches its configured repository root.
  - Missing header → `400` with error code `REPO_HEADER_MISSING`.
  - Path mismatch → `400` with error code `REPO_MISMATCH` (response includes expected/received paths).
  - Rationale: Prevents cross-repo accidents when multiple CodePlane instances run simultaneously. No token management, no file permissions, no auth state.
- Isolation rationale:
  - Failure in one repo cannot affect another.
  - Version skew between repos is not a problem.
  - CI and local dev work identically.
  - Aligns with spec's determinism-first philosophy.

Repo activation:

- `cpl up` initializes repo if needed (creates `.codeplane/`, repo UUID, config).
- Index is eagerly built on startup and continuously maintained.

Auto-start options (optional):

- Manual: `cpl up` (recommended; explicit is better)

Daemon startup includes:

- Git HEAD verification
- Overlay index diff
- Index consistency check

Daemon shutdown:

- Graceful shutdown timeout: 5 seconds (configurable)
- In-flight HTTP requests: allowed to complete until timeout, then aborted
- Active refactor/mutation operations: 
  - If in planning phase → abort immediately (no side effects)
  - If in apply phase → complete current file, abort remainder, rollback partial batch
- Connected SSE clients: receive `shutdown` event, then disconnect
- Flushes writes
- Releases locks

Logging:

- Multi-output support: logs can be sent to multiple destinations simultaneously
- Each output specifies format, destination, and optional level override
- Formats: `json` (structured JSON lines) or `console` (human-readable)
- Destinations: `stderr`, `stdout`, or absolute file path
- Default: single console output to stderr at INFO level
- Levels: `debug`, `info`, `warn`, `error`
- Required fields per JSON entry:
  - `ts`: ISO 8601 timestamp with milliseconds
  - `level`: log level
  - `event`: human-readable message
- Optional correlation fields:
  - `request_id`: request correlation identifier
  - `op_id`: operation identifier (for tracing a single request)
  - `task_id`: task envelope identifier
- Configuration example:
  ```yaml
  logging:
    level: DEBUG
    outputs:
      - format: console
        destination: stderr
        level: INFO        # Show INFO+ on console
      - format: json
        destination: /var/log/codeplane.jsonl
                            # Inherits DEBUG from parent
  ```
- JSON output example:
  ```json
  {"ts":"2026-01-26T15:30:00.123Z","level":"info","event":"daemon started","port":54321}
  {"ts":"2026-01-26T15:30:01.456Z","level":"debug","op_id":"abc123","event":"refactor planning started","symbol":"MyClass"}
  {"ts":"2026-01-26T15:30:02.789Z","level":"error","op_id":"abc123","event":"indexer timeout","lang":"java","timeout_ms":30000}
  ```
- Access via CLI:
  - `cpl status --verbose`: last 50 lines
  - `cpl status --follow`: tail -f equivalent
  - `cpl doctor --logs`: full log bundle for diagnostics

Installation and upgrades:

- Install modes (user-level only; no root/system install):
  - `pipx install codeplane`
  - Static binary from GitHub Releases
- Upgrades via package manager (pip/uv)

Diagnostics and introspection:

- `cpl doctor` checks:
  - Daemon reachable
  - Index integrity
  - Commit hash matches Git HEAD
  - Config sanity
- `cpl doctor --logs`: bundled diagnostic report including recent logs
- Runtime introspection:
  - `cpl status --verbose`: includes last N log lines and paths
  - `cpl status --json`: machine-readable index metadata (paths, size, commit, overlay state)
  - `cpl status --follow`: optional alias for tailing logs (not a stable interface)
  - Healthcheck endpoint exists (`/health`) returning JSON (interface details deferred)

Config precedence:

1. One-off overrides via `cpl up --set key=value` / env vars
2. Per-repo: `.codeplane/config.yaml`
3. Global: `~/.config/codeplane/config.yaml`
4. Built-in defaults

Environment variables use `CODEPLANE__` prefix with double underscore delimiter for nesting:
- `CODEPLANE__LOGGING__LEVEL=DEBUG`
- `CODEPLANE__DAEMON__PORT=8080`

No dedicated config CLI in v1. Edit files directly.

Error response schema:

All API errors return a consistent JSON structure:

```json
{
  "code": 4001,
  "error": "INDEX_CORRUPT",
  "message": "Index checksum mismatch; rebuild required",
  "retryable": false,
  "details": {
    "expected_hash": "abc123",
    "actual_hash": "def456"
  }
}
```

Fields:
- `code`: Numeric error code (for programmatic handling)
- `error`: String error identifier (for logging and display)
- `message`: Human-readable description
- `retryable`: Boolean hint — `true` if retry may succeed without intervention
- `details`: Optional object with error-specific context

Defaults prevent footguns:

- `.cplignore` auto-generated
- Dangerous paths excluded
- Overlay disabled by default in CI

Failure recovery playbooks:

| Failure | Detection | Recovery Command |
|---|---|---|
| Corrupt index | `cpl doctor` fails hash check | Automatic rebuild (or `cpl debug index-rebuild`) |
| Schema mismatch | Startup error | Automatic rebuild on `cpl up` |
| Stale revision | `cpl status` shows mismatch | Automatic re-fetch/rebuild on `cpl up` |
| Daemon crash | Daemon auto-exits | `cpl up` (restarts daemon) |

Platform constraints:

- Transport:
  - HTTP localhost on all platforms (identical implementation)
  - No platform-specific IPC code
- Filesystem:
  - No background watchers (see reconciliation)
  - Hash-based change detection
- Locking:
  - Uses `portalocker` cross-platform
  - CRLF normalized internally
- Path casing:
  - Canonical casing tracked on Windows
  - Case sensitivity honored on Linux

### 4.3 Terminology Note: “Always-on” vs Operated Lifecycle

One source uses “local, always-on control plane” as conceptual framing; the operability spec defines explicit start/stop.

Unified operational interpretation:

- CodePlane is **conceptually** a “control plane beneath agents.”
- It is **operationally** a repo-scoped daemon managed via `cpl up` / `cpl down`.

---

## 5. Repository Truth & Reconciliation (No Watchers)

### 5.1 Design Goals

- Correctly reflect repository state on disk, even across external edits.
- Never mutate Git state unless explicitly triggered by a CodePlane operation.
- Cheap, deterministic reconciliation before/after every CodePlane operation.
- No reliance on OS watchers (watchers optional and narrow at most).
- Works across:
  1. Git-tracked files
  2. Git-ignored but CPL-tracked files
  3. CPL-ignored files

### 5.2 Canonical Repo State Version

Authoritative repo version is:

```
RepoVersion = (HEAD SHA, .git/index stat metadata, submodule SHAs)
```

- `HEAD SHA`: `git rev-parse HEAD` or libgit2 equivalent.
- `.git/index`: compare mtime + size (no need to read contents).
- Submodules: treat each submodule as its own repo, include its HEAD SHA.

### 5.3 File Type Classification

| Type | Defined By | Tracked In | Checked During Reconcile? | Indexed? |
|---|---|---|---|---|
| 1. Git-tracked | Git | Git index | Yes (stat + hash fallback) | Yes (shared and local) |
| 2. CPL-tracked (Git-ignored) | `.cplignore` opt-in via negation patterns (e.g., `!.env.local`) | CPL overlay index | Yes (stat + hash) | Yes (local only) |
| 3. Ignored | `.cplignore` hard-excluded | None | No | No |

**Note:** To include a Git-ignored file in the local overlay index, add a negation pattern to `.cplignore`. For example, `!.env.local` will opt that file into CPL tracking even though it's Git-ignored.

### 5.4 Change Detection Strategy

Git-tracked files use Git-style status logic:

1. Load Git index entries.
2. For each tracked file:
   - `stat()` compare to cached metadata (mtime, size, inode).
   - If metadata differs → hash file content and compare to index SHA.
   - If confirmed changed → reindex file and invalidate relevant caches.

CPL-tracked files (not in Git):

- Maintain internal CPL index entries.
- Compare stat against cached metadata.
- If metadata differs → hash file content to confirm.
- Reindex only changed files.

### 5.5 Reconciliation Triggers

Reconciliation occurs:

- On daemon start
- Before and after every operation that reads or mutates repo state
- After agent-initiated file or Git ops (rename, commit, rebase, etc.)

### 5.6 Rename and Move Detection

- Detect delete+create pairs with identical hash → infer rename.
- Optional: Git-style similarity diff for small content changes.
- Default: treat as unlink + create unless hash match.

### 5.7 CRLF, Symlinks, Submodules

- CRLF: normalize line endings during hashing; avoid false dirty.
- Symlinks: treat as normal files; do not follow. Git tracks symlink targets as content blobs.
- Submodules:
  - Track submodule HEADs independently.
  - Reindex on submodule HEAD or path change.
  - Never recurse unless submodule is initialized.

### 5.8 Corruption and Recovery

- CodePlane never mutates `.git/index`, working tree, or HEAD.
- On Git metadata corruption: fail with clear message; don’t auto-repair.
- On CPL index corruption: wipe and reindex from Git + disk.

### 5.9 Reconcile Algorithm (Pseudocode)

```python
def reconcile(repo):
    head_sha = get_head_sha()
    index_stat = stat('.git/index')

    if (head_sha, index_stat) != repo.last_seen_version:
        repo.invalidate_caches()

    changed_files = []

    # 1. Git-tracked
    for path, entry in git_index.entries():
        fs_meta = stat(path)
        if fs_meta != entry.stat:
            if sha(path) != entry.hash:
                changed_files.append(path)

    # 2. CPL-tracked untracked files
    for path, entry in cpl_overlay.entries():
        fs_meta = stat(path)
        if fs_meta != entry.stat:
            if sha(path) != entry.hash:
                changed_files.append(path)

    # 3. Rename detection
    deleted = repo.files_missing()
    added = repo.files_added()
    for a in deleted:
        for b in added:
            if repo.cached_hash(a) == sha(b):  # use cached hash for deleted file
                repo.mark_rename(a, b)

    # 4. Reindex changed files
    for f in changed_files:
        repo.reindex(f)

    repo.last_seen_version = (head_sha, index_stat)
```

### 5.10 Reconciliation Invariants

- All mutations are operation-initiated.
- No daemon background threads mutate repo state.
- Reconcile logic is stateless, deterministic, idempotent.
- Git is the sole truth for tracked file identity and content.
- CPL index is derived from disk + Git, never canonical.

---

## 6. Ignore Rules, Two-Tier Index Model, and Security Posture

### 6.1 Security Guarantees

- No shared artifact includes secrets. Only Git-tracked files are eligible.
- Local overlay index may include sensitive files, but it is never uploaded/shared/in CI artifacts.
- All indexing and mutation actions are scoped, audited, deterministic.
- Reconciliation is stateless and pull-based; no background mutation.

### 6.2 Threat Assumptions

- Runs under trusted OS user account.
- Does not defend against compromised OS or user session.
- Assumes Git is canonical truth for tracked file truth.

### 6.3 `.cplignore` Role and Semantics

`.cplignore` is a superset of `.gitignore` and defines what CodePlane never indexes.

Security-focused posture defines `.cplignore` defaults that block secrets and noise. See defaults below.

### 6.4 Indexing Model (Security View)

| Tier | Contents | Shared? | Indexed? | Example Files |
|---|---|---:|---:|---|
| Git-tracked | Tracked source files | Yes | Yes | `src/main.py` |
| CPL overlay | Git-ignored but whitelisted | No | Yes | `.env.local` |
| Ignored (CPL) | Blocked via `.cplignore` | No | No | `secrets/`, `*.pem` |

Shared artifact = Git-tracked only. Overlay = local-only. Ignored = excluded.

### 6.5 Shared Artifact Safety

- Inclusion rule: Only files explicitly tracked by Git are considered.
- Build rule: CI artifact construction begins from a clean Git clone.
- Validation rule: Enterprises can hash-check artifacts and run secret scanners.

### 6.6 `.gitignore` Defaults (Security-Relevant)

Recommended baseline:

```
# Secrets and tokens
.env
*.pem
*.key
*.p12
*.crt
*.aws

# Build and runtime artifacts
node_modules/
dist/
build/
.venv/
__pycache__/
*.pyc

# IDE and OS junk
.vscode/
.idea/
.DS_Store
*.log
*.lock
```

### 6.7 `.cplignore` Defaults (Security + Efficiency)

Superset ignore file blocks noisy, unsafe, irrelevant paths:

```
# Always ignored for indexing
.env
*.pem
*.key
*.p12
*.crt
*.aws
node_modules/
dist/
build/
.venv/
__pycache__/
*.pyc
*.log
coverage/
pytest_cache/
```

These files are never indexed even locally.

### 6.8 Failure Modes and Protections

| Misconfig | Result | Mitigation |
|---|---|---|
| Secrets committed to Git | Artifact leaks secret | Prevent via pre-commit hooks, Git scanning |
| Missing `.cplignore` | Sensitive files indexed locally | Defaults applied automatically |
| Lax Git hygiene | Build includes unintended files | Clean clone + hash match required |

### 6.9 Security-Auditability Notes

- All mutations emit structured deltas.
- Index is deterministic and reproducible.
- Operation history is append-only (SQLite-backed).
- No automatic retries or implicit mutations.

### 6.10 `.env` Overlay Indexing: Resolved

One source explicitly stated local overlay may include `.env` and local config.
Another source's `.cplignore` default explicitly blocks `.env` from indexing even locally.

**Resolution:** Default-blocked in `.cplignore` for security. Users who need `.env` indexed locally can explicitly whitelist via negation pattern (e.g., `!.env` or `!.env.local`) in `.cplignore`. This preserves security-first defaults while allowing explicit opt-in.

---

## 7. Indexing & Retrieval Architecture (Syntactic + Semantic, Two-Layer)

### 7.1 Overview

CodePlane builds a deterministic, incrementally updated two-layer index:

**Syntactic Layer (Always-On):**
- Fast lexical search engine (Tantivy) for identifiers, paths, tokens
- Structural metadata store (SQLite)
- Dependency and symbol graph for bounded, explainable expansions
- Tree-sitter parsing (~15 bundled grammars)

**Semantic Layer (Batch SCIP Indexers):**
- One-shot SCIP indexers per language (scip-go, scip-typescript, rust-analyzer, etc.)
- Precise cross-file references, type hierarchies, and symbol resolution
- No persistent language servers — indexers run to completion and terminate
- File state tracking (CLEAN/DIRTY/STALE/PENDING_CHECK)

Indexing scope:
- Git-tracked files (primary)
- CPL-tracked files (local overlay, never shared)
- CPL-ignored files excluded

No embeddings required. Normal load target is <1s response for syntactic queries.

### 7.2 Lexical Index

- Engine: Tantivy via PyO3 bindings
- Scope: paths, identifiers, docstrings optional
- Update model: immutable segment + delete+add on change
- Indexing throughput: 5k–50k docs/sec depending on hardware
- Query latency: <10ms warm cache for top-K
- Incremental updates based on Git blob hash and file content hash diff
- Atomicity: build in temp dir and swap in (`os.replace()`)

### 7.3 Structural Metadata

- Store: SQLite, single-file, ACID, WAL mode
- Schema includes:
  - `chunk_registry`: file/chunk id, blob hash, spans
  - `symbols`: name, kind, location, language
  - `relations`: edges between symbols (calls, imports, contains, inherits)
- Concurrency:
  - Readers non-blocking
  - Writer blocked only during batch update (~10–100ms)
- Consistency: metadata update transactionally coupled to index revision swap

### 7.4 Parser (Tree-sitter)

- Default parser: Tree-sitter via Python bindings
- Languages: ~15 bundled grammars (~15 MB total), version-pinned
- Failure mode: If grammar fails or file unsupported, skip with warning
- No fallback tokenization — lexical index handles fuzzy matching
- Tree-sitter provides syntactic structure only; semantic resolution via SCIP

### 7.5 Semantic Layer (SCIP Batch Indexers)

#### Design Rationale

Persistent LSPs are rejected for CodePlane due to:
- Memory overhead: 200MB–1GB+ per language server, multiplied by active languages
- Latency on cold start: 5–30+ seconds to load project
- Multi-environment complexity: Cannot easily run multiple Python interpreters, Go modules, or Java SDKs concurrently
- Daemon resource constraints: CodePlane must remain lightweight

Instead, CodePlane uses **one-shot SCIP indexers** that:
- Run to completion and terminate (no persistent processes)
- Output SCIP protobuf files (standard format, tooling ecosystem)
- Support incremental/sharded operation (index changed files only)
- Decouple indexing from query-time

#### Supported SCIP Indexers

| Language | Indexer | Acquisition |
|----------|---------|-------------|
| Go | scip-go | GitHub releases |
| TypeScript/JavaScript | scip-typescript | npm package |
| Python | scip-python | PyPI or GitHub |
| Java/Kotlin | scip-java | GitHub releases |
| C# | scip-dotnet | NuGet or GitHub |
| Rust | rust-analyzer (SCIP mode) | GitHub releases |
| C/C++ | scip-clang | GitHub releases |

Additional indexers can be configured via `.codeplane/config.yaml`.

#### Indexer Lifecycle

1. **On-demand invocation**: Triggered by reconciliation when files are DIRTY
2. **Execution**: Indexer runs as subprocess, writes output to file path (not stdout)
3. **Termination**: Indexer must exit; hanging processes are killed after timeout
4. **Import**: Output parsed and merged into semantic index
5. **Cleanup**: Temporary SCIP files removed after import

#### File State Model

Each indexed file has a semantic state:

| State | Meaning | Refresh Behavior |
|-------|---------|------------------|
| CLEAN | Semantic data matches content, dependencies confirmed | No action needed |
| DIRTY | Content changed, refresh enqueued | Re-index this file |
| STALE | Dependency's interface confirmed changed | Re-index this file |
| PENDING_CHECK | Dependency is dirty, interface change unknown | Wait for dependency |

State transitions:
- File edit → DIRTY
- Dependency becomes DIRTY → dependents become PENDING_CHECK
- Dependency refresh completes with interface change → dependents become STALE
- Dependency refresh completes with no interface change → dependents return to CLEAN
- File refresh completes successfully → CLEAN

#### Refresh Job Worker

```
COMMIT ORDER (Critical):
1. Claim job (queued → running, atomic WHERE clause)
2. Run SCIP indexer
3. Check not superseded (HEAD at enqueue == current HEAD)
4. Import output into semantic index (transactional)
5. Mark completed AFTER import succeeds
```

HEAD-aware deduplication: Jobs keyed by `(context_id, head_at_enqueue)` to prevent redundant work after rapid commits.

#### Mutation Gate

Semantic writes (refactors using semantic data) require:
- All affected files must be CLEAN
- If any file is DIRTY/STALE/PENDING_CHECK, operation is rejected
- Escape hatch: `force_syntactic: true` allows syntactic-only edits

#### SCIP Indexer Configuration

```yaml
semantic:
  enabled: true
  indexers:
    python:
      command: ["scip-python", "index"]
      args: ["--project-root", "."]
      timeout_ms: 300000
    typescript:
      command: ["npx", "scip-typescript", "index"]
      timeout_ms: 300000
  refresh:
    max_concurrent_jobs: 2
    job_timeout_ms: 300000
    poll_interval_ms: 30000
```

### 7.6 Graph Index
- Nodes: symbols
- Edges: calls, imports, inherits, contains
- Schema: `relation(src_id, dst_id, type, weight)`
- Traversal:
  - Depth cap: 2–3
  - Fanout cap per node role (utility capped at 3, class at 10)
  - Deterministic order: lexicographic on symbol name
- Purpose:
  - Expand context for symbol search and rerank
  - Input to refactor targets and reference resolution

### 7.7 Indexing Mechanics

- Change detection:
  - Git blob hash + mtime for tracked files
  - Content hash for untracked files
- Chunk granularity:
  - Function/class-level when possible
  - Fallback to full file
- Update triggers:
  - On daemon start
  - Pre/post each operation
  - On detected repo state change
- Deleted reference cleanup:
  - On chunk deletion remove all edges targeting chunk
  - Update relation tables and affected symbols accordingly

### 7.8 Atomic Update Protocol

- All index writes go to a temp dir/db.
- On success:
  - `os.replace()` old `index/` and `meta.db` atomically
  - Optional backup previous revision (e.g. `index.prev/`)
- Performance target: full diff update (10–20 files) under 1–2s
- Crash safety: no intermediate state visible; recovery via Git + clean rebuild

### 7.9 Retrieval Pipeline (No Embeddings)

Pipeline:

1. Lexical search
2. Graph expansion (bounded)
3. Deterministic reranking:
   - exact matches
   - fuzzy matches
   - graph distance
   - file role (test vs src)
   - optional recency
4. External symbol enrichment (see §7.9.1)

This replaces repeated grep and file opening.

### 7.9.1 External Symbol Enrichment

Query responses that reference symbols from external libraries (dependencies, stdlib) are enriched with signature and docstring information when available from SCIP index data.

**Rationale:** Agents need library function signatures when refactoring code that calls them. Without this, agents guess or rely on training data (which may be stale for the installed version).

**Mechanism:**

1. Scan query results for unresolved symbols (calls/references not in indexed codebase)
2. Look up symbol in SCIP semantic index (external dependencies indexed when SCIP indexer runs)
3. Extract signature and docstring from SCIP occurrence data
4. Attach enrichment to response

**Response schema:**

```json
{
  "results": [...],
  "external_symbols": {
    "requests.get": {
      "signature": "get(url: str | bytes, params: ..., **kwargs) -> Response",
      "docstring": "Sends a GET request.",
      "source": "site-packages/requests/api.py"
    }
  }
}
```

**Scope:**

- Only symbols in returned snippets enriched (not pre-indexed)
- Enrichment available for languages with SCIP indexers
- If semantic index unavailable or stale, enrichment omitted (degraded but functional)

### 7.10 Mental Map Endpoints

“Single call” repo map returns:

- Directory structure
- Language breakdown
- Packages/modules
- Entry points
- Test layout
- Dependency hubs
- Public surface summaries

Symbol search returns:

- Definitions
- References
- Spans and usage counts

Targeted lexical search is indexed, scoped, structured, deterministic.

(Interface details are deferred; these are capabilities.)

### 7.11 Documentation Awareness

CodePlane indexes documentation files as first-class citizens alongside code, enabling agents to find explanations, examples, and references coherently.

#### Supported Documentation Formats

| Format | Extensions | Structure Extraction |
|--------|------------|---------------------|
| Markdown | `.md`, `.markdown` | Headings, code blocks, links |
| reStructuredText | `.rst` | Headings, code blocks, directives |
| AsciiDoc | `.adoc`, `.asciidoc` | Headings, code blocks, links |
| Plain text | `.txt`, `README`, `CHANGELOG` | Paragraph boundaries only |

#### Documentation Structure Index

For each documentation file, CodePlane extracts:

- **Headings**: Title, level, anchor slug, line span
- **Code blocks**: Language tag, content, line span, referenced symbols (best-effort)
- **Links**: Internal (relative paths), external (URLs), anchor references
- **Front matter**: YAML/TOML metadata blocks (common in static site generators)

This enables:
- Navigation by heading ("jump to Installation section")
- Finding code examples for a symbol
- Detecting broken internal links

#### Doc-Code Linking (Best-Effort)

CodePlane attempts to link documentation references to code symbols:

1. **Code blocks**: Parse code blocks using Tree-sitter (when language tagged) and extract symbol references
2. **Inline code**: Match backtick-wrapped identifiers (`` `MyClass` ``) against known symbols
3. **Import statements in examples**: Link to actual module definitions

Linking is best-effort and heuristic-based:
- Exact symbol name matches are linked with high confidence
- Partial matches (e.g., `MyClass` in prose without backticks) are flagged but not auto-linked
- Ambiguous references (multiple symbols with same name) are not linked

#### Search Ranking for Documentation

Documentation files receive adjusted ranking based on query intent signals:

| Query Pattern | Doc Weight | Code Weight |
|---------------|------------|-------------|
| "how to", "example", "usage" | Higher | Lower |
| "where is", "definition", "implementation" | Lower | Higher |
| Symbol name (exact) | Equal | Equal |
| General keyword | Equal | Equal |

Agents can also explicitly scope searches:
- `scope:docs` — documentation files only
- `scope:code` — source files only
- `scope:all` — default, both

#### Docstring Extraction

Docstrings are extracted as part of symbol metadata:

- Python: `"""..."""` immediately following `def`/`class`
- JavaScript/TypeScript: JSDoc `/** ... */` preceding functions/classes
- Go: `//` comment blocks preceding exported symbols
- Rust: `///` doc comments

Docstrings are:
- Stored with their parent symbol in the structural index
- Searchable via lexical index
- Returned as part of symbol search results

### 7.12 SCIP Indexer Management & Language Support

#### Acquisition Model

CodePlane does not bundle SCIP indexers. Indexers are downloaded on-demand and cached globally.

- Cache location: `~/.codeplane/indexers/{language}-{indexer}-{version}/`
- Manifest: `~/.codeplane/indexers/manifest.json` tracks installed indexers and their checksums
- Downloads are SHA256-verified against a signed manifest fetched from CodePlane's release infrastructure

#### Init-Time Discovery

`cpl init` performs language detection and prompts for SCIP indexer installation:

1. Scan repository for language indicators (file extensions, config files)
2. Present detected languages and recommended indexers
3. User confirms which indexers to install
4. Download, verify, and register confirmed indexers
5. Write selections to `.codeplane/config.yaml`

Example prompt:
```
Detected languages:
  ✓ Python (3847 files) — recommended: scip-python
  ✓ TypeScript (1203 files) — recommended: scip-typescript
  ✓ Go (892 files) — recommended: scip-go
  ○ Java (12 files) — recommended: scip-java (optional, 150MB)

Install SCIP indexers for [Python, TypeScript, Go]? [Y/n]
Include Java? [y/N]
```

#### Runtime Language Discovery

If incremental reindexing detects a new language not covered by installed indexers:

1. Daemon logs warning: `INDEXER_LANGUAGE_DISCOVERED`
2. Daemon sets status flag: `pending_indexer_install: true`
3. Semantic operations for that language return error `8003 INDEXER_LANGUAGE_UNSUPPORTED`
4. `cpl status` shows: `New language detected: Rust. Run 'cpl indexer install' or configure exclusion.`
5. MCP API continues serving all other operations normally (syntactic queries still work)
6. User runs `cpl indexer install` to interactively install missing indexers, or configures exclusion

This design:
- Never blocks the user silently
- Never auto-downloads without consent
- Keeps daemon running for languages already supported
- Makes the gap visible and actionable
- Syntactic layer remains fully functional for all languages

#### SCIP Indexer CLI Commands

| Command | Description |
|---------|-------------|
| `cpl indexer list` | Show installed SCIP indexers and their status |
| `cpl indexer install` | Interactive install for detected but missing languages |
| `cpl indexer install <language>` | Install SCIP indexer for specific language |
| `cpl indexer remove <language>` | Remove SCIP indexer for specific language |
| `cpl indexer update` | Check for and install indexer updates |
| `cpl indexer run [--language <lang>]` | Manually trigger semantic indexing |

#### SCIP Indexer Configuration

```yaml
semantic:
  enabled: true
  
  indexers:
    python:
      command: ["scip-python", "index"]
      args: ["--project-root", "."]
      timeout_ms: 300000
    typescript:
      command: ["npx", "scip-typescript", "index"]
      timeout_ms: 300000
    go:
      command: ["scip-go"]
      timeout_ms: 300000
  
  # Languages to exclude from semantic indexing
  exclude:
    - markdown
    - plaintext
  
  # Global settings
  refresh:
    max_concurrent_jobs: 2
    job_timeout_ms: 300000
    poll_interval_ms: 30000
```

#### Tree-sitter Grammar Management

Tree-sitter grammars provide syntactic parsing (separate from SCIP semantic indexing):
- Bundled for common languages (~15 grammars, ~15MB total)
- Downloadable for additional languages via `cpl grammar install <language>`
- Stored in `~/.codeplane/grammars/`
- Required for syntactic layer; semantic layer uses SCIP indexers

---

## 8. Deterministic Refactor Engine (SCIP-Based Semantic Data)

### 8.1 Purpose

Provide IntelliJ-class deterministic refactoring (rename / move / delete / change signature) across multi-language repositories using **pre-indexed SCIP semantic data** as the semantic authority, preserving determinism, auditability, and user control.

This subsystem is narrowly scoped: a high-correctness refactor planner and executor.

### 8.2 Core Principles

- SCIP-based semantics: all refactor planning uses pre-indexed SCIP semantic data
- Static configuration: languages, environments, roots known at startup
- No speculative semantics: CodePlane never guesses bindings
- No working tree mutation during planning
- Single atomic apply to the real repo
- Mutation gate: semantic writes require all affected files to be CLEAN
- Optional subsystem: enabled by default, configurable off

### 8.3 Supported Operations

- `rename_symbol(from, to, at)`
- `rename_file(from_path, to_path)`
- `move_file(from_path, to_path)`
- `delete_symbol(at)`

All operations:
- Return **structured diff output** with `files_changed`, `edits`, `symbol`, `new_name`, etc.
- Provide **preview → apply → rollback** semantics
- Are **atomic** at the patching level
- Operate across **tracked and untracked (overlay) files**
- Require all affected files to be in CLEAN semantic state
- Trigger deterministic re-indexing after apply

### 8.3a Architecture Overview

#### SCIP-Based Execution

- All refactor planning (rename, move, delete) uses SCIP semantic index data
- SCIP provides: symbol definitions, references, type hierarchies, import graphs
- No persistent language servers; semantic data pre-computed by batch indexers
- CodePlane maintains full control of edit application, version tracking, and reindexing

#### Semantic Data Flow

1. User requests refactor (e.g., rename symbol)
2. CodePlane checks mutation gate: all affected files must be CLEAN
3. Query SCIP index for all occurrences of target symbol
4. Generate structured edit plan from occurrence positions
5. Preview edits to user
6. Apply edits atomically
7. Mark affected files as DIRTY, enqueue semantic refresh

#### Edit Application and Reindexing

- Edit plans generated from SCIP occurrence data
- File edits are applied atomically via mutation engine
- Affected files are marked DIRTY and re-indexed
- Syntactic index updated immediately; semantic index refreshed via job queue
- Overlay/untracked files are updated as first-class citizens

### 8.3b Language Support Model

- Semantic refactors available for languages with SCIP indexers installed
- Syntactic-only fallback available via `force_syntactic: true` option
- Unsupported languages can still use syntactic edits (find/replace with confirmation)
- No runtime auto-detection; language support declared at init

### 8.4 Definitions

Context:

A context is the minimal semantic “world” in which an LSP can correctly analyze and refactor a subset of the repo.

Context includes:

- Language + LSP server type/version
- Environment selector:
  - Python interpreter path / venv
  - C# solution + SDK
  - Java build root
  - Go module/work root + tags
- Workspace roots
- Sparse-checkout include paths

Context worktree:

A persistent Git worktree per context sandbox:

- Reset to base commit R before each operation
- Sparse checkout to minimize I/O
- Optional warm LSP instance bound to that worktree (optional but default)

### 8.5 Refactor Execution Flow

1. **Mutation Gate Check**: All affected files must be CLEAN
2. **Query SCIP Index**: Find all occurrences of target symbol
3. **Generate Edit Plan**: Compute structured edits from occurrence positions
4. **Preview**: Show user the planned changes
5. **Apply**: Execute edits atomically via mutation engine
6. **Mark DIRTY**: Affected files enqueued for semantic re-indexing
7. **Syntactic Update**: Immediate update of syntactic index

If mutation gate fails (files DIRTY/STALE/PENDING_CHECK):
- Operation rejected with clear error
- User can wait for semantic refresh to complete
- Or use `force_syntactic: true` for syntactic-only edit

### 8.6 Multi-Context Handling

When multiple semantic contexts exist for a language (e.g., multiple Python venvs):

**Detection:**
- Each context produces independent SCIP index data
- Same file may have different semantic interpretations per context

**Refactor behavior:**
- Query all relevant contexts
- Merge occurrence sets
- Detect divergence (same position, different symbol identity)
- If divergent: fail and report conflicting contexts
- If consistent: proceed with merged occurrence set

CodePlane never silently guesses semantics.

### 8.7 Context Selection Rules

Minimum set:

- Context owning the definition file
- Contexts including known dependents (from index/config)

If uncertain:

- Query all contexts for that language (bounded by config)

### 8.8 Context Detection at Init

Principle: best-effort and safe; require explicit config when ambiguous.

Signals:

- .NET: multiple `.sln`
- Java: multiple independent `pom.xml` / `build.gradle`
- Go: multiple `go.mod` not unified by `go.work`
- Python: multiple env descriptors in separate subtrees

Classification:

- Single context → uses single context data
- Multiple valid roots → multi-context mode
- Ambiguous → require explicit config

Persistence:

- `.codeplane/contexts.yaml` (versioned schema)

### 8.9 Configuration Model (Minimal)

```yaml
contexts:
  - id: core-java
    language: java
    workspace_roots: [./core]
    env:
      build_root: ./core
    indexer:
      name: scip-java

defaults:
  max_parallel_contexts: 4
  divergence_behavior: fail
```

### 8.10 Git-Aware File Moves

- If a file rename or move affects a Git-tracked file:
  - CodePlane will perform a `git mv`-equivalent operation
  - This updates Git's index to reflect the move (preserving history)
  - Only performed if the file is clean and tracked
  - Fails safely if the working tree state is inconsistent (e.g. modified, unstaged)
- If the file is untracked or ignored (e.g. overlay files):
  - CodePlane performs a normal filesystem move only
- This ensures Git rename detection and downstream agent operations remain correct
- Preserves history; never commits

Structured diff will reflect:
```json
{
  "file_moved": true,
  "from": "src/old_path.py",
  "to": "src/new_path.py",
  "git_mv": true
}
```

### 8.11 Comments and Documentation References

SCIP-based renames **do not affect** comments, docstrings, or markdown files.

Examples of unaffected references:
- `# MyClassA` (comment)
- `"""Used in MyClassA."""` (docstring)
- `README.md` references to `MyClassA`
- Code examples in documentation
- Inline code references (`` `MyClassA` ``)

#### Auto-Update with Warning

CodePlane performs a **post-refactor documentation sweep** that:

1. **Scans** for textual references to the renamed symbol:
   - Comments in source code (from structural index)
   - Documentation files (markdown, RST, AsciiDoc, plain text)
   - Docstrings (extracted during indexing)
   - Code blocks in documentation (parsed for symbol references)
   - Inline code spans (`` `SymbolName` ``)

2. **Categorizes** matches by confidence:
   - **High confidence**: Exact match in backticks, code blocks, or import statements
   - **Medium confidence**: Exact match in prose near code context
   - **Low confidence**: Partial match or ambiguous context

3. **Auto-applies** changes but **flags for review**:
   - All documentation edits are applied in the same atomic patch
   - The response includes a `doc_updates_applied` field with:
     - Files changed
     - Matches found (with confidence levels)
     - Line numbers and context
   - A `review_recommended: true` flag when any low/medium confidence matches exist

4. **Structured response** includes both semantic and documentation edits:

```json
{
  "refactor": "rename_symbol",
  "semantic_edits": {
    "files_changed": 12,
    "edits": [...]
  },
  "doc_edits": {
    "files_changed": 3,
    "review_recommended": true,
    "matches": [
      {
        "file": "README.md",
        "line": 45,
        "confidence": "high",
        "context": "See `MyClassA` for details"
      },
      {
        "file": "docs/guide.md", 
        "line": 123,
        "confidence": "medium",
        "context": "The MyClassA handles authentication"
      }
    ]
  }
}
```

The agent receives the full diff and can verify documentation updates make sense in context. Since the operation is atomic, rollback reverts both semantic and documentation changes together.

#### Configuration

```yaml
refactor:
  doc_sweep:
    enabled: true           # default
    auto_apply: true        # apply doc changes automatically
    min_confidence: medium  # only auto-apply medium+ confidence
    scan_extensions:
      - .md
      - .rst
      - .adoc
      - .txt
```

This ensures textual references to renamed symbols are coherently updated without being conflated with semantic SCIP-backed mutations, while giving agents visibility into what changed and why.

### 8.12 Optional Subsystem Toggle

The deterministic SCIP-backed refactor engine is **enabled by default**, but may be disabled via configuration or CLI for environments with limited resources.

**Why disable:**
- SCIP indexers consume resources during indexing
- Some users may prefer to delegate refactors to agents or external tools

**How to disable:**

Via config:
```yaml
refactor:
  enabled: false
```

Or CLI:
```bash
cpl up --no-refactor
```

When disabled:

- No SCIP indexers run
- No refactor endpoints
- Syntactic indexing and generic mutation remain

### 8.13 Refactor Out of Scope

- Git commits, staging, revert, or history manipulation
- Test execution or build validation
- Refactor logs beyond structured diff response
- Dynamic language inference (e.g., `eval`, `getattr`)
- Partial or speculative refactors
- Multi-symbol refactors

### 8.14 Guarantees + Result Types

Always:
- **Deterministic**: Same refactor input → same result
- **Isolated**: Edits are applied only to confirmed, LSP-authorized files
- **Audit-safe**: Git-aware moves preserve index correctness
- **Overlay-compatible**: Untracked files handled equally
- **Agent-delegated commit control**: CodePlane never stages or commits
- No working tree mutation during planning
- Single atomic apply
- Explicit divergence reporting

Best-effort:

- Validation reporting
- Coverage limited to successfully loaded contexts

Results:

- Applied: merged patch, contexts used, optional validation results
- Divergence: conflicting hunks, contexts involved, diagnostics
- InsufficientContext: no viable context loaded; explicit configuration required

---

## 9. Mutation Engine (Atomic File Edits)

### 9.1 Design Objectives

- Never leave repo partial/corrupt/indeterminate.
- Always apply mutations atomically, or not at all.
- Permit concurrent mutations only when edits are disjoint.
- Maintain clean separation between file mutations and Git state (except rename tracking).
- Predictable cross-platform behavior (line endings, permissions, fsync).
- Always emit a structured delta reflecting the full effect.

### 9.2 Apply Protocol

- All edits are planned externally (LSP or reducer).
- All file edits staged in memory or temp files.
- Each target file exclusively locked prior to apply.
- Contents replaced wholesale via:
  - `os.replace()` (POSIX)
  - `ReplaceFile()` (Windows)
- `fsync()` called on new file and parent directory for durability.
- CRLF normalized to LF during planning; re-encoded on write to preserve original form.
- No in-place edits.

### 9.3 Concurrency Model

- Thread pool executor applies independent files in parallel.
- Thread count defaults to number of vcores.
- Final file write + rename serialized per file.
- Preconditions (hash or mtime+size) must pass prior to apply; otherwise abort.
- Overlapping mutations detected and blocked.

### 9.4 Scope Enforcement

- All file edits must fall within explicit working set or allowlist.
- `.cplignore` paths categorically excluded.
- Git-ignored files are editable but flagged for agent confirmation.
- New file paths created under allowed directory accepted.
- Mutations that touch unscoped paths rejected pre-apply.

### 9.5 Structured Delta Format (Required)

Per-file:

- `path`: relative path
- `oldHash`: pre-edit SHA256
- `newHash`: post-edit SHA256
- `lineEnding`: LF | CRLF
- `edits`: array of `{ range: {start: {line, char}, end: {line, char}}, newText, semantic, symbol? }`

Global:

- `mutationId`: UUID or agent-generated key
- `repoFingerprint`: hash of full file state
- `symbolsChanged`: optional list of semantic symbols affected
- `testsAffected`: optional list of test names

### 9.6 Failure and Rollback

- Any failure during write, rename, or precondition check aborts the batch.
- Temp files deleted.
- Locks released.
- Repo left in original state.
- No Git commands run as part of rollback.

### 9.7 Git Behavior (Mutation Engine)

- `git mv` is the only allowed Git mutation, and only for clean tracked files.
- Git index, HEAD, or refs are never modified.
- No Git status, reset, merge, stash operations triggered as rollback.

### 9.8 SCIP and Edit Planning

- All semantic refactors sourced from SCIP index data.
- No fallback to internal symbol index for semantic edit planning.
- All edits must conform to a unified diff format.

### 9.9 Performance Constraints

- Full-batch application of ~20 files should complete in <1s on modern SSD.
- Pre-write prep (diff, temp staging) parallelized.
- Final apply (rename+fsync) serialized and lock-guarded.
- No assumption of in-place edit savings.

### 9.10 Out of Scope (Mutation Engine)

- No Git commits, staging, reset, stash, merge.
- No recovery using Git state.
- No in-place edits or patch files.
- No speculative edits or partial semantic ops.

---

## 10. Git and File Operations (No Terminal Mediation)

Git:

All Git operations via `pygit2` (libgit2 bindings):

- **Read operations:** status, diff, blame, log, branches, tags, remotes, merge analysis
- **Write operations:**
  - Index: stage, unstage, discard
  - Commits: commit, amend
  - Branches: create, checkout, delete, rename
  - History: reset (soft/mixed/hard), merge, cherry-pick, revert
  - Stash: push, pop, apply, drop, list
  - Tags: create, delete
  - Remotes: fetch, push, pull
  - Rebase: plan, execute, continue, abort, skip (interactive rebase support)
  - Submodules: list, status, init, update, sync, add, deinit, remove
  - Worktrees: list, add, open, remove, lock, unlock, prune

Note: Some submodule operations (update, sync, add, deinit, remove) and worktree
remove use subprocess fallbacks to `git` CLI for completeness and credential
support where pygit2 bindings are incomplete.

Credentials for remote operations:
- SSH: via `KeypairFromAgent` (uses system SSH agent)
- HTTPS: via credential helper callback that invokes `git credential fill`

Agents never run git shell commands directly (except for the subprocess fallbacks noted above).

File operations:

- Native Python
- Atomic writes
- Hash-checked
- Scoped

Critical mutation semantics rule:

Every state-mutating operation returns a complete structured JSON delta including:

- Files changed
- Hashes before/after
- Diff stats
- Affected symbols
- Affected tests
- Updated repo state

This exists to eliminate verification loops and follow-up probing.

---

## 11. Tests: Planning, Parallelism, Execution

### 11.1 Goal

Fast deterministic test execution across large suites by parallelizing at test **target** level (files, packages, classes). Must support any language CodePlane indexes.

### 11.2 Definitions

- Test Target: smallest runnable unit CodePlane manages (e.g., a test file or Go package).
- Worker: CodePlane-managed subprocess executing one or more targets.
- Batch: set of targets assigned to worker.
- Estimated Cost: scalar weight used to balance batches (default 1).

### 11.3 Target Model

```json
{
  "target_id": "tests/test_utils.py",
  "lang": "python",
  "kind": "unit",
  "cmd": ["pytest", "tests/test_utils.py"],
  "cwd": "repo_root",
  "estimated_cost": 1.2
}
```

### 11.4 Execution Strategy

1. Discover targets:
   - per-language logic
   - stable `target_id`
   - default `estimated_cost`
2. Greedy bin packing:
   - assign to N workers by cost-balanced packing
3. Parallel execution:
   - spawn N subprocesses
   - each runs its batch sequentially
   - per-target and global timeouts
4. Merge results:
   - parse outputs to structured schema
   - classify failures
   - detect retries
   - label flaky outcomes

### 11.5 Test Runner Discovery

CodePlane uses a three-tier resolution strategy: explicit config → marker detection → language defaults.

#### Resolution Order (First Match Wins)

1. **Explicit config** in `.codeplane/config.yaml`
2. **Marker file detection** (see table below)
3. **Language default** (fallback)

#### Marker File Detection

| Marker | Runner | Priority |
|--------|--------|----------|
| `pytest.ini` | pytest | High |
| `pyproject.toml` with `[tool.pytest]` | pytest | High |
| `setup.cfg` with `[tool:pytest]` | pytest | Medium |
| `jest.config.js`, `jest.config.ts`, `jest.config.json` | jest | High |
| `package.json` with `"jest"` key | jest | Medium |
| `vitest.config.js`, `vitest.config.ts` | vitest | High |
| `go.mod` | go test | High |
| `Cargo.toml` | cargo test | High |
| `*.csproj` with test references | dotnet test | High |
| `pom.xml` | mvn test | Medium |
| `build.gradle`, `build.gradle.kts` | gradle test | Medium |
| `Gemfile` with rspec | rspec | Medium |
| `mix.exs` | mix test | High |

#### Language Defaults (When No Marker Found)

| Language | Default Runner |
|----------|---------------|
| Python | pytest |
| JavaScript/TypeScript | jest |
| Go | go test |
| Rust | cargo test |
| Java | mvn test |
| C# | dotnet test |
| Ruby | rspec |
| Elixir | mix test |

#### Config Override

```yaml
tests:
  runners:
    # Override detected runner
    python: pytest
    typescript: vitest  # Use vitest instead of detected jest
    
  # Custom runners for specific patterns
  custom:
    - pattern: "e2e/**/*.spec.ts"
      runner: playwright
      cmd: ["npx", "playwright", "test", "{path}"]
    - pattern: "integration/**/*.test.py"
      runner: pytest
      cmd: ["pytest", "--integration", "{path}"]
      timeout_sec: 120
      
  # Exclude patterns from test discovery
  exclude:
    - "**/fixtures/**"
    - "**/mocks/**"
```

#### Multiple Runners in Same Repo

When multiple test frameworks are detected:
- Each is registered independently
- Test targets are tagged with their runner
- Parallel execution respects runner boundaries
- Results are merged with runner attribution

Example: repo with jest (unit) + playwright (e2e) + pytest (backend):
```json
[
  {"target_id": "src/__tests__/utils.test.ts", "runner": "jest"},
  {"target_id": "e2e/login.spec.ts", "runner": "playwright"},
  {"target_id": "tests/test_api.py", "runner": "pytest"}
]
```

#### Runner Not Found

If a runner is configured but not available in PATH:
- `cpl doctor` reports: `Test runner 'pytest' not found in PATH`
- Test operations return error `7001 TEST_RUNNER_NOT_FOUND`
- CodePlane does not install test runners (user responsibility)

### 11.6 Language-Specific Targeting Rules

Target rules depend on language + available runner; supports any language with:

- recognized parser (Tree-sitter or LSP-backed)
- declarative discovery of test files/commands
- CLI runner that can execute individual test units

| Language | Target Granularity | Target ID Example | Cmd Template |
|---|---|---|---|
| Python | File (`test_*.py`) | `tests/test_utils.py` | `pytest {path}` |
| Go | Package (`./pkg/foo`) | `pkg/foo` | `go test -json ./pkg/foo` |
| JS/TS | File (`*.test.ts`) | `src/__tests__/foo.test.ts` | `jest {path}` |
| Java | Class or module | `com.example.FooTest` | `mvn -Dtest=FooTest test` |
| .NET | Project or class | `MyProject.Tests.csproj` | `dotnet test {path}` |
| Rust | File or module | `tests/integration_test.rs` | `cargo test --test {name}` |
| Ruby | File (`*_spec.rb`) | `spec/models/user_spec.rb` | `rspec {path}` |
| Elixir | File (`*_test.exs`) | `test/my_app_test.exs` | `mix test {path}` |

### 11.6 Defaults

- `N = min(#vCPUs, 8)`
- Target cost = 1 if unknown
- Fail-fast: stop if first failure batch completes (configurable)
- Timeout: 30s per target (configurable)

### 11.7 Optional Enhancements

- Historical cost recording per target (rolling median)
- Resource class labels (`unit`, `integration`, etc.)
- Test suite fingerprints for delta debugging

### 11.8 Out of Scope

- Per-test-case parallelism
- CI sharding or remote execution
- API interface definition (handled separately)

---

## 12. Task Model, Convergence Controls, and Ledger

### 12.1 Scope and Principle

CodePlane models tasks, enforces convergence bounds, and persists an operation ledger.

Core principle:

CodePlane never relies on agent discipline; it enforces mechanical constraints making non-convergence visible, finite, and auditable.

### 12.2 Task Definition and Lifecycle

A task is a correlation envelope for operations.

A task exists to:

- group related operations
- apply execution limits
- survive daemon restarts
- produce structured outcomes

A task does not:

- own control flow
- store agent reasoning
- perform retries
- infer success/failure

Lifecycle states:

| State | Meaning |
|---|---|
| OPEN | Task active; operations correlated |
| CLOSED_SUCCESS | Task ended cleanly |
| CLOSED_FAILED | Task aborted due to limits/invariants |
| CLOSED_INTERRUPTED | Daemon restart or client disconnect |

Tasks are explicitly opened and closed; never reopened implicitly.

Persisted task state:

```yaml
task_id: string
opened_at: timestamp
closed_at: timestamp | null
state: OPEN | CLOSED_*
repo_snapshot:
  git_head: sha
  index_version: int
limits:
  max_mutations: int
  max_test_runs: int
  max_duration_sec: int
counters:
  mutation_count: int
  test_run_count: int
last_mutation_fingerprint: string | null
last_failure_fingerprint: string | null
```

Not persisted:

- prompts
- agent intent
- reasoning traces
- retry logic

### 12.3 Convergence Controls (Server-Enforced)

1. Mutation budget:
   - Each state-mutating call increments `mutation_count`.
   - If `mutation_count > max_mutations`, reject mutation and set task to CLOSED_FAILED.

2. Test execution budget:
   - Test runs are first-class operations.
   - If `test_run_count > max_test_runs`, reject further test calls.

3. Failure fingerprinting:
   - Deterministic failures fingerprinted using:
     - failing test names
     - normalized exception type
     - normalized stack trace
     - exit code
   - Fingerprint returned in each failure response.
   - If same fingerprint occurs after a mutation, CodePlane flags non-progress.

4. Mutation fingerprinting:
   - Each mutation returns fingerprint:
     - `files_changed_hash`
     - `diff_stats`
     - `symbol_changes`
   - Identical consecutive mutation fingerprints:
     - mark as no-op
     - budget still increments

CodePlane does not decide next step.

### 12.4 Restart Semantics

On daemon restart:

- All OPEN tasks marked CLOSED_INTERRUPTED.
- Repo reconciled from Git.
- Indexes revalidated incrementally.
- No task resumes implicitly.

Clients must open a new task.

Guarantees:

- No mixed state
- No replayed side effects
- No phantom progress

### 12.5 Operation Ledger

#### v1 vs v1.5 Scope

CodePlane deliberately distinguishes between **v1 (minimal, SQLite-only)** logging and **v1.5 (optional artifact expansion)**.

- v1 focuses on *mechanical accountability* only.
- v1.5 exists solely to improve developer ergonomics if real pain appears.

#### Purpose

The ledger provides **mechanical accountability**, not observability or surveillance.

It exists to answer:
- what happened
- in what order
- under what limits
- with what effects

Primary persistence:

- Local append-only SQLite DB owned by daemon, stored in repo:
  - `.codeplane/ledger.db`

v1 ledger schema (SQLite only):

```sql
tasks (
  task_id TEXT PRIMARY KEY,
  opened_at TIMESTAMP,
  closed_at TIMESTAMP,
  state TEXT,
  repo_head_sha TEXT,
  limits_json TEXT
);

operations (
  op_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  timestamp TIMESTAMP,
  duration_ms INTEGER,
  op_type TEXT,
  success BOOLEAN,

  -- repo boundaries
  repo_before_hash TEXT,
  repo_after_hash TEXT,

  -- mutation summary (no content)
  changed_paths TEXT,           -- JSON array of file paths
  diff_stats TEXT,              -- files_changed, insertions, deletions
  short_diff TEXT,              -- e.g. "+ foo.py", "- bar.ts", "~ baz.go"

  -- convergence signals
  mutation_fingerprint TEXT,
  failure_fingerprint TEXT,
  failure_class TEXT,
  failing_tests TEXT,
  limit_triggered TEXT,

  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);
```

Ledger is append-only.

Optional artifact store (v1.5, deferred):

- Only if needed for debugging.
- Stores:
  - full test logs
  - full diffs/patches
  - tool stdout/stderr
- Stored on filesystem; referenced by artifact_id + hash in SQLite.
- Short-lived (hours/days).
- Derived mirror (non-authoritative) may exist:
  - `~/.codeplane/ledger/YYYY-MM-DD.ndjson`
- Ledger remains authoritative; artifacts disposable.

Retention policy:

- v1 default:
  - retain 7–14 days or last 500 tasks
  - configurable
- v1.5:
  - artifacts retained 24–72 hours
  - aggressively GCed
  - missing artifacts never invalidate ledger integrity

Audit model:

- Intended auditors: developers, agent/tool authors, maintainers.
- Explicitly not for: compliance surveillance, user monitoring, model training.

Explicitly does not do:

- no retries
- no backoff
- no strategy shifts
- no planning
- no success inference

---

## 13. Observability and Operator Insight

### 13.1 Why Observability

CodePlane is infrastructure. Infrastructure requires visibility.

Operators need to answer:

- Is the daemon healthy?
- Are agents making progress or spinning?
- Which operations are slow, failing, or succeeding?
- Is the index fresh or stale?
- Are LSP servers responsive or degraded?

Without observability, operators debug blind.

### 13.2 Scope and Principles

Observability in CodePlane serves **operators and tool authors**, not surveillance or model training.

Principles:

1. **Visibility without overhead**: Observability is always-on, not sampled or opt-in.
2. **Structured and queryable**: Telemetry is structured data, not log grep.
3. **Bundled and self-contained**: No external dependencies required. Dashboard ships with daemon.
4. **Standards-based**: OpenTelemetry for traces and metrics. Exportable but not required.

### 13.3 What CodePlane Monitors

Observability covers three categories:

#### Operations (Request-Level)

Every MCP operation emits a trace with spans:

- Operation type, parameters, and outcome
- Duration and timing breakdown
- Task correlation (if within a task envelope)
- Files touched, symbols resolved, tests run
- Error codes and failure fingerprints

Purpose: Understand what agents are doing, how long it takes, and what fails.

#### System Health (Daemon-Level)

The daemon exposes continuous health metrics:

| Metric | What It Measures |
|--------|------------------|
| Index staleness | Time since last reconciliation; drift from Git HEAD |
| LSP status | Per-language availability, response times, error rates |
| Resource usage | Memory, CPU, open file handles |
| Reconciliation rate | Reconciliations per minute; duration histogram |
| Task throughput | Tasks opened/closed per interval; budget exhaustion rate |

Purpose: Know if the daemon is healthy before problems compound.

#### Convergence Signals (Agent-Level)

Observability surfaces agent progress signals:

| Signal | What It Measures |
|--------|------------------|
| Mutation fingerprint repetition | Same fingerprint after mutation → no progress |
| Failure fingerprint repetition | Same failure after mutation → non-converging |
| Budget utilization | Percentage of task budget consumed |
| Operation cadence | Operations per minute; pauses and bursts |

Purpose: Detect spinning agents and non-convergent loops without CodePlane making decisions.

### 13.4 How Operators Access Observability

#### Dashboard Endpoint

The daemon exposes a unified dashboard at `/dashboard`:

- Bundled with daemon; no external setup
- Accessible via browser at `http://127.0.0.1:<port>/dashboard`
- Unified view of traces, metrics, and health

Dashboard capabilities:

- Filter operations by task, operation type, outcome, time range
- View individual traces with span breakdowns
- Monitor real-time health metrics
- Identify slow or failing operations

#### Metrics Endpoint

The daemon exposes a Prometheus-compatible metrics endpoint at `/metrics`:

- Scrapeable by external monitoring systems
- Useful for fleet-level aggregation (optional, not required)
- Includes all health metrics from section 13.3

#### Programmatic Access

- Traces: Available via OpenTelemetry export (optional configuration)
- Metrics: Available via `/metrics` endpoint
- Ledger: Remains the authoritative record (section 12.5)

### 13.5 Relationship to Ledger

The ledger (section 12) and observability serve different purposes:

| Aspect | Ledger | Observability |
|--------|--------|---------------|
| Purpose | Mechanical accountability | Operational insight |
| Retention | Days to weeks | Real-time + short-term |
| Audience | Post-hoc audit | Live debugging |
| Format | SQLite, append-only | Traces, metrics, dashboards |
| Scope | Task and operation records | System-wide health |

They complement, not replace, each other.

### 13.6 What Observability Does Not Do

Observability does not:

- Make decisions for agents
- Trigger alerts or automated responses
- Persist indefinitely (traces are ephemeral; ledger is durable)
- Phone home or transmit externally (unless explicitly configured)
- Require external infrastructure to function

Observability is passive visibility, not active control.

---

## 15. "Deterministic Refactoring Primitives" (Summary-Level Capability List)

This section preserves the explicit capability list for quick reference.

Refactors described as tool operations:

- `rename_symbol(from, to, at)`
- `rename_file(from_path, to_path)`
- `move_file(from_path, to_path)`
- `delete_symbol(at)`

Implementation:

- All semantic refactors use LSP (`textDocument/rename`, etc.) as the sole authority
- CodePlane never guesses or speculatively resolves bindings
- Non-semantic operations (exact-match comment/docstring sweeps, mechanical file renames) are handled separately and reported as optional, previewable patches

All refactors:

- Produce atomic edit batches
- Provide previews
- Apply via CodePlane patch system
- Return full structured context

---

## 16. Embeddings Policy

Embeddings are intentionally excluded from the core design.

Rationale:

- Agents can explore structure deterministically.
- Embedding lifecycle cost is high.
- Core value is indexing + structure + execution.

If added later:

- Optional
- Gated
- Partial
- Never foundational

---

## 17. Subsystem Ownership Boundaries (Who Owns What)

### 15.1 CodePlane Owns

- Repo reconciliation (Git-centric, deterministic)
- Indexing:
  - Tantivy lexical index
  - SQLite structural metadata
  - Graph construction/traversal bounds
  - Atomic index updates
- Shared tracked index artifact production/consumption rules (CI build, checksum verify, cache, forward-compat limits)
- Overlay index lifecycle (local-only, rebuildable)
- File mutation application protocol:
  - lock
  - scope enforce
  - atomic apply
  - structured deltas
- Semantic refactor protocol:
  - contexts
  - worktrees
  - patch merge
  - divergence reporting
  - single atomic apply
- Test target discovery adapters + parallel target execution harness
- Task envelopes + convergence limits
- Operation ledger persistence + retention + optional artifacts
- Operator CLI + lifecycle + diagnostics + config layering

### 15.2 CodePlane Does Not Own

- Planning, strategy selection, retries, success inference
- Editor buffer state; it reconciles from disk + Git
- Git commits, staging/branch management flows, merges, rebases, stashes, resets (explicitly out of scope for mutation engine; read-only operations allowed)
- Embeddings-first semantic retrieval
- Remote execution / CI sharding

---

## 18. Resolved Conflicts (Previously Open)

The following contradictions have been resolved:

1. **`.env` overlay indexing**: Resolved. Default-blocked in `.cplignore`. Users can explicitly whitelist via `!.env` if needed. See section 6.10.

2. **Refactor fallback semantics**: Resolved. Semantic refactors are LSP-only; CodePlane never guesses bindings. "Structured lexical edits" refers only to non-semantic operations (exact-match comment sweeps, mechanical file renames). These are explicitly not semantic refactors.

3. **Tree-sitter failure policy**: Resolved. On parse failure, skip file, log warning, continue indexing. Never abort the indexing pass for a single file failure. See section 7.4.

4. **"Always-on" framing vs explicit lifecycle**: Resolved. CodePlane is conceptually a control plane, operationally a repo-scoped daemon managed via `cpl up` / `cpl down`. OS service integration is deferred.

---

## 19. Risk Register (Remaining Design Points)

Items 1-3 from the original register have been resolved (see section 16). Remaining items:

1. Multi-context scaling:
   - context explosion risk
   - warm LSP resource footprint
   - operational limits beyond `max_parallel_contexts` not fully specified
2. Optional watchers:
   - must never become correctness-critical
   - must not violate "no background mutation"
3. Security posture depends on Git hygiene:
   - secrets committed to Git leak into shared artifacts by definition; mitigations are external (pre-commit hooks, scanning)

---

## 20. Readiness Note: What Is Stable Enough for API Surfacing Next

Stable enough that API design should be mechanical:

- Repo fingerprinting, reconciliation triggers, and invariants
- Index composition and update protocol
- Structured delta requirements for all mutations
- Mutation apply protocol and scope rules
- Refactor context/worktree planning and divergence reporting shapes
- Test target model and parallel execution semantics
- Task envelope semantics, budgets, fingerprinting, restart behavior
- Ledger schema, retention policy, optional artifact model
- CLI lifecycle and operability checks
- Config layering and defaults framework
- Observability model, trace/metric categories, and dashboard scope

All previously-open contradictions have been resolved. API surfacing can proceed.

---

## 21. What CodePlane Is (Canonical Summary)

CodePlane is:

- A repository control plane
- A deterministic execution layer
- A structured context provider
- A convergence enforcer

It turns AI coding from slow and chaotic into fast, predictable, and auditable by fixing the system, not the model.

---

## 22. MCP API Specification

### 22.1 Design Principles

The MCP API is the primary interface for AI agents to interact with CodePlane.

Core design choices:

| Dimension | Choice | Rationale |
|-----------|--------|-----------|
| Protocol | **Hybrid**: MCP (tools) + REST (admin) | MCP for agents, REST for operators |
| Framework | **FastMCP**: Official MCP Python SDK | Zero custom protocol code, schema from types |
| Granularity | **Namespaced families**: ~35 tools | One tool per operation, grouped by prefix |
| Streaming | **Context.report_progress**: native MCP | Progress via protocol, not separate tools |
| Naming | **Prefixed families**: `git_*`, `search_*`, etc. | Namespace safety, semantic grouping |
| State | **Envelope wrapper**: meta in every response | Session context without model pollution |

**Tool Design Principles:**

1. **One tool, one purpose** — Each tool has a single responsibility and return type
2. **Namespaced families** — Related tools share prefix: `git_*`, `search_*`, `refactor_*`
3. **Session via envelope** — Every response wrapped with session/timing metadata
4. **Progress via Context** — Long operations report progress through MCP's native mechanism
5. **Pagination via response models** — Cursor-based pagination encoded in return type

### 22.2 Protocol Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Clients                               │
│  (Claude, Cursor, Copilot, Continue, custom agents)             │
└─────────────────────┬───────────────────────────────────────────┘
                      │ MCP/JSON-RPC 2.0 over HTTP/SSE
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CodePlane Daemon                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  FastMCP Server │  │  REST Handler   │  │  SSE Handler    │  │
│  │   (~35 tools)   │  │  (/health, etc) │  │  (streaming)    │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
│           │                    │                    │           │
│           └────────────────────┼────────────────────┘           │
│                                ▼                                │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                  Response Envelope Wrapper                  ││
│  │  - Wrap all tool responses with ToolResponse[T]             ││
│  │  - Inject session_id, request_id, timestamp                 ││
│  │  - Track task state, budgets, fingerprints                  ││
│  └─────────────────────────────────────────────────────────────┘│
│                                │                                │
│           ┌────────────────────┼────────────────────┐           │
│           ▼                    ▼                    ▼           │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐      │
│  │   Index     │      │  Refactor   │      │   Mutation  │      │
│  │   Engine    │      │   Engine    │      │   Engine    │      │
│  └─────────────┘      └─────────────┘      └─────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

### 22.3 Response Envelope

All tool responses are wrapped in a consistent envelope that provides session context without polluting domain models.

**Envelope schema:**

```python
@dataclass
class ResponseMeta:
    session_id: str | None
    request_id: str
    timestamp_ms: int
    task_id: str | None = None
    task_state: str | None = None  # "OPEN" | "CONVERGED" | "FAILED" | "CLOSED"

@dataclass
class ToolResponse(Generic[T]):
    result: T
    meta: ResponseMeta
```

**Wire format:**

```json
{
  "result": {
    "oid": "abc123def456",
    "message": "feat: add new feature"
  },
  "meta": {
    "session_id": "sess_a1b2c3d4e5f6",
    "request_id": "req_x9y8z7w6v5u4",
    "timestamp_ms": 1706400000000,
    "task_id": "task_p1q2r3s4t5u6",
    "task_state": "OPEN"
  }
}
```

**Implementation:**

A `@codeplane_tool` decorator wraps FastMCP's `@mcp.tool()` to inject the envelope:

```python
def codeplane_tool(mcp: FastMCP):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, ctx: Context, **kwargs):
            result = await fn(*args, ctx=ctx, **kwargs)
            return ToolResponse(
                result=result,
                meta=ResponseMeta(
                    session_id=get_session_id(ctx),
                    request_id=ctx.request_id,
                    timestamp_ms=int(time.time() * 1000),
                    task_id=get_task_id(ctx),
                    task_state=get_task_state(ctx),
                )
            )
        return mcp.tool()(wrapper)
    return decorator
```

**Session lifecycle:**

1. **Auto-creation**: Session created on first tool call from a connection
2. **Task binding**: Session creates an implicit task envelope for convergence tracking
3. **State tracking**: All operations within session share counters and fingerprints
4. **Timeout**: Idle sessions close after 30 minutes (configurable)
5. **Explicit control**: Client can create/close/switch sessions via `session_*` tools

**Explicit session override:**

Any tool can accept optional `session_id` parameter to:
- Join an existing session from another connection
- Resume a session after reconnect
- Run operations in a specific task context

### 22.4 Tool Catalog

Tools are organized into namespaced families. Each tool has a single responsibility and strongly-typed response.

#### Session Tools

| Tool | Purpose |
|------|---------|
| `session_info` | Get current session state, task context, operation history |
| `session_create` | Create new session with optional task description |
| `session_close` | Close session and finalize task |

#### Git Tools — Read Operations

| Tool | Purpose |
|------|---------|
| `git_status` | Repository status (staged, modified, untracked, conflicts) |
| `git_diff` | Generate diff between refs or working tree |
| `git_blame` | Line-by-line authorship for a file |
| `git_log` | Commit history with optional filters |
| `git_show` | Show commit details |
| `git_branches` | List branches with tracking info |
| `git_tags` | List tags |
| `git_remotes` | List configured remotes |
| `git_stash_list` | List stash entries |

#### Git Tools — Write Operations

| Tool | Purpose |
|------|---------|
| `git_stage` | Stage files for commit |
| `git_unstage` | Unstage files |
| `git_discard` | Discard working tree changes |
| `git_commit` | Create commit |
| `git_amend` | Amend previous commit |
| `git_create_branch` | Create new branch |
| `git_checkout` | Switch branches or restore files |
| `git_delete_branch` | Delete branch |
| `git_merge` | Merge branches |
| `git_cherrypick` | Cherry-pick commits |
| `git_revert` | Revert commits |
| `git_reset` | Reset HEAD to a state |
| `git_stash_push` | Stash changes |
| `git_stash_pop` | Pop stash entry |
| `git_fetch` | Fetch from remote |
| `git_push` | Push to remote |
| `git_pull` | Pull from remote |

#### Git Tools — Rebase

| Tool | Purpose |
|------|---------|
| `git_rebase_plan` | Generate rebase plan (commits to be rebased) |
| `git_rebase_execute` | Execute rebase plan |
| `git_rebase_continue` | Continue after conflict resolution |
| `git_rebase_abort` | Abort and restore original state |
| `git_rebase_skip` | Skip current commit |

#### Git Tools — Submodules

| Tool | Purpose |
|------|---------|
| `git_submodules` | List submodules with status |
| `git_submodule_init` | Initialize submodules |
| `git_submodule_update` | Update submodules to recorded commits |
| `git_submodule_add` | Add new submodule |
| `git_submodule_remove` | Remove submodule |

#### Git Tools — Worktrees

| Tool | Purpose |
|------|---------|
| `git_worktrees` | List worktrees |
| `git_worktree_add` | Add new worktree |
| `git_worktree_remove` | Remove worktree |

#### Search Tools

| Tool | Purpose |
|------|---------|
| `search` | Unified search (lexical, symbol, references, definitions) |

#### Read Tools

| Tool | Purpose |
|------|---------|
| `read_files` | Read file contents with optional line ranges |
| `map_repo` | Repository structure and mental model |

#### Mutation Tools

| Tool | Purpose |
|------|---------|
| `mutate` | Atomic file edits |

#### Refactor Tools (Requires LSP)

| Tool | Purpose |
|------|---------|
| `refactor_rename` | Rename symbol across codebase |
| `refactor_move` | Move symbol to different file |
| `refactor_preview` | Preview refactoring changes |
| `refactor_apply` | Apply previewed refactoring |

#### Test Tools

| Tool | Purpose |
|------|---------|
| `test_discover` | Discover tests in codebase |
| `test_run` | Execute tests with progress |

#### Status Tools

| Tool | Purpose |
|------|---------|
| `status` | Daemon health, index state |

**Total: ~35 tools**

### 22.5 Progress Reporting

Long-running operations report progress through MCP's native `Context.report_progress()` mechanism rather than separate streaming tool variants.

**Example:**

```python
@codeplane_tool(mcp)
async def test_run(
    targets: list[str] | None = None,
    fail_fast: bool = False,
    ctx: Context,
) -> TestSuiteResult:
    """Run tests with live progress updates."""
    tests = await discover_tests(targets)
    results = []
    
    for i, test in enumerate(tests):
        await ctx.report_progress(
            progress=i,
            total=len(tests),
            message=f"Running {test.name}",
        )
        result = await run_test(test)
        results.append(result)
        
        if fail_fast and not result.passed:
            break
    
    return TestSuiteResult(tests=results)
```

Clients receive progress events via the MCP protocol's built-in progress notification mechanism.

### 22.6 Pagination

Tools returning collections support cursor-based pagination for large result sets.

#### Request Parameters

```typescript
{
  // ... tool-specific parameters ...
  cursor?: string;  // Opaque continuation token from previous response
  limit?: number;   // Results per page (default 20, max 100)
}
```

#### Response Schema

```typescript
{
  results: Array<T>;
  pagination: {
    next_cursor?: string;      // Present if more results available
    total_estimate?: number;   // Approximate total (optional, may be expensive)
  };
  // ... other tool-specific fields ...
}
```

#### Pagination Behavior

1. **Cursor opacity** — Cursors are opaque strings; clients must not parse or construct them
2. **Cursor lifetime** — Cursors remain valid for the session lifetime or 1 hour, whichever is shorter
3. **Consistency model** — Pagination uses snapshot isolation; concurrent writes do not affect in-flight pagination
4. **Exhaustion** — When `next_cursor` is absent, all results have been returned

#### Paginated Tools

| Tool | Paginates | Notes |
|------|-----------|-------|
| `search` | Yes | All search modes |
| `map_repo` (structure) | Yes | File tree only |
| `git_log` | Yes | Commit history |
| `git_blame` | Yes | Line authorship |
| `read_files` | No | Uses explicit line ranges |
| `mutate` | No | Single operation |

### 22.7 Tool Specifications

The following sections define detailed parameter and response schemas for each tool. All responses are wrapped in the `ToolResponse` envelope (see 22.3).

---

#### `search`

Unified search across lexical index, symbols, and references.

**Parameters:**

```typescript
{
  query: string;                    // Search query
  mode: "lexical" | "symbol" | "references" | "definitions";
  scope?: {
    paths?: string[];               // Limit to paths (glob patterns)
    languages?: string[];           // Limit to languages
    kinds?: string[];               // Symbol kinds: function, class, variable, etc.
  };
  limit?: number;                   // Max results (default 20, max 100)
  cursor?: string;                  // Continuation token
  include_snippets?: boolean;       // Include code snippets (default true)
  session_id?: string;              // Optional session override
}
```

**Response:**

```typescript
{
  results: Array<{
    path: string;
    line: number;
    column: number;
    snippet: string;
    symbol?: {
      name: string;
      kind: string;
      container?: string;
    };
    score: number;
    match_type: "exact" | "fuzzy" | "semantic";
  }>;
  pagination: {
    next_cursor?: string;
    total_estimate?: number;
  };
  query_time_ms: number;
}
```

---

#### `map_repo`

Repository mental model — structure, languages, entry points, dependencies.

**Parameters:**

```typescript
{
  include?: Array<"structure" | "languages" | "entry_points" | "dependencies" | "test_layout" | "public_api">;
  depth?: number;                   // Directory depth (default 3)
  session_id?: string;
}
```

**Response:**

```typescript
{
  structure: {
    root: string;
    tree: DirectoryNode[];          // Nested directory structure
    file_count: number;
    total_lines: number;
  };
  languages: Array<{
    language: string;
    file_count: number;
    line_count: number;
    percentage: number;
  }>;
  entry_points: Array<{
    path: string;
    kind: "main" | "cli" | "api" | "test" | "config";
    language: string;
  }>;
  dependencies: {
    direct: string[];
    dev: string[];
    package_manager: string;
  };
  test_layout: {
    framework: string;
    test_dirs: string[];
    test_count: number;
  };
  public_api: Array<{
    symbol: string;
    kind: string;
    path: string;
    exported: boolean;
  }>;
  _session: SessionState;
}
```

---

#### `read_files`

Read file contents with optional line ranges.

**Parameters:**

```typescript
{
  paths: string | string[];         // Single path or array
  ranges?: Array<{                  // Optional line ranges per file
    path: string;
    start_line: number;
    end_line: number;
  }>;
  include_metadata?: boolean;       // Include file stats (default false)
  session_id?: string;
}
```

**Response:**

```typescript
{
  files: Array<{
    path: string;
    content: string;
    language: string;
    line_count: number;
    range?: { start: number; end: number };
    metadata?: {
      size_bytes: number;
      modified_at: string;
      git_status: "clean" | "modified" | "untracked";
      hash: string;
    };
  }>;
  _session: SessionState;
}
```

---

#### `mutate`

Atomic file edits with structured delta response.

**Parameters:**

```typescript
{
  edits: Array<{
    path: string;
    action: "create" | "update" | "delete";
    content?: string;               // Full content for create/update
    patches?: Array<{               // Or line-level patches
      range: { start: number; end: number };
      replacement: string;
    }>;
  }>;
  dry_run?: boolean;                // Preview only (default false)
  session_id?: string;
}
```

**Response:**

```typescript
{
  applied: boolean;
  dry_run: boolean;
  delta: {
    mutation_id: string;
    files_changed: number;
    insertions: number;
    deletions: number;
    files: Array<{
      path: string;
      action: "created" | "updated" | "deleted";
      old_hash?: string;
      new_hash?: string;
      diff_stats: { insertions: number; deletions: number };
    }>;
  };
  affected_symbols?: string[];
  affected_tests?: string[];
  repo_fingerprint: string;
  _session: SessionState;
}
```

---

#### Git Tools (`git_*`)

Git operations are exposed as individual tools with the `git_` prefix. Each tool maps to a specific operation with strongly-typed parameters and responses.

**Tool naming convention:** `git_{operation}` (e.g., `git_status`, `git_commit`, `git_diff`)

**Common optional parameter:** All git tools accept `session_id?: string` for session override.

##### `git_status`

```typescript
// Parameters
{ paths?: string[] }

// Response
{
  branch: string | null;
  head_commit: string;
  is_clean: boolean;
  staged: Array<{ path: string; status: string; old_path?: string }>;
  modified: Array<{ path: string; status: string }>;
  untracked: string[];
  conflicts: Array<{ path: string; ancestor_oid?: string; ours_oid?: string; theirs_oid?: string }>;
  state: "none" | "merge" | "revert" | "cherrypick" | "rebase" | "bisect";
}
```

##### `git_diff`

```typescript
// Parameters
{
  base?: string;       // Commit/ref to diff against
  target?: string;     // Target ref (default: working tree)
  staged?: boolean;    // Diff staged changes
  paths?: string[];    // Scope to paths
}

// Response
{
  files: Array<{
    path: string;
    status: "added" | "modified" | "deleted" | "renamed" | "copied";
    old_path?: string;
    binary: boolean;
    hunks: Array<{
      old_start: number;
      old_lines: number;
      new_start: number;
      new_lines: number;
      header: string;
      lines: Array<{ origin: "+" | "-" | " "; content: string; old_lineno?: number; new_lineno?: number }>;
    }>;
  }>;
  stats: { files_changed: number; insertions: number; deletions: number };
}
```

##### `git_commit`

```typescript
// Parameters
{
  message: string;
  paths?: string[];    // Specific paths (default: all staged)
  author?: { name: string; email: string };
  allow_empty?: boolean;
}

// Response
{ oid: string; short_oid: string }
```

##### `git_log`

```typescript
// Parameters
{
  ref?: string;        // Starting ref (default: HEAD)
  limit?: number;      // Max commits (default: 50)
  since?: string;      // ISO date
  until?: string;      // ISO date
  paths?: string[];    // Filter to paths
  cursor?: string;     // Pagination
}

// Response
{
  commits: Array<{
    oid: string;
    short_oid: string;
    message: string;
    author: { name: string; email: string; time: string };
    parents: string[];
  }>;
  pagination: { next_cursor?: string };
}
```

##### `git_merge`

```typescript
// Parameters
{
  ref: string;         // Branch/ref to merge
  message?: string;    // Merge commit message
}

// Response
{
  success: boolean;
  fastforward: boolean;
  commit?: string;
  conflicts: Array<{ path: string; ancestor_oid?: string; ours_oid?: string; theirs_oid?: string }>;
}
```

##### `git_rebase_plan`

```typescript
// Parameters
{
  upstream: string;    // Upstream ref to rebase onto
  onto?: string;       // Optional: rebase onto different base
}

// Response
{
  upstream: string;
  onto: string;
  steps: Array<{
    action: "pick";
    commit_sha: string;
    message: string;
  }>;
}
```

##### `git_rebase_execute`

```typescript
// Parameters
{
  plan: {
    upstream: string;
    onto: string;
    steps: Array<{
      action: "pick" | "reword" | "edit" | "squash" | "fixup" | "drop";
      commit_sha: string;
      message?: string;  // For reword/squash
    }>;
  };
}

// Response
{
  success: boolean;
  completed_steps: number;
  total_steps: number;
  state: "done" | "conflict" | "edit_pause" | "aborted";
  conflict_paths?: string[];
  current_commit?: string;
  new_head?: string;
}
```

##### Other Git Tools

The remaining git tools follow similar patterns:

| Tool | Key Parameters | Response Summary |
|------|---------------|------------------|
| `git_blame` | `path`, `line_range?` | Line authorship with commit info |
| `git_show` | `ref` | Commit details with diff |
| `git_branches` | - | List of branches with tracking |
| `git_tags` | - | List of tags |
| `git_remotes` | - | List of remotes with URLs |
| `git_stage` | `paths` | Staging result |
| `git_unstage` | `paths` | Unstaging result |
| `git_discard` | `paths` | Discard result |
| `git_amend` | `message?` | Amended commit OID |
| `git_create_branch` | `name`, `ref?` | New branch info |
| `git_checkout` | `ref`, `create?` | Checkout result |
| `git_delete_branch` | `name`, `force?` | Deletion result |
| `git_reset` | `ref`, `mode` | Reset result |
| `git_cherrypick` | `commit` | Cherry-pick result |
| `git_revert` | `commit` | Revert result |
| `git_stash_push` | `message?`, `include_untracked?` | Stash commit |
| `git_stash_pop` | `index?` | Pop result |
| `git_stash_list` | - | List of stash entries |
| `git_fetch` | `remote?`, `prune?` | Updated refs |
| `git_push` | `remote?`, `force?` | Push result |
| `git_pull` | `remote?` | Pull result |
| `git_rebase_continue` | - | Rebase state |
| `git_rebase_abort` | - | Abort confirmation |
| `git_rebase_skip` | - | Skip result |
| `git_submodules` | - | List with status |
| `git_submodule_init` | `paths?` | Init result |
| `git_submodule_update` | `paths?`, `recursive?` | Update result |
| `git_submodule_add` | `url`, `path`, `branch?` | New submodule info |
| `git_submodule_remove` | `path` | Removal result |
| `git_worktrees` | - | List of worktrees |
| `git_worktree_add` | `path`, `ref` | New worktree info |
| `git_worktree_remove` | `name`, `force?` | Removal result |

---

#### Refactor Tools (`refactor_*`)

Semantic refactoring via LSP.

**Parameters:**

```typescript
{
  action: "rename" | "move" | "delete" | "preview" | "apply" | "cancel";
  
  // For rename
  symbol?: string;                  // Symbol name or path:line:col
  new_name?: string;
  
  // For move
  from_path?: string;
  to_path?: string;
  
  // For delete
  target?: string;                  // Symbol or path
  
  // For apply/cancel
  refactor_id?: string;             // From preview response
  
  // Options
  include_comments?: boolean;       // Sweep comments/docs (default true)
  contexts?: string[];              // Specific contexts (default all)
  session_id?: string;
}
```

**Response:**

```typescript
{
  refactor_id: string;
  status: "previewed" | "applied" | "cancelled" | "divergence";
  preview?: {
    files_affected: number;
    edits: Array<{
      path: string;
      hunks: Array<{ old: string; new: string; line: number }>;
      semantic: boolean;            // LSP-driven vs comment sweep
    }>;
    contexts_used: string[];
  };
  applied?: {
    delta: MutationDelta;           // Same as mutate
    validation?: {
      diagnostics_before: number;
      diagnostics_after: number;
    };
  };
  divergence?: {
    conflicting_hunks: Array<{
      path: string;
      contexts: string[];
      hunks: Array<{ context: string; content: string }>;
    }>;
    resolution_options: string[];
  };
  _session: SessionState;
}
```

---

#### Test Tools (`test_*`)

Test discovery and execution.

**Parameters:**

```typescript
{
  action: "discover" | "run" | "status" | "cancel";
  
  // For discover
  paths?: string[];                 // Scope discovery
  
  // For run
  targets?: string[];               // Specific targets (default all)
  filter?: {
    pattern?: string;               // Test name pattern
    tags?: string[];                // Test tags/markers
    failed_only?: boolean;          // Re-run failures
  };
  parallelism?: number;             // Worker count (default auto)
  timeout_sec?: number;             // Per-target timeout
  fail_fast?: boolean;              // Stop on first failure
  
  // For status/cancel
  run_id?: string;
  
  session_id?: string;
}
```

**Response:**

```typescript
{
  action: "discover" | "run" | "status" | "cancel";
  
  // discover
  targets?: Array<{
    target_id: string;
    path: string;
    language: string;
    runner: string;
    estimated_cost: number;
    test_count?: number;
  }>;
  
  // run / status
  run_id?: string;
  status?: "running" | "completed" | "cancelled" | "failed";
  progress?: {
    total: number;
    completed: number;
    passed: number;
    failed: number;
    skipped: number;
  };
  results?: Array<{
    target_id: string;
    status: "passed" | "failed" | "skipped" | "error";
    duration_ms: number;
    failure?: {
      message: string;
      stack?: string;
      output?: string;
    };
  }>;
  summary?: {
    total_duration_ms: number;
    flaky: string[];
    new_failures: string[];
    fixed: string[];
  };
  
  _session: SessionState;
}
```

---

#### Session Tools (`session_*`)

Session and task lifecycle management.

**Parameters:**

```typescript
{
  action: "status" | "new" | "close" | "configure";
  
  // For new
  limits?: {
    max_mutations?: number;
    max_test_runs?: number;
    max_duration_sec?: number;
  };
  
  // For close
  reason?: "success" | "failed" | "abandoned";
  
  // For configure (update limits mid-task)
  limits_delta?: {
    add_mutations?: number;
    add_test_runs?: number;
  };
  
  session_id?: string;
}
```

**Response:**

```typescript
{
  action: "status" | "new" | "close" | "configure";
  task: {
    task_id: string;
    state: "OPEN" | "CLOSED_SUCCESS" | "CLOSED_FAILED" | "CLOSED_INTERRUPTED";
    limits: {
      max_mutations: number;
      max_test_runs: number;
      max_duration_sec: number;
    };
    counters: {
      mutations: number;
      test_runs: number;
      elapsed_sec: number;
    };
    fingerprints: {
      last_mutation: string | null;
      last_failure: string | null;
      repeated_failure_count: number;
    };
    timeline: Array<{
      timestamp: string;
      op_type: string;
      success: boolean;
      fingerprint?: string;
    }>;
  };
  _session: SessionState;
}
```

---

#### `status`

Daemon health, index state, and session info.

**Parameters:**

```typescript
{
  include?: Array<"daemon" | "index" | "session" | "lsp" | "config">;
  session_id?: string;
}
```

**Response:**

```typescript
{
  daemon: {
    version: string;
    uptime_sec: number;
    pid: number;
    port: number;
    memory_mb: number;
  };
  index: {
    version: number;
    commit: string;
    file_count: number;
    symbol_count: number;
    last_updated: string;
    overlay_files: number;
    healthy: boolean;
  };
  session: SessionState;
  lsp: {
    languages: Array<{
      language: string;
      server: string;
      status: "running" | "stopped" | "crashed" | "not_installed";
      memory_mb?: number;
    }>;
    pending_install: string[];
  };
  config: {
    repo_root: string;
    config_sources: string[];
    active_contexts: string[];
  };
  _session: SessionState;
}
```

---

### 22.8 REST Endpoints (Operator)

Non-MCP endpoints for operators and monitoring.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check (returns 200 if alive) |
| `/ready` | GET | Readiness check (returns 200 if index loaded) |
| `/metrics` | GET | Prometheus-format metrics (see section 13) |
| `/status` | GET | JSON status (same as `status` tool) |
| `/dashboard` | GET | Observability dashboard (see section 13) |

**Validation:** Same `X-CodePlane-Repo` header as MCP.

**Example:**

```bash
curl -H "X-CodePlane-Repo: $(cat .codeplane/repo)" \
     http://127.0.0.1:$(cat .codeplane/port)/health
```

---

### 22.9 Error Handling

All MCP tools use the error schema defined in section 4.2.

**MCP-specific error wrapping:**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32000,
    "message": "CodePlane error",
    "data": {
      "code": 4001,
      "error": "REFACTOR_DIVERGENCE",
      "message": "Contexts disagree on rename target",
      "retryable": false,
      "details": { ... },
      "_session": { ... }
    }
  }
}
```

**Budget exceeded handling:**

When task budget is exceeded, all subsequent mutating operations return:

```json
{
  "code": 6001,
  "error": "TASK_BUDGET_EXCEEDED",
  "message": "Mutation budget exceeded (20/20)",
  "retryable": false,
  "details": {
    "budget_type": "mutations",
    "limit": 20,
    "current": 20
  },
  "_session": { ... }
}
```

Client must close task and open new one, or configure additional budget.

---

### 22.10 MCP Server Configuration

CodePlane registers as an MCP server. Client configuration example:

**Claude Desktop / Cursor:**

```json
{
  "mcpServers": {
    "codeplane": {
      "transport": "http",
      "url": "http://127.0.0.1:${port}",
      "headers": {
        "Authorization": "Bearer ${token}"
      }
    }
  }
}
```

**Dynamic discovery:**

Clients can read `.codeplane/port` and `.codeplane/token` to configure automatically.

---

### 22.11 Versioning

- API version included in `/status` response
- Breaking changes increment major version
- Tools may gain optional parameters without version bump
- Deprecated tools return warning in `meta.warnings`

Current version: `1.0.0`
