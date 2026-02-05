# CodePlane — Copilot Instructions

Authority: SPEC.md wins. If unsure or there is a spec conflict, stop and ask.

1) MCP First (Default)
If a CodePlane MCP tool exists for an action, use it.
Terminal commands are fallback only when the tool does not exist.

- Read files: mcp_codeplane_read_files (not cat/head)
- Edit files: mcp_codeplane_atomic_edit_files (not sed/echo/awk)
- List files: mcp_codeplane_list_files (not ls/find)
- Map repo: mcp_codeplane_map_repo (not manual traversal)
- Search: CodePlane search tools (not grep/rg)
- Git ops: CodePlane git tools (not raw git)

2) Non-Negotiable Invariants
- Refactors are LSP-only (no regex, no guessing)
- No autonomous mutations (all reconciliation is triggered)
- Determinism over heuristics
- Structured outputs only (no raw text)
- Ledger is append-only (no updates or deletes)

3) No Hacks (Root Cause Only)
If something fails, diagnose and fix it properly. Do not “make it pass”.

Forbidden:
- # type: ignore, Any, dishonest cast()
- try/except or inline imports to dodge module issues
- regex or string parsing for structured data
- raw SQL to bypass ORM or typing
- empty except blocks or silent fallbacks
- “for now” workarounds

If you cannot solve it correctly with available tools or information, say so and ask.

4) All Checks Must Pass (Method-Agnostic)
Lint, typecheck, tests, and CI must be green.

- Prefer CodePlane MCP endpoints for lint/test/typecheck when available
- Terminal commands are acceptable only if MCP support does not exist
- The requirement is the result, not the invocation method

5) GitHub Remote Actions Must Be Exact
When asked to perform a specific remote action (merge, resolve threads, release, etc.):
- do exactly that action, or
- state it is not possible with available tools

No substitutions.

6) Change Discipline (Minimal)
- Before coding: read the issue, relevant SPEC.md sections, and match repo patterns
- Prefer minimal code; do not invent abstractions or reimplement libraries
- Tests should be small, behavioral, and parameterized when appropriate

7) Read MCP Response Hints
CodePlane MCP responses may include `agentic_hint`, `coverage_hint`, or `display_to_user` fields.
Always check for and follow these hints—they provide actionable guidance for next steps.

8) NEVER Reset Hard Without Approval
**ABSOLUTE PROHIBITION**: Never execute `git reset --hard` under any circumstances without explicit user approval.

This applies to:
- `git reset --hard` (any ref)
- `mcp_codeplane_git_reset` with `mode: hard`
- Any equivalent destructive operation that discards uncommitted changes

If you believe a hard reset is needed:
1. STOP and explain why you think it's necessary
2. List what uncommitted work will be lost
3. Wait for explicit user confirmation before proceeding

Violating this rule destroys work irreversibly and may affect parallel agent workflows.