# CodePlane — Unified System Specification (Pre-API)

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

## 4. Architecture Overview (Pre-API)

### 4.1 Components

- **CodePlane daemon (Python)**
  - Maintains deterministic indexes.
  - Owns file, Git, test, and refactor operations.
  - Exposes endpoints (API/MCP design is out of scope here; only the existence of tool-like primitives is assumed).

- **Agent client**
  - Copilot, Claude Code, Cursor, Continue, etc.
  - Uses CodePlane tools only.
  - Never edits files or runs shell commands directly.

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
| Fetch shared index | Automatic and internal as part of `up` when policy dictates. |
| Rebuild index | Automatic when integrity checks fail (with clear policy and warning). Hidden escape hatch: `cpl debug index-rebuild`. |
| Upgrade | Removed entirely. Prefer package manager / installer. Self-updater deferred until security posture hardens. |

Daemon model:

- Default: **repo-scoped daemon** — one daemon per repository.
- `cpl up` in a repo directory starts/ensures a daemon for that repo only.
- No global multi-repo daemon in v1. Cross-repo features are out of scope; if added later, architecture will be revisited.
- Transport: Unix domain socket at `.codeplane/daemon.sock` (Linux/macOS) or named pipe (Windows). No port allocation conflicts.
- Isolation rationale:
  - Failure in one repo cannot affect another.
  - Version skew between repos is not a problem.
  - CI and local dev work identically.
  - Aligns with spec's determinism-first philosophy.

Repo activation:

- Repo must be initialized via `cpl init`.
- Creates `.codeplane/`, repo UUID, config, socket path.
- Index is lazily built or fetched.

Auto-start options (optional):

- Manual: `cpl up` (recommended; explicit is better)
- OS user-service integration deferred: repo-scoped daemons don't fit the "one global service" pattern cleanly. If needed, users can script `cpl up` in shell init or use a process manager.

Daemon startup includes:

- Git HEAD verification
- Overlay index diff
- Index consistency check

Daemon shutdown:

- Flushes writes
- Releases locks
- Leaves repo unchanged

Installation and upgrades:

- Install modes (user-level only; no root/system install):
  - `pipx install codeplane`
  - Static binary from GitHub Releases
  - Optional: Homebrew, Winget
- Upgrades:
  - Via package manager or installer (no CLI self-updater in v1)
  - No auto-updates
  - Safe hot-restart of daemon via `cpl down` / `cpl up`
  - Index artifacts forward-compatible if schema unchanged

Diagnostics and introspection:

- `cpl doctor` checks:
  - Daemon reachable
  - Index integrity (shared + overlay)
  - Commit hash matches Git HEAD
  - Port/socket availability
  - Config sanity
  - Git clean state
- `cpl doctor --logs`: bundled diagnostic report including recent logs
- Runtime introspection:
  - `cpl status --verbose`: includes last N log lines and paths
  - `cpl status --json`: machine-readable index metadata (paths, size, commit, overlay state)
  - `cpl status --follow`: optional alias for tailing logs (not a stable interface)
  - Healthcheck endpoint exists (`/health`) returning JSON (interface details deferred)

Shared index artifact handling:

- CI builds shared index artifact (tracked-only; no secrets).
- Hosted on GitHub Releases, S3, or Azure Blob.
- Downloaded automatically as part of `cpl up` when policy dictates.
- Stored in local cache, checksum-verified.
- Replaces shared index if commit is newer.
- Overlay never included; always local.

Config precedence:

1. One-off overrides via `cpl up --set key=value` / env vars
2. Per-repo: `.codeplane/config.yaml`
3. Global: `~/.config/codeplane/config.yaml`
4. Built-in defaults

No dedicated config CLI in v1. Edit files directly.

Defaults prevent footguns:

- `.cplignore` auto-generated
- Dangerous paths excluded
- Overlay disabled by default in CI

Failure recovery playbooks:

| Failure | Detection | Recovery Command |
|---|---|---|
| Corrupt index | `cpl doctor` fails hash check | Automatic rebuild (or `cpl debug index-rebuild`) |
| Schema mismatch | Startup error | Automatic rebuild on `cpl up` |
| Stale socket | `cpl up` error (socket exists but daemon unreachable) | Automatic cleanup and restart on `cpl up` |
| Stale revision | `cpl status` shows mismatch | Automatic re-fetch/rebuild on `cpl up` |
| Daemon crash | Daemon auto-exits | `cpl up` (restarts daemon) |

Platform constraints:

- Sockets:
  - Unix: domain socket default
  - Windows: named pipe fallback
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
| 2. CPL-tracked (Git-ignored) | `.cplignore` opt-in | CPL overlay index | Yes (stat + hash) | Yes (local only) |
| 3. Ignored | `.cplignore` hard-excluded | None | No | No |

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
- Overlay and shared indexes are deterministic and reproducible.
- Operation history is append-only (SQLite-backed).
- No automatic retries or implicit mutations.

### 6.10 Documented Contradiction: Overlay may include `.env` vs `.cplignore` default blocks `.env`

One source explicitly states local overlay may include `.env` and local config.
Another source’s `.cplignore` default explicitly blocks `.env` from indexing even locally.

This is preserved as a design contradiction and is tracked in the risk register. Until resolved, implementations must choose a safe default (security posture suggests default-blocking `.env`), but the contradiction remains an explicit open item.

---

## 7. Indexing & Retrieval Architecture (Lexical + Structural + Graph)

### 7.1 Overview

CodePlane builds a deterministic, incrementally updated hybrid index with:

- Fast lexical search engine (Tantivy) for identifiers, paths, tokens.
- Structured metadata store (SQLite; “preferred” mentioned alongside DuckDB in one place).
- Dependency and symbol graph for bounded, explainable expansions.

Indexing split:

- Shared tracked index (Git-tracked files; CI-buildable; distributable artifact)
- Local overlay index (untracked/sensitive files; local-only)

No embeddings required. Normal load target is <1s response.

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
- Languages: 10+ bundled grammars (~10 MB total), version-pinned
- Failure mode: “If grammar fails or file unsupported, crash gracefully and skip”
  - This phrase is preserved as authored but is internally ambiguous; tracked as a risk.
- No fallback tokenization — lexical index handles fuzzy matching
- Not sufficient for cross-file refactors; requires LSP

### 7.5 LSP Support (Indexing Context)

- Usage: only for semantic refactors (rename symbol, move module)
- Integration:
  - Bundled tree-sitter grammars
  - Dynamic LSP binary install via setup wizard (opt-in)
  - Per-language cache under `~/.codeplane/lsp/` keyed by lang+version (intentionally shared across repos; these are tooling binaries, not repo state)
- Invocation: async subprocesses via JSON-RPC per language; isolated and optional

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

This replaces repeated grep and file opening.

### 7.10 Mental Map Endpoints (Embedding Replacement, Pre-API Concept)

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

### 7.11 Startup Wizard & Extensibility

Initial `codeplane init` prompts:

- Detect dominant languages
- Offer to download language-specific LSPs
- Set preferences for grammar overrides and expansion heuristics

Dynamic grammar management update:

- Not just init: detect new language usage during incremental updates or file additions
- Dynamically download/register grammars and/or LSPs as needed on-the-fly
- Update grammar registry in cache without requiring full reinit

Future: language packs via versioned plugin registry (no wheel bloat).

---

## 8. Deterministic Refactor Engine (LSP-Only, Single vs Multi Context)

### 8.1 Purpose

Provide IntelliJ-class deterministic refactoring (rename / move / delete / change signature) across multi-language repositories using **LSP as the sole semantic authority**, preserving determinism, auditability, and user control.

This subsystem is narrowly scoped: a high-correctness refactor planner and executor.

### 8.2 Core Principles

- LSP-only semantics: all refactor planning delegated to language servers.
- Static configuration: languages, environments, roots known at startup.
- No speculative semantics: CodePlane never guesses bindings.
- No working tree mutation during planning.
- Single atomic apply to the real repo.
- Explicit divergence handling when multiple semantic contexts disagree.
- Optional subsystem: enabled by default, configurable off.

### 8.3 Supported Operations

- Rename symbol
- Rename/move file or module
- Safe delete symbol
- Change signature (where supported by LSP)

Each supports:

- Plan → preview → apply → rollback
- Structured diff output
- Deterministic re-indexing after apply

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

### 8.5 Refactor Modes

Mode A: Single-context repo

When: one coherent environment per language.

Plan:

- One context per language
- One persistent worktree + warm LSP

Flow:

1. Reset worktree to commit R
2. Ask LSP to compute refactor
3. Apply edits in worktree
4. Emit patch = `git diff R`
5. Apply patch once to real working tree (atomic)
6. Optional validation (diagnostics / build)

Mode B: Multi-context repo

When: multiple incompatible environments exist.

Plan:

- N contexts per language
- One worktree + warm LSP per context
- Compute in sandboxes, merge patches, apply once

Flow:

1. Select target contexts
2. For each context (parallel, bounded):
   - Reset worktree to R
   - Run LSP refactor
   - Emit patch Pi
3. Merge patches:
   - Disjoint edits → union
   - Identical overlapping edits → de-dup
   - Differing overlapping edits → divergence
4. If no divergence:
   - Apply merged patch atomically to real repo
5. Optional per-context validation

### 8.6 Divergence Handling

Default: fail and report.

On divergence return structured result:

- Conflicting hunks
- Context IDs
- Diagnostics if available

Optional (off by default):

- Deterministic resolution policy (primary context wins)
- Accepted only if validation passes in all contexts

CodePlane never silently guesses semantics.

### 8.7 Context Selection Rules

Minimum set:

- Context owning the definition file
- Contexts including known dependents (from index/config)

If uncertain:

- Run all contexts for that language (bounded by config)

### 8.8 Context Detection at Init

Principle: best-effort and safe; require explicit config when ambiguous.

Signals:

- .NET: multiple `.sln`
- Java: multiple independent `pom.xml` / `build.gradle`
- Go: multiple `go.mod` not unified by `go.work`
- Python: multiple env descriptors in separate subtrees

Classification:

- Single context → single-context mode
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
    worktree_scope_paths: [./core]
    env:
      build_root: ./core
    lsp:
      server: jdtls
      version: pinned

defaults:
  max_parallel_contexts: 4
  divergence_behavior: fail
  validation: diagnostics
```

### 8.10 Git-Aware File Moves

- Tracked files: `git mv` equivalent
- Untracked/ignored files: filesystem move only
- Preserves history; never commits

### 8.11 Comments and Documentation References

LSP refactors do not modify:

- Comments
- Docstrings
- Markdown / docs

CodePlane performs a separate non-semantic sweep:

- Exact string matches
- Reported as optional previewable patch set
- Never mixed with semantic edits

### 8.12 Optional Subsystem Toggle

Enabled by default. Disable via config:

```yaml
refactor:
  enabled: false
```

Or CLI:

```bash
cpl up --no-refactor
```

When disabled:

- No LSPs launched
- No refactor endpoints
- Indexing and generic mutation remain

### 8.13 Guarantees + Result Types (Pre-API Concept)

Always:

- No working tree mutation during planning
- Single atomic apply
- Explicit divergence reporting
- Deterministic outputs

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

### 9.8 LSP and Edit Planning

- All semantic refactors sourced from LSP (`textDocument/rename`, etc.).
- No fallback to internal symbol index for semantic edit planning.
- Structured reducers (non-LSP) must output the same enriched schema.
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

- Local operations via `pygit2`:
  - status, diff, blame, staging
- Remote operations via system git subprocess:
  - fetch, pull, push (credential compatibility)

Agents never run git commands directly.

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

### 11.5 Language-Specific Targeting Rules

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
| Rust, Ruby, etc. | File/Module/Project | Language-dependent | Custom adapter logic |

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

Purpose:

- Mechanical accountability: what happened, order, limits, effects
- Not observability, not surveillance

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

## 13. “Deterministic Refactoring Primitives” (Summary-Level Capability List)

This section preserves the explicit capability list and notes its relationship to the refactor engine constraints.

Refactors described as tool operations:

- Rename symbol
- Rename file
- Move file/module
- Delete element safely

Implementation statement (as authored in one source):

- Prefer LSP (`textDocument/rename`) where available
- Fallback to structured lexical edits

All refactors:

- Produce atomic edit batches
- Provide previews
- Apply via CodePlane patch system
- Return full structured context

Conflict note:

- The refactor-engine spec requires LSP-only semantics for semantic refactors and forbids semantic guessing.
- Therefore any “structured lexical edits” fallback must be interpreted as:
  - non-semantic edits, or
  - explicit opt-in mechanical transformations, or
  - documentation/comment sweep (exact string match), or
  - otherwise treated as an unresolved contradiction (tracked in risks).

---

## 14. Embeddings Policy

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

## 15. Subsystem Ownership Boundaries (Who Owns What)

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

## 16. Resolved Conflicts (Previously Open)

The following contradictions have been resolved:

1. **`.env` overlay indexing**: Resolved. Default-blocked in `.cplignore`. Users can explicitly whitelist via `!.env` if needed. See section 6.10.

2. **Refactor fallback semantics**: Resolved. Semantic refactors are LSP-only; CodePlane never guesses bindings. "Structured lexical edits" refers only to non-semantic operations (exact-match comment sweeps, mechanical file renames). These are explicitly not semantic refactors.

3. **Tree-sitter failure policy**: Resolved. On parse failure, skip file, log warning, continue indexing. Never abort the indexing pass for a single file failure. See section 7.4.

4. **"Always-on" framing vs explicit lifecycle**: Resolved. CodePlane is conceptually a control plane, operationally a repo-scoped daemon managed via `cpl up` / `cpl down`. OS service integration is deferred.

---

## 17. Risk Register (Remaining Design Points)

Items 1-3 from the original register have been resolved (see section 16). Remaining items:

1. Multi-context scaling:
   - context explosion risk
   - warm LSP resource footprint
   - operational limits beyond `max_parallel_contexts` not fully specified
2. Shared index artifact schema drift:
   - strict compatibility and rebuild rules must be enforced
3. Optional watchers:
   - must never become correctness-critical
   - must not violate "no background mutation"
4. Security posture depends on Git hygiene:
   - secrets committed to Git leak into shared artifacts by definition; mitigations are external (pre-commit hooks, scanning)

---

## 18. Readiness Note: What Is Stable Enough for API Surfacing Next

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
- Shared index artifact fetch and verification rules
- Config layering and defaults framework

All previously-open contradictions have been resolved. API surfacing can proceed.

---

## 19. What CodePlane Is (Canonical Summary)

CodePlane is:

- A repository control plane
- A deterministic execution layer
- A structured context provider
- A convergence enforcer

It turns AI coding from slow and chaotic into fast, predictable, and auditable by fixing the system, not the model.
