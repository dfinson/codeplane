# CodePlane

Local repository control plane for AI coding agents. Exposes repository operations via MCP tools over HTTP.

**Status:** Design + scaffolding phase. See `SPEC.md` for authoritative design.

## Technology Stack

- **CLI:** Click
- **HTTP:** Starlette + Uvicorn
- **Index:** Tantivy + SQLite + Symbol Graph
- **Refactor:** LSP-only (no regex, no guessing)
- **Logging:** structlog
- **Observability:** OpenTelemetry + Phoenix

## Architecture Invariants

These are non-negotiable design principles. Do not suggest alternatives:

- **LSP-only refactoring** — CodePlane never guesses symbol bindings. All semantic refactors go through LSP.
- **No background mutations** — Reconciliation is triggered, never autonomous.
- **Determinism over heuristics** — If it can be computed, don't infer it.
- **Structured responses always** — Every operation returns structured context, not raw text.
- **Ledger is append-only** — Never update or delete ledger records.

---

## A. Development Workflow

When implementing features, fixing bugs, or making changes:

### 1. Context Gathering

- **Fetch the relevant GitHub issue** — Read acceptance criteria and linked discussions
- **Read the relevant SPEC.md section(s)** — The spec is authoritative; implementation follows spec
- **Check existing code patterns** — Match conventions already established in the codebase

### 2. Branch Workflow

Create or checkout a branch following this naming convention:

```
<gh-username>/<type>/<short-description>
```

Types: `feature`, `bug`, `doc`, `spike`, `refactor`, `chore`

Examples:
- `dfinson/feature/otel-traces`
- `dfinson/bug/lsp-timeout-handling`
- `dfinson/spike/tantivy-perf`

### 3. Design Before Implementation

**Always evaluate 2-3 design options before writing code.**

- Present tradeoffs explicitly
- **Treat LOC as a first-class cost** — More code means more review, more tests, more maintenance
- Prefer concise and elegant over verbose and explicit
- Ask which approach to proceed with
- If spec is silent or ambiguous, propose spec amendments before implementing

### 4. Spec Alignment

- If implementation reveals a spec gap, **propose a spec update first**
- If implementation conflicts with spec, **stop and clarify** — do not silently diverge
- Reference spec sections in commit messages and PR descriptions

### 5. Commit Discipline

- Atomic commits with clear messages
- Prefix: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
- Reference issue numbers: `feat: add otel traces (#115)`

### 6. Testing Discipline

- Target 90%+ coverage, but **quality over quantity**
- Use **Given/When/Then** (BDD) or **Arrange/Act/Assert** (AAA) structure
- **Parametrize** tests where possible — one test with 10 cases beats 10 copy-paste tests
- Tests must be reviewable — no thousands of LOC that humans will never read
- Test behavior and outcomes, not implementation details

### 7. Comment Policy

Comments are a maintenance liability. Apply these rules strictly:

- **Minimize comments** — Code should be self-documenting through clear naming and structure
- **Never use comments as a changelog** — Git history exists; comments like "Added 2026-01-15" or "Changed per review" are forbidden
- **No commented-out code** — Delete it; Git remembers
- **Acceptable comments:**
  - Non-obvious "why" explanations (not "what")
  - Regulatory/compliance references
  - Links to external specs or RFCs
  - TODO with issue number: `# TODO(#123): handle edge case`

---

## B. Code Review Guidance

Copilot performs well on narrow, locally-visible issues. It struggles with architecture, context, and judgment calls.

### What to Prioritize (Copilot strengths — lean in)

- **Injection vulnerabilities** — shell, eval, SQL, path traversal
- **Cross-platform bugs** — Windows paths, CRLF, case sensitivity
- **Race conditions** — especially in daemon lifecycle and LSP management
- **Type mismatches** — signature inconsistencies, None handling
- **Error handling gaps** — unhandled exceptions, missing rollback

### What to Flag Conservatively (Copilot weaknesses — compensate)

- **Destructive operations** — file deletion, ledger writes, Git mutations
  - Require explicit rollback/recovery paths
  - Check for partial failure scenarios
  - Verify atomicity guarantees

- **State corruption risks** — index inconsistency, orphaned locks, stale caches
  - These are high-severity; prefer false positives

- **Architecture violations** — anything that bypasses LSP, mutates in background, or breaks structured response contracts

### What NOT to Suggest

- Removing debug output from tests (CI needs visibility)
- Premature abstraction ("extract this to a utility")
- Excessive documentation for self-evident code
- "Consider using X library" without concrete justification
- Logging changes in hot paths without perf context

### High-Risk Paths (Extra Scrutiny)

| Path | Risk |
|------|------|
| `src/codeplane/mutation/` | Data loss, partial writes |
| `src/codeplane/refactor/` | Incorrect symbol resolution |
| `src/codeplane/daemon/` | Lifecycle bugs, resource leaks |
| `src/codeplane/ledger/` | Audit integrity, append-only violation |
| `src/codeplane/index/` | Stale data, corruption |

### Review Anti-Patterns to Avoid

- Line-level nitpicks on large architectural PRs (focus on design)
- Suggesting changes that violate spec invariants
- Overengineering suggestions for spike/prototype branches
- Context-free "best practice" recommendations

---

## Dev Commands

```bash
make dev         # Install with dev deps
make lint        # Ruff
make typecheck   # Mypy
make test        # Pytest
```
