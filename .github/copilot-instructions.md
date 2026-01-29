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

## ⛔ ABSOLUTE PROHIBITION: No Hacky Workarounds

**This is the most important section. Violating these rules is unacceptable.**

When you encounter ANY problem — type errors, lint failures, test failures, API mismatches, library incompatibilities — you MUST address it properly. There are NO exceptions.

### What Constitutes a Hacky Workaround (FORBIDDEN)

- **Type ignores to silence errors** — `# type: ignore`, `cast()` to lie about types, `Any` to escape type checking
- **Inline imports to avoid module-level issues** — If an import fails at module level, fix the real problem
- **Try/except around imports** — Dependencies are declared; if missing, that's a setup bug
- **Raw SQL to bypass ORM type issues** — If SQLModel types don't work, understand why and fix the model or query
- **String manipulation instead of proper parsing** — If you need to parse TOML, use a TOML parser
- **Regex to extract structured data** — Use the appropriate parser for the format
- **Casting return values to silence type checkers** — `int(some_any_value)` when you don't know what it is
- **Empty except blocks** — `except: pass` hides bugs
- **Fallback values that mask errors** — `value or default` when `value` being falsy indicates a bug
- **Conditional logic to work around library quirks** — If a library behaves unexpectedly, read its docs or find a different approach
- **Copy-pasting code because you can't figure out the abstraction** — Take the time to understand the pattern

### What You MUST Do Instead

1. **Stop and diagnose** — Understand WHY the error occurs, not just WHAT the error says
2. **Read documentation** — Library docs, type stubs, source code if needed
3. **Check existing patterns** — How does the rest of the codebase handle this?
4. **Question the approach** — Maybe the entire approach is wrong, not just the implementation
5. **Ask for clarification** — If you're unsure, say so. Don't guess and hack.
6. **Fix root causes** — If the model is wrong, fix the model. If the schema is wrong, fix the schema.
7. **Propose alternatives** — If proper solution A is blocked, propose proper solution B, not a hack

### Examples of Proper Problem Resolution

**Bad (hack):**
```python
# Type checker complains about Symbol.id being int | None
stmt = select(func.count(Symbol.id))  # type: ignore
```

**Good (proper):**
```python
# Symbol.id is Optional because SQLModel uses None before insert.
# Use func.count('*') which doesn't depend on column nullability.
stmt = select(func.count()).select_from(Symbol)
```

**Bad (hack):**
```python
try:
    import tantivy
except ImportError:
    tantivy = None  # type: ignore
```

**Good (proper):**
```python
# tantivy is a declared dependency in pyproject.toml
# If it fails to import, that's a broken environment - let it crash
import tantivy
```

**Bad (hack):**
```python
# SQLModel join doesn't type-check, use raw SQL
result = session.exec(text("SELECT * FROM symbols JOIN ..."))
```

**Good (proper):**
```python
# Understand SQLModel's join syntax and use it correctly
# If the ORM can't express it, reconsider the query design
stmt = select(Symbol).join(File, Symbol.file_id == File.id)
```

### The Fundamental Rule

**If your solution involves the words "workaround", "hack", "bypass", "silence", "ignore", or "for now" — STOP.**

Either:
1. Solve the problem correctly, or
2. Explicitly state "I cannot solve this properly because X" and ask for guidance

There is no third option. Quick fixes become permanent technical debt. Every hack you write, someone else has to maintain. Respect the codebase and the humans who work on it.

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

### 8. Anti-Overengineering Rules

**Default posture: Write less code.**

Before writing any abstraction, validator, or helper, ask: "Does a library already do this?"

**Forbidden patterns:**

- **Reimplementing library features** — If pydantic-settings handles env vars, don't write custom env parsing. If structlog has processors, don't wrap them. Read the library docs first.
- **Custom validators for standard types** — Use `Literal["A", "B", "C"]` not a custom normalizer. Use `Field(ge=0, le=65535)` not a validator method.
- **Migration/compatibility shorthands** — No `json_format: bool` that "converts to" the real config. Users can write the real config.
- **Verbose docstrings** — If the function is `_load_yaml(path) -> dict`, don't write "Load YAML file, returning empty dict if missing." The code is obvious.
- **Field descriptions that repeat the field name** — `port: int = Field(description="The port number")` adds nothing. Delete it.
- **Factory methods for simple constructors** — If `Error(code, message, details={...})` works, don't add `Error.from_x()` methods.

**Docstring rules:**

- One-line docstrings only, unless explaining non-obvious behavior
- No Args/Returns sections for obvious signatures
- No docstrings on private helpers with self-documenting names
- Class docstrings: one line stating purpose, not restating field names

**Test for overengineering:**

If you can delete code and tests still pass with equivalent coverage, the code was unnecessary.

### 9. Problem-Solving Standards

**Every problem requires proper understanding before any solution.**

When you encounter an error, failure, or unexpected behavior:

1. **Reproduce and isolate** — Can you create a minimal case?
2. **Read the error completely** — Stack traces, type errors, and lint messages contain information
3. **Trace the data flow** — Where does the unexpected value come from?
4. **Consult authoritative sources** — Library documentation, type stubs, source code
5. **Understand the design intent** — Why was the code written this way?

**Never:**
- Guess at solutions without understanding the problem
- Apply fixes that worked elsewhere without verifying they apply here
- Suppress errors instead of fixing their causes
- Add defensive code to "handle" situations that shouldn't occur
- Use escape hatches (`Any`, `# type: ignore`, `cast()`) to silence type checkers

**If you don't understand something, say so.** Admitting uncertainty is professional. Shipping hacks is not.

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

### Type System Respect

The type system exists to catch bugs. When mypy reports an error:

1. **The type checker is probably right** — Don't assume it's wrong
2. **Read the full error** — Mypy explains exactly what's incompatible
3. **Trace the types** — Where does the type mismatch originate?
4. **Fix the root cause** — Not the symptom

**Never acceptable:**
- `# type: ignore` without a specific error code and explanation
- `cast()` that lies about the actual runtime type
- `Any` to escape type checking
- `isinstance()` guards that paper over design flaws

**If the types don't work, the design might be wrong.** Type errors often reveal architectural problems. Listen to them.

---

## C. Pull Request Creation

When creating pull requests:

### 1. Template Usage

- **Always check for PR templates** in `.github/PULL_REQUEST_TEMPLATE.md` or `.github/PULL_REQUEST_TEMPLATE/`
- **Use the template structure** — do not discard or reformat it
- **Be context-sensitive with checkboxes** — only check boxes that actually apply to this PR
  - If a checkbox doesn't apply, leave it unchecked or mark N/A
  - Do not blindly check all boxes
  - Each checked box is a commitment

### 2. Description Guidelines

- **Stay high-level** — PR descriptions summarize intent, not implementation details
- **Link to issues** — Use `Closes #123` or `Relates to #123`
- **Avoid code in descriptions** — The diff is the code; the description is the context
- **State what changed and why** — not how (the diff shows how)

### 3. Scope Discipline

- One logical change per PR
- If a PR touches unrelated areas, split it
- Refactors and features should not mix

---

## D. GitHub Remote Operations

> **⚠️ CRITICAL: No Substitutions**
>
> When asked to perform a specific GitHub remote action (resolve threads, merge PR, create release, etc.):
> - Do **exactly** what was requested, or
> - Say "I cannot do that with the available tools"
>
> **Never** substitute a different action. If asked to resolve review threads and you can't, say so. Do not add a comment instead. Do not do something "close enough." Either perform the exact operation or report that it's not possible.

---

## Dev Commands

```bash
make dev         # Install with dev deps
make lint        # Ruff
make typecheck   # Mypy
make test        # Pytest
```
