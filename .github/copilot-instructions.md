# CodePlane — Copilot Instructions

Authority: SPEC.md wins. If unsure or there is a spec conflict, stop and ask.

## ⛔ CRITICAL: Use `checkpoint` — NEVER Terminal ⛔

**After ANY code change**, you MUST call:
- `checkpoint(changed_files=[...], commit_message="...", push=True)` — lint + test + commit + push + semantic diff

**FORBIDDEN alternatives** (these are WRONG, do NOT use them):
- `pytest`, `python -m pytest`, `ruff`, `ruff check`, `ruff format`, `mypy` in terminal
- `git add`, `git commit`, `git push` in terminal
- Any test runner or linter invoked via `run_in_terminal`

The `checkpoint` tool runs lint + affected tests + commit + push in one call.
Terminal commands for these operations are ALWAYS wrong in this repo.

## ⛔ CHECKPOINT IS A BLOCKING GATE ⛔

`checkpoint`, `semantic_diff`, and `map_repo` are **exclusive tools**.
The server enforces a session lock — no other tool runs concurrently.

**After checkpoint returns, you MUST fully process the result before ANY other work:**
1. Read `passed` (true/false) — stop here if false
2. Read `lint` section — check for errors in YOUR changed files
3. Read `tests` section — check for failures
4. Read `commit` section — confirm commit/push status
5. Read `agentic_hint` — follow its instructions
6. ONLY THEN resume other work

**Lint/test failures are YOUR fault.** You passed `changed_files` — checkpoint runs
lint and tests ONLY on code affected by those files. Do NOT dismiss failures as
"pre-existing" or "not related to my changes". They almost certainly ARE your changes.
Errors in OTHER files are often transitive — caused by your changes breaking
downstream dependents. These are still YOUR responsibility. Fix ALL issues.

**If checkpoint fails or you cannot read the full result: STOP and tell the user.**
Do NOT continue editing files after a failed or unread checkpoint.

## ⛔ E2E Tests Are OFF-LIMITS ⛔

**DO NOT RUN E2E TESTS** unless ALL of the following are true:
1. The user has EXPLICITLY requested E2E tests by name
2. You have explained the cost (clones real repos, starts daemons, takes minutes, high CPU)
3. The user has CONFIRMED they want to proceed

E2E tests (`tests/e2e/`) are:
- **Excluded by default** from `pytest` runs
- **Resource-intensive**: Clone real GitHub repos, start CodePlane daemons
- **Slow**: Can take 5-15+ minutes and consume significant CPU
- **NOT for routine validation**: Use `checkpoint` instead

**To run E2E tests (ONLY with explicit user confirmation):**
- `pytest tests/e2e/ --ignore=` (override the default ignore)

**Violating this wastes user resources and disrupts their workflow.**

---

1) Non-Negotiable Invariants
- Refactors are index-based (no regex, no guessing)
- No autonomous mutations (all reconciliation is triggered)
- Determinism over heuristics
- Structured outputs only (no raw text)
- Ledger is append-only (no updates or deletes)

2) No Hacks (Root Cause Only)
If something fails, diagnose and fix it properly. Do not "make it pass".

Forbidden:
- # type: ignore, Any, dishonest cast()
- try/except or inline imports to dodge module issues
- regex or string parsing for structured data
- raw SQL to bypass ORM or typing
- empty except blocks or silent fallbacks
- "for now" workarounds

If you cannot solve it correctly with available tools or information, say so and ask.

3) All Checks Must Pass (Method-Agnostic)
Lint, typecheck, tests, and CI must be green.

- Prefer CodePlane MCP endpoints for lint/test/typecheck when available
- Terminal commands are acceptable only if MCP support does not exist
- The requirement is the result, not the invocation method

4) GitHub Remote Actions Must Be Exact
When asked to perform a specific remote action (merge, resolve threads, release, etc.):
- do exactly that action, or
- state it is not possible with available tools

No substitutions.

5) Change Discipline (Minimal)
- Before coding: read the issue, relevant SPEC.md sections, and match repo patterns
- Prefer minimal code; do not invent abstractions or reimplement libraries
- Tests should be small, behavioral, and parameterized when appropriate

6) Read MCP Response Hints
CodePlane MCP responses may include `agentic_hint`, `coverage_hint`, or `display_to_user` fields.
Always check for and follow these hints—they provide actionable guidance for next steps.

7) NEVER Reset Hard Without Approval
**ABSOLUTE PROHIBITION**: Never execute `git reset --hard` under any circumstances without explicit user approval.

This applies to:
- `git reset --hard` (any ref)
- Any equivalent destructive operation that discards uncommitted changes

If you believe a hard reset is needed:
1. STOP and explain why you think it's necessary
2. List what uncommitted work will be lost
3. Wait for explicit user confirmation before proceeding

Violating this rule destroys work irreversibly and may affect parallel agent workflows.

## Benchmarking (cpl-bench)

The benchmarking script lives at `benchmarking/cpl_bench/setup_and_run.py`.
It handles CodePlane init, daemon lifecycle, and EVEE evaluation in one command.

```bash
# Basic run (recon experiment, port 7777, no reindex)
python benchmarking/cpl_bench/setup_and_run.py /path/to/target/repo

# Choose experiment
python benchmarking/cpl_bench/setup_and_run.py /path/to/repo --experiment agent-ab

# Custom port and timeout
python benchmarking/cpl_bench/setup_and_run.py /path/to/repo --port 8888 --timeout 180

# Force full reindex (deletes .codeplane/ first)
python benchmarking/cpl_bench/setup_and_run.py /path/to/repo --reindex
```

**Key flags:**
- `--experiment {recon,agent-ab}` — which evaluation to run (default: `recon`)
- `--port PORT` — daemon port (default: `7777`)
- `--timeout SECS` — MCP call timeout (default: `120`)
- `--reindex` — delete `.codeplane/` and rebuild the index from scratch (default: off)

The script automatically kills the daemon when the experiment finishes.

For CodePlane MCP tool usage instructions, see [AGENTS.md](../AGENTS.md).
