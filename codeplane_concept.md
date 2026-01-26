# CodePlane — Specification Summary (Current State)

## 1. Problem Statement

Modern AI coding agents are not limited by reasoning ability.  
They are limited by **how they interact with repositories**.

The dominant sources of friction are:

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

Result:  
**Small fixes take 5–10 minutes instead of seconds**, due to orchestration and I/O inefficiency, not model capability.

---

## 2. Core Idea

Introduce a **local, always-on control plane** that sits beneath agents and turns a repository into a **deterministic, queryable system**.

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

It is a deterministic execution and retrieval layer.

---

## 4. Architecture Overview

### Components

1. CodePlane Daemon (Python)
   - Always-on
   - Maintains indexes
   - Owns file, git, test, and refactor operations
   - Exposes MCP endpoints

2. Agent Client
   - Copilot, Claude Code, Cursor, Continue, etc.
   - Uses MCP tools only
   - Never edits files or runs shell commands directly

3. Git
   - Authoritative history and audit layer
   - Primary signal for detecting external mutations

VS Code becomes a viewer, not a state manager.

---

## 5. Repository State Management

### Git-Centric Reconciliation

Git is the authoritative signal for tracked files.

Reconciliation occurs:
- On daemon startup
- Before/after every MCP endpoint call (cheap check)

Process:
- Detect changed tracked files via Git
- Incrementally reindex only affected files
- Correct even if server was down or files were edited externally

### File Watching

- No global file watchers required
- Optional narrow watchers for:
  - Current task working set
  - Untracked but relevant files

Correctness comes from Git reconciliation, not OS events.

---

## 6. Ignore Rules and Sensitive Files

### `.cplignore`

- Superset of `.gitignore`
- Defines what CodePlane never indexes
- Default exclusions:
  - venv
  - node_modules
  - .idea
  - build outputs
  - internal caches

### Two-Tier Index Model

#### Shared Index (Optional)
- Derived only from Git-tracked files
- Contains no secrets
- Built in CI and distributed as an artifact
- Never required to be committed to Git

#### Local Overlay Index
- Lives only on developer machine
- May include:
  - `.env`
  - local config
  - untracked source files
- Never shared or uploaded
- Used for local refactors without leaking secrets

---

## 7. Indexing and Retrieval (No Embeddings)

### Core Indexes

1. Lexical Index
   - Tantivy (BM25-style)
   - Identifiers, paths, imports
   - Optional comments/docstrings

2. Structural Metadata Store
   - SQLite (preferred) or DuckDB
   - Symbols, spans, imports, containment
   - Dependency edges

3. Graph
   - Import/mention graph
   - Bounded expansion
   - Deterministic and explainable

### Retrieval Pipeline

1. Lexical search
2. Graph expansion (bounded)
3. Deterministic reranking:
   - Exact matches
   - Fuzzy matches
   - Graph distance
   - File role (test vs src)
   - Optional recency

Replaces repeated grep and file opening.

---

## 8. Mental Map Endpoints (Embedding Replacement)

### Repo Map
Single call returns:
- Directory structure
- Language breakdown
- Packages/modules
- Entry points
- Test layout
- Dependency hubs
- Public surface summaries

### Symbol Search
- Definitions
- References
- Spans and usage counts

### Targeted Lexical Search
- Indexed
- Scoped
- Structured
- Deterministic

These endpoints replace exploratory thrash.

---

## 9. Deterministic Refactoring Primitives

IntelliJ-class refactors as MCP operations:

- Rename symbol
- Rename file
- Move file/module
- Delete element safely

Implementation:
- Prefer LSP (`textDocument/rename`) where available
- Fallback to structured lexical edits

All refactors:
- Produce atomic edit batches
- Provide previews
- Apply via CodePlane patch system
- Return full structured context

---

## 10. Git and File Operations (No Terminal)

### Git
- Local operations via `pygit2`
  - status, diff, blame, staging
- Remote operations via system git subprocess
  - fetch, pull, push (for credential compatibility)

Agents never run git commands directly.

### File Operations
- Native Python
- Atomic writes
- Hash-checked
- Scoped

---

## 11. Mutation Semantics (Critical Design Rule)

Every state-mutating MCP endpoint returns a **complete structured JSON delta**, including:

- Files changed
- Hashes before/after
- Diff stats
- Affected symbols
- Affected tests
- Updated repo state

This eliminates verification loops and follow-up calls.

---

## 12. Tests and Convergence

- Deterministic test planning
- Run only impacted tests by default
- Fail fast
- Structured failure output

Convergence controls:
- Iteration caps
- Repeated failure detection
- Forced strategy shifts
- Explicit reset semantics

---

## 13. Embeddings Policy

Embeddings are intentionally excluded from the core design.

Rationale:
- Agents can explore structure deterministically
- Embedding lifecycle cost is high
- Core value comes from indexing, structure, and execution

If added later:
- Optional
- Gated
- Partial
- Never foundational

---

## 14. What CodePlane Is

CodePlane is:
- A repository control plane
- A deterministic execution layer
- A structured context provider
- A convergence enforcer

It turns AI coding from slow and chaotic into **fast, predictable, and auditable** by fixing the system, not the model.
