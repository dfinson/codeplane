---
name: code-health
description: >
  Codebase health audit and cleanup. Use when asked to improve code quality,
  reduce technical debt, find dead code, fix god classes, remove duplication,
  improve naming, or do a general health pass on the codebase.
---

# Code Health Audit

Systematic process for finding and fixing real quality problems in a codebase.
No external tooling required beyond the project's own linters and test suites.

## When to use

- "Do a code quality pass" / "find and fix technical debt"
- "Clean up this module" / "reduce complexity in X"
- "Check for dead code / duplication / naming issues"

---

## Phase 1: Discover the project

Before touching anything, learn the toolchain. Answer these questions:

1. **Languages and frameworks** — read the root config files (package.json,
   pyproject.toml, Cargo.toml, go.mod, Makefile, etc.)
2. **Linter(s)** — what's configured? (eslint, ruff, clippy, golint, etc.)
3. **Formatter** — is there one? (prettier, ruff format, gofmt, etc.)
4. **Test runner** — how do you run the full suite? How long does it take?
5. **Type checker** — is there one? (tsc, mypy, pyright, etc.)
6. **Migration tool** — does the project use DB migrations? (alembic, knex,
   django, etc.)
7. **Package manager** — how are deps added? (npm, uv, cargo, go get, etc.)
8. **CI** — is there a CI config? What does it run?
9. **Existing conventions** — contribution guide, architecture docs, ADRs?

Don't assume. Read the files.

---

## Phase 2: Mechanical baseline

Run every available automated check. This is your before-snapshot.

```
<linter> [flags to show statistics or counts]
<linter> [flags to select unused-import / dead-code rules]
<formatter> --check [src]
<type-checker>
<test-runner> [quiet/summary mode]
```

Record the output. You'll diff against it at the end.

---

## Phase 3: Systematic scan

Don't just "read the code." Use concrete techniques to surface problems at scale.

### 3a. Find the biggest files

Large files are where debt accumulates. Start here.

```bash
find <src-dirs> -name '*.py' -o -name '*.ts' -o -name '*.tsx' -o -name '*.go' \
  | xargs wc -l | sort -rn | head -30
```

Any file >300 LOC deserves a skim. Any file >600 LOC almost certainly has
multiple responsibilities that should be split.

For classes specifically (Python example — adapt the pattern):
```bash
grep -rn 'class ' <src-dir> --include='*.py' | while read line; do
  file=$(echo "$line" | cut -d: -f1)
  lineno=$(echo "$line" | cut -d: -f2)
  total=$(wc -l < "$file")
  echo "$((total - lineno)) LOC remaining  $line"
done | sort -rn | head -20
```

### 3b. Find duplicated logic

Search for structural duplication — functions/methods that do the same thing in
different places.

**Same function name in multiple files:**
```bash
grep -rn 'def <suspect_name>\|function <suspect_name>' <src-dirs> --include='*.py' --include='*.ts'
```

**Similar patterns across files** — look for repeated idioms:
```bash
# Find all files that use the same pattern (adapt the regex)
grep -rln '<distinctive_code_fragment>' <src-dirs>
```

**Compare two methods you suspect are duplicates:**
Read both, diff them mentally. The shared parts become the helper; the different
parts become parameters, enums, or callbacks.

### 3c. Find API surface drift

If the project exposes the same data through multiple interfaces (REST + MCP,
REST + GraphQL, CLI + API, etc.), compare them:

```bash
# Find all response builders / serializers for a given entity
grep -rn 'def.*to_response\|def.*to_dict\|def.*serialize' <src-dirs>
```

Read each one. Are they returning the same fields? Same types? If not, one is
stale.

### 3d. Find wrong-layer code

**Functions on a class that don't use instance state:**
```bash
# Python: methods that never reference 'self' in their body
grep -n 'def ' <file> | while read line; do
  name=$(echo "$line" | grep -oP 'def \K\w+')
  # Check if self is used beyond the signature
  # (manual inspection — grep for self. usage in the method body)
done
```

In practice: skim service classes. If a method's only dependency is its
arguments (no `self.x` access), it doesn't belong on the class.

**Business logic in route handlers / controllers:**
```bash
# Find route handlers that are longer than ~15 lines
grep -rn '@app\.\|@router\.\|app\.get\|app\.post' <src-dirs> --include='*.py' --include='*.ts'
```

If a handler does more than validate → delegate → return, it has logic that
belongs in the service layer.

### 3e. Find hardcoded values

```bash
grep -rn 'version.*=.*"[0-9]\|"0\.\|"1\.\|"v[0-9]' <src-dirs>
grep -rn '[0-9]\{4,\}' <src-dirs>   # large numeric literals (byte limits, timeouts)
```

### 3f. Find type safety gaps

```bash
# Python
grep -rn ': Any\|-> Any\|# type: ignore' <src-dirs> --include='*.py'

# TypeScript
grep -rn ': any\|as any\|as unknown' <src-dirs> --include='*.ts' --include='*.tsx'
```

### 3g. Find dead code

Beyond what the linter catches:

```bash
# Find functions/classes that are only defined, never referenced elsewhere
grep -rn 'def <name>\|class <name>' <src-dirs>    # definition
grep -rn '<name>' <src-dirs> | grep -v 'def \|class \|import '  # usage
```

If a symbol is defined but never imported or called from another file, it may be
dead. Verify before removing — it could be used dynamically or via reflection.

---

## Phase 4: Cluster and prioritize

Group findings by relationship, not by type. Related issues should be fixed
together:

- "These 4 findings are all about the same god class" → one batch
- "These 3 are all stale API fields" → one batch
- "These 2 are the same function duplicated" → one batch

Order batches by impact, not count:

1. **Bug fixes** — incorrect behavior, impossible state transitions, crashes
2. **Structural** — god class splits, wrong-layer extractions, deduplication
3. **Mechanical** — dead code removal, unused imports, naming fixes
4. **Cosmetic** — skip unless trivial or you're already in the file

For each batch: know which files will change, what the test command is, and what
"done" looks like before you start.

---

## Phase 5: Execute

For each batch:

1. Make the changes
2. Run the **full** test suite and linter. Never skip this. Never run just the
   "related" tests — structural changes cause cascade failures in unexpected
   places.
3. If tests break, fix them immediately. Don't move to the next batch with
   failures.
4. Commit with a descriptive message (conventional commits: `refactor:`, `fix:`,
   `chore:`).

**Discipline:**
- One logical change per commit. Don't mix a bug fix with a refactor.
- Keep changes minimal. Extracting a class doesn't require reformatting the
  original file.
- Don't add abstractions, error handling, comments, types, or tests to code you
  didn't change.
- If you touch DB schema, create a migration using the project's tool.
- Add dependencies only through the project's package manager.

**When extracting code (god class → helpers):**
1. Copy the code to the new location
2. Update the original to call the new location
3. Run tests — they should pass without modification if the extraction is clean
4. If tests need updating, update imports/mocks — not logic
5. Commit

**When deduplicating:**
1. Identify the shared pattern and the caller-specific differences
2. Write the shared helper with the differences as parameters (enum, dataclass,
   callback — not boolean flags)
3. Replace both call sites
4. Run tests
5. Commit

---

## Phase 6: Iterate

Structural changes create cascading effects. After completing all batches:

1. **Re-run the scan** (Phase 3 commands). New issues will have surfaced —
   extracted modules may now be the new largest files, relocated code may expose
   new duplication.
2. **Compare with baseline** — re-run all linters, test suite, type checker.
   Are the numbers better? If not, something went wrong.
3. **Decide whether to do another pass.** Diminishing returns kick in fast.
   One or two passes catches the vast majority of real debt. Stop when the
   remaining findings are all cosmetic or judgment calls.

---

## When to stop

You're done when:
- No files >500 LOC with multiple responsibilities remain
- No functions are duplicated across modules
- All API surfaces return consistent shapes
- The linter count is at or below baseline
- The test suite passes
- The remaining findings are purely cosmetic or would require major architectural
  changes that aren't warranted

Don't chase perfection. The goal is to leave the codebase measurably better, not
to achieve some abstract score.

---

## What NOT to do

- Don't build tracking infrastructure for the cleanup itself.
- Don't score, rank, or dimension-review quality — just fix things.
- Don't refactor code you're not actively fixing.
- Don't add comments/types/docs to unchanged code.
- Don't design for hypothetical future requirements.
- Don't let a single extraction spiral into 30 files — if it's not clean in <5
  files, split into smaller batches.
- Don't skip the test suite. Ever. For any reason.

---

## Quick-reference: smell → fix

| Smell | Fix |
|-------|-----|
| Pure function on a class that doesn't use instance state | Move to module level or utility module |
| Two methods with >15 lines of near-identical code | Extract shared helper; parameterize the differences |
| Inline state transition buried in a nested method | Lift to caller; return structured result |
| Config/parsing logic mixed into business layer | Move to a config/utility module |
| One service doing another service's job | Move to the owning service; inject via constructor |
| Third-party SDK types leaking across boundaries | Wrap behind an interface; concrete types stay in the adapter |
| UI component >200 LOC with mixed concerns | Split into container + presentational |
| State selector creating new references every call | Memoize or use a selector factory |
| Hardcoded magic value (version, limit, timeout) | Named constant or config/metadata lookup |
| Multiple API surfaces with different shapes for same entity | Single source-of-truth response builder |
| Utility function copy-pasted across modules | One shared location, callers import it |

---

## Folder and module structure

When you extract code, you need to decide where it lives. Don't just dump
everything into a `utils/` folder. Follow the project's existing structure, and
if the structure is unclear, apply these principles:

### Recognize common layers

Most projects have some variant of these layers (names vary):

```
routes / controllers / handlers   → HTTP/CLI entry points. Thin: parse input, delegate, return.
services / use-cases              → Business logic and orchestration. No direct DB or I/O.
repositories / data-access        → Database queries and persistence.
adapters / clients / integrations → Third-party SDK wrappers. Isolate external dependencies.
models / domain / types           → Data structures, enums, state machines, validation.
config                            → Loading, parsing, building configuration objects.
```

Not every project has all of these, and the names differ. The point is: figure
out which layers *this* project has, and respect them when placing new code.

### Placement rules

**When extracting a pure function off a class:**
- If it's used only by that module → make it module-level in the same file.
- If it's used by 2+ modules → create a focused utility module near its callers
  (e.g. `services/url_utils.py`, not `utils/misc.py`).

**When splitting a god class:**
- Each extracted piece should correspond to one responsibility.
- Name the new module after the responsibility, not after the original class
  (e.g. `resume_prompt.py`, not `runtime_service_helpers.py`).
- The original class becomes a thin orchestrator that delegates to the new pieces.

**When you find shared logic between two sibling modules:**
- If both are in the same directory, add a private module in that directory
  (e.g. `services/_shared.py` or `services/common.ts`).
- If they're in different directories, put the shared code at the nearest common
  ancestor, or in a dedicated `lib/` or `core/` module if one exists.

**When wrapping a third-party SDK:**
- The adapter/wrapper goes in whatever `adapters/` or `integrations/` directory
  the project uses.
- The rest of the codebase imports only the interface, never the SDK types
  directly. If the language supports it, use conditional/type-only imports for
  the concrete types.

### Signs of bad structure

- A `utils.py` / `helpers.ts` file that's >200 LOC and growing — it's becoming a
  junk drawer. Split by domain.
- Circular imports between modules — usually means two modules are at the wrong
  layer, or shared types need to be lifted into a separate module.
- A directory with 15+ files and no subdirectories — consider grouping by feature
  or responsibility. But don't create folders with 1-2 files just for symmetry.
- Module names like `_helpers`, `_common`, `_misc` — these attract unrelated code
  over time. Name modules after what they *do*.
