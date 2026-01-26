# CodePlane Deterministic Refactor Engine — Final Scope Spec

## Purpose

Provide **IntelliJ‑class, deterministic refactoring** (rename / move / delete / change signature) across multi‑language repositories using **LSP as the sole semantic authority**, while preserving determinism, auditability, and user control.

This subsystem is **not** a general mutation engine. It is a narrowly scoped, high‑correctness refactor planner and executor.

---

## Core Principles

- **LSP‑only semantics**: all refactor planning is delegated to language servers.
- **Static configuration**: languages, environments, and roots are known at startup.
- **No speculative semantics**: CodePlane never guesses or heuristically resolves bindings.
- **No working tree mutation during planning**.
- **Single atomic apply** to the real repo.
- **Explicit divergence handling** when multiple semantic contexts disagree.
- **Optional subsystem**: enabled by default, configurable off.

---

## Supported Operations

- Rename symbol
- Rename / move file or module
- Safe delete symbol
- Change signature (where supported by LSP)

Each operation supports:
- Plan → preview → apply → rollback
- Structured diff output
- Deterministic re‑indexing after apply

---

## Definitions

### Context

A **context** is the minimal semantic world in which an LSP can correctly analyze and refactor part of the repository.

A context includes:
- Language + LSP server (type + version)
- Environment selector  
  - Python: interpreter / venv  
  - C#: solution + SDK  
  - Java: build root  
  - Go: module/workspace + tags
- Workspace roots
- Sparse‑checkout include paths

### Context Worktree

A **persistent Git worktree** used as an isolated sandbox per context.

Properties:
- Reset to a base commit **R** before each operation
- Sparse checkout to minimize I/O
- Bound to one warm LSP instance (optional but default)

---

## Refactor Modes

### Mode A — Single‑Context Repository

**When**  
One coherent environment per language (single solution, single interpreter, unified build graph).

**Plan**
- One context per language
- One persistent worktree + warm LSP

**Refactor flow**
1. Reset worktree to commit R
2. Ask LSP to compute refactor
3. Apply edits in worktree
4. Emit patch = `git diff R`
5. Apply patch once to real working tree (atomic)
6. Optional validation (diagnostics / build)

**Characteristics**
- Fastest path
- No merge logic
- Minimal orchestration

---

### Mode B — Multi‑Context Repository

**When**  
Multiple incompatible environments exist (multiple solutions, interpreters, modules, samples, apps).

**Plan**
- N contexts per language
- One worktree + warm LSP per context
- Refactor protocol: *compute in sandboxes, merge patches, apply once*

**Refactor flow**
1. Select target contexts
2. For each context (parallel, bounded):
   - Reset worktree to R
   - Run LSP refactor
   - Emit patch `Pi`
3. Merge patches:
   - Disjoint edits → union
   - Identical overlapping edits → de‑dup
   - Differing overlapping edits → **divergence**
4. If no divergence:
   - Apply merged patch atomically to real repo
5. Optional per‑context validation

---

## Divergence Handling

Default behavior: **fail and report**.

When divergence occurs:
- Return structured result:
  - Conflicting hunks
  - Context IDs
  - Diagnostics (if available)

Optional (off by default):
- Deterministic resolution policy (e.g. primary context wins)
- Accepted only if validation passes in all contexts

CodePlane **never silently guesses semantics**.

---

## Context Selection Rules

Minimum set:
- Context owning the definition file
- Contexts including known dependents (from index / config)

If uncertain:
- Run all contexts for that language (bounded by config)

---

## Context Detection at Init

### Principle
Best‑effort and safe. Escalate to explicit config when ambiguous.

### Detection signals
- .NET: multiple `.sln`
- Java: multiple independent `pom.xml` / `build.gradle`
- Go: multiple `go.mod` not unified by `go.work`
- Python: multiple env descriptors in separate subtrees

### Classification
- Single context → single‑context mode
- Multiple valid roots → multi‑context mode
- Ambiguous → require explicit config

### Persistence
Detected contexts are stored in:
```
.codeplane/contexts.json
```
(versioned schema)

---

## Configuration Model (Minimal)

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

---

## Git‑Aware File Moves

- Tracked files: `git mv` equivalent
- Untracked / ignored files: filesystem move only
- Preserves history, never commits

---

## Comment and Documentation References

LSP refactors do **not** modify:
- Comments
- Docstrings
- Markdown / docs

CodePlane performs a **separate non‑semantic sweep**:
- Exact string matches
- Reported as optional, previewable patch set
- Never mixed with semantic edits

---

## Optional Subsystem

Enabled by default.

Disable via config:
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
- Indexing and generic mutation remain available

---

## Guarantees

Always:
- No working tree mutation during planning
- Single atomic apply
- Explicit divergence reporting
- Deterministic outputs

Best‑effort:
- Validation reporting
- Coverage limited to successfully loaded contexts

---

## Refactor Endpoint Results

- **Applied**
  - Merged patch
  - Contexts used
  - Optional validation results
- **Divergence**
  - Conflicting hunks
  - Contexts involved
  - Diagnostics
- **InsufficientContext**
  - No viable context loaded
  - Explicit configuration required
