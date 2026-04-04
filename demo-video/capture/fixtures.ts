/**
 * Mock API response fixtures for Playwright capture.
 *
 * All data mirrors the exact shapes from backend/models/api_schemas.py
 * and matches the scenario described in SCREENSHOTS_NEEDED.md:
 * 10+ jobs across 2 repos, mixed agents, mixed states.
 */

const BASE_TIME = new Date("2026-03-31T14:30:00Z");
function minutesAgo(n: number) {
  return new Date(BASE_TIME.getTime() - n * 60_000).toISOString();
}

// ---------------------------------------------------------------------------
// Jobs (non-archived, visible on dashboard)
// ---------------------------------------------------------------------------

const JOB_DEFAULTS = {
  baseRef: "main",
  worktreePath: null,
  permissionMode: "full_auto",
  prUrl: null,
  mergeStatus: null,
  resolution: null,
  archivedAt: null,
  failureReason: null,
  progressHeadline: null,
  progressSummary: null,
  worktreeName: null,
  verify: null,
  selfReview: null,
  maxTurns: null,
  verifyPrompt: null,
  selfReviewPrompt: null,
  parentJobId: null,
  modelDowngraded: false,
  requestedModel: null,
  actualModel: null,
  completedAt: null,
  conflictFiles: null,
  resolutionError: null,
};

export const REPO_ISSUE_TRACKER = "/home/dev/repos/demo-issue-tracker-api";
export const REPO_SUPPORT_DASH = "/home/dev/repos/demo-support-dashboard";

export const mockJobs = [
  // ── In Progress column ──────────────────────────────────────────────
  {
    ...JOB_DEFAULTS,
    id: "j-0006",
    state: "running",
    title: "Persist the selected status filter in the URL query string",
    prompt: "Persist the selected status filter in the URL query string so the view survives page refreshes",
    repo: REPO_SUPPORT_DASH,
    sdk: "copilot",
    branch: "cpl/j-0006",
    model: "gpt-4o",
    progressHeadline: "Writing filter serialization logic",
    progressSummary: "Serializing active filters to URL search params",
    createdAt: minutesAgo(25),
    updatedAt: minutesAgo(1),
  },
  {
    ...JOB_DEFAULTS,
    id: "j-0007",
    state: "running",
    title: "Add customer email search to the ticket list endpoint",
    prompt: "Add customer email search to the ticket list endpoint and add tests",
    repo: REPO_ISSUE_TRACKER,
    sdk: "copilot",
    branch: "cpl/j-0007",
    model: "gpt-4o",
    progressHeadline: "Writing test cases",
    progressSummary: "Adding unit tests for email search filter",
    createdAt: minutesAgo(18),
    updatedAt: minutesAgo(2),
  },
  {
    ...JOB_DEFAULTS,
    id: "j-0009",
    state: "queued",
    title: "Return 409 Conflict when archiving an already-archived ticket",
    prompt: "Return 409 Conflict when archiving an already-archived ticket and add a test",
    repo: REPO_ISSUE_TRACKER,
    sdk: "copilot",
    branch: null,
    model: "gpt-4o",
    createdAt: minutesAgo(3),
    updatedAt: minutesAgo(3),
  },
  {
    ...JOB_DEFAULTS,
    id: "j-0010",
    state: "queued",
    title: "Add a loading skeleton to the ticket list",
    prompt: "Add a loading skeleton to the ticket list while the API request is in flight",
    repo: REPO_SUPPORT_DASH,
    sdk: "copilot",
    branch: null,
    model: "gpt-4o",
    createdAt: minutesAgo(2),
    updatedAt: minutesAgo(2),
  },

  // ── Awaiting Input column ───────────────────────────────────────────
  {
    ...JOB_DEFAULTS,
    id: "j-0003",
    state: "review",
    title: "Add pagination to the ticket list endpoint",
    prompt: "Add pagination to the ticket list endpoint with limit and offset query params",
    repo: REPO_ISSUE_TRACKER,
    sdk: "copilot",
    branch: "cpl/j-0003",
    model: "gpt-4o",
    resolution: "unresolved",
    createdAt: minutesAgo(90),
    updatedAt: minutesAgo(58),
    completedAt: minutesAgo(60),
  },
  {
    ...JOB_DEFAULTS,
    id: "j-0005",
    state: "review",
    title: "Tighten error handling around ticket archival",
    prompt: "Tighten error handling around ticket archival — return proper 404/409 codes",
    repo: REPO_ISSUE_TRACKER,
    sdk: "claude",
    branch: "cpl/j-0005",
    model: "claude-sonnet-4-5-20250514",
    resolution: "unresolved",
    createdAt: minutesAgo(75),
    updatedAt: minutesAgo(53),
    completedAt: minutesAgo(55),
  },
  {
    ...JOB_DEFAULTS,
    id: "j-0008",
    state: "waiting_for_approval",
    title: "Add keyboard shortcut hints to the search input and status filter",
    prompt: "Add keyboard shortcut hints to the search input and status filter",
    repo: REPO_SUPPORT_DASH,
    sdk: "claude",
    branch: "cpl/j-0008",
    model: "claude-sonnet-4-5-20250514",
    permissionMode: "review_and_approve",
    progressHeadline: "Waiting for approval",
    createdAt: minutesAgo(12),
    updatedAt: minutesAgo(5),
  },
  {
    ...JOB_DEFAULTS,
    id: "j-0002",
    state: "completed",
    title: "Add a priority badge column to the ticket table",
    prompt: "Add a priority badge column to the ticket table with color-coded labels",
    repo: REPO_SUPPORT_DASH,
    sdk: "claude",
    branch: "cpl/j-0002",
    model: "claude-sonnet-4-5-20250514",
    resolution: "merged",
    prUrl: "https://github.com/acme/demo-support-dashboard/pull/14",
    createdAt: minutesAgo(120),
    updatedAt: minutesAgo(98),
    completedAt: minutesAgo(100),
  },

  // ── Failed column ──────────────────────────────────────────────────
  {
    ...JOB_DEFAULTS,
    id: "j-0011",
    state: "failed",
    title: "Migrate test suite to Vitest",
    prompt: "Migrate the existing Jest test suite to Vitest",
    repo: REPO_SUPPORT_DASH,
    sdk: "copilot",
    branch: "cpl/j-0011",
    model: "gpt-4o",
    failureReason: "Test runner configuration incompatible with existing jest.config.ts — multiple transform conflicts",
    createdAt: minutesAgo(40),
    updatedAt: minutesAgo(32),
  },
];

// ---------------------------------------------------------------------------
// Job detail: running job j-0007 (transcript, timeline, artifacts)
// ---------------------------------------------------------------------------

const TURN_A = "turn-001";
const TURN_B = "turn-002";
const TURN_C = "turn-003";

export const runningJobTranscript = [
  // Turn 1: Initial analysis
  {
    jobId: "j-0007",
    seq: 1,
    timestamp: minutesAgo(17),
    role: "reasoning",
    content: "I need to understand the existing ticket list endpoint and its query parameters before adding email search. Let me start by reading the route handler.",
    turnId: TURN_A,
  },
  {
    jobId: "j-0007",
    seq: 2,
    timestamp: minutesAgo(17),
    role: "tool_call",
    content: "",
    turnId: TURN_A,
    toolName: "read_file",
    toolArgs: '{"path": "src/routes/tickets.py", "startLine": 1, "endLine": 80}',
    toolResult: "from fastapi import APIRouter, Query, Depends\nfrom ..models import Ticket, TicketFilter\nfrom ..services.ticket_service import TicketService\n\nrouter = APIRouter(prefix=\"/tickets\", tags=[\"tickets\"])\n\n@router.get(\"/\")\nasync def list_tickets(\n    status: str | None = Query(None),\n    priority: str | None = Query(None),\n    limit: int = Query(20, ge=1, le=100),\n    offset: int = Query(0, ge=0),\n    svc: TicketService = Depends(),\n) -> list[Ticket]:\n    return await svc.list_tickets(status=status, priority=priority, limit=limit, offset=offset)",
    toolSuccess: true,
    toolDisplay: "Read src/routes/tickets.py L1-80",
    toolDisplayFull: "Read src/routes/tickets.py L1-80",
    toolDurationMs: 45,
  },
  {
    jobId: "j-0007",
    seq: 3,
    timestamp: minutesAgo(17),
    role: "tool_call",
    content: "",
    turnId: TURN_A,
    toolName: "read_file",
    toolArgs: '{"path": "src/services/ticket_service.py", "startLine": 1, "endLine": 60}',
    toolResult: "from sqlalchemy import select, func\nfrom ..models import Ticket\n\nclass TicketService:\n    def __init__(self, db):\n        self.db = db\n\n    async def list_tickets(self, *, status=None, priority=None, limit=20, offset=0):\n        q = select(Ticket)\n        if status:\n            q = q.where(Ticket.status == status)\n        if priority:\n            q = q.where(Ticket.priority == priority)\n        q = q.offset(offset).limit(limit)\n        result = await self.db.execute(q)\n        return result.scalars().all()",
    toolSuccess: true,
    toolDisplay: "Read src/services/ticket_service.py L1-60",
    toolDisplayFull: "Read src/services/ticket_service.py L1-60",
    toolDurationMs: 38,
  },
  {
    jobId: "j-0007",
    seq: 4,
    timestamp: minutesAgo(16),
    role: "tool_call",
    content: "",
    turnId: TURN_A,
    toolName: "grep_search",
    toolArgs: '{"query": "customer_email|email", "includePattern": "src/**/*.py"}',
    toolResult: "src/models/ticket.py:12:    customer_email: str = Field(...)\nsrc/models/ticket.py:24:    email: str | None = None  # filter alias",
    toolSuccess: true,
    toolDisplay: 'Search "customer_email|email" in src/**/*.py',
    toolDisplayFull: 'Search "customer_email|email" in src/**/*.py',
    toolDurationMs: 120,
  },
  {
    jobId: "j-0007",
    seq: 5,
    timestamp: minutesAgo(16),
    role: "agent",
    content: "I've analyzed the existing codebase. The ticket list endpoint in `src/routes/tickets.py` already supports `status` and `priority` filters. The `Ticket` model has a `customer_email` field. I need to:\n\n1. Add an `email` query parameter to the route handler\n2. Update `TicketService.list_tickets()` to filter by email\n3. Write tests for the new filter",
    turnId: TURN_A,
    title: "Analysis complete",
  },

  // Turn 2: Implementation
  {
    jobId: "j-0007",
    seq: 6,
    timestamp: minutesAgo(14),
    role: "reasoning",
    content: "Now I'll add the email search parameter. I should use `ilike` for case-insensitive partial matching so users can search by partial email addresses.",
    turnId: TURN_B,
  },
  {
    jobId: "j-0007",
    seq: 7,
    timestamp: minutesAgo(14),
    role: "tool_call",
    content: "",
    turnId: TURN_B,
    toolName: "replace_string_in_file",
    toolArgs: '{"filePath": "src/routes/tickets.py", "oldString": "    offset: int = Query(0, ge=0),", "newString": "    offset: int = Query(0, ge=0),\\n    email: str | None = Query(None, description=\\"Filter by customer email (partial match)\\"),"}',
    toolResult: "Successfully replaced string in src/routes/tickets.py",
    toolSuccess: true,
    toolDisplay: "Edit src/routes/tickets.py",
    toolDisplayFull: "Edit src/routes/tickets.py",
    toolDurationMs: 52,
  },
  {
    jobId: "j-0007",
    seq: 8,
    timestamp: minutesAgo(13),
    role: "tool_call",
    content: "",
    turnId: TURN_B,
    toolName: "replace_string_in_file",
    toolArgs: '{"filePath": "src/routes/tickets.py", "oldString": "    return await svc.list_tickets(status=status, priority=priority, limit=limit, offset=offset)", "newString": "    return await svc.list_tickets(status=status, priority=priority, email=email, limit=limit, offset=offset)"}',
    toolResult: "Successfully replaced string in src/routes/tickets.py",
    toolSuccess: true,
    toolDisplay: "Edit src/routes/tickets.py",
    toolDisplayFull: "Edit src/routes/tickets.py",
    toolDurationMs: 48,
  },
  {
    jobId: "j-0007",
    seq: 9,
    timestamp: minutesAgo(12),
    role: "tool_call",
    content: "",
    turnId: TURN_B,
    toolName: "replace_string_in_file",
    toolArgs: '{"filePath": "src/services/ticket_service.py"}',
    toolResult: "Successfully replaced string in src/services/ticket_service.py",
    toolSuccess: true,
    toolDisplay: "Edit src/services/ticket_service.py",
    toolDisplayFull: "Edit src/services/ticket_service.py",
    toolDurationMs: 55,
  },
  {
    jobId: "j-0007",
    seq: 10,
    timestamp: minutesAgo(11),
    role: "tool_call",
    content: "",
    turnId: TURN_B,
    toolName: "run_in_terminal",
    toolArgs: '{"command": "cd /home/dev/repos/demo-issue-tracker-api && python -m pytest tests/ -x --tb=short"}',
    toolResult: "======================== test session starts =========================\ncollected 14 items\ntests/test_tickets.py::test_list_tickets PASSED\ntests/test_tickets.py::test_filter_by_status PASSED\ntests/test_tickets.py::test_filter_by_priority PASSED\ntests/test_tickets.py::test_pagination PASSED\ntests/test_archival.py::test_archive_ticket PASSED\ntests/test_archival.py::test_archive_already_archived PASSED\n========================= 14 passed in 2.34s =========================",
    toolSuccess: true,
    toolDisplay: "$ python -m pytest tests/ -x --tb=short",
    toolDisplayFull: "$ python -m pytest tests/ -x --tb=short",
    toolDurationMs: 3420,
  },
  {
    jobId: "j-0007",
    seq: 11,
    timestamp: minutesAgo(10),
    role: "agent",
    content: "I've added the `email` query parameter to the ticket list endpoint with case-insensitive partial matching using `ilike`. All existing tests pass. Now I'll write tests specifically for the email search functionality.",
    turnId: TURN_B,
    title: "Email filter implemented",
  },

  // Turn 3: Writing tests (in progress)
  {
    jobId: "j-0007",
    seq: 12,
    timestamp: minutesAgo(8),
    role: "reasoning",
    content: "I should write comprehensive tests covering: exact match, partial match, case insensitivity, no results, and combination with other filters.",
    turnId: TURN_C,
  },
  {
    jobId: "j-0007",
    seq: 13,
    timestamp: minutesAgo(7),
    role: "tool_call",
    content: "",
    turnId: TURN_C,
    toolName: "create_file",
    toolArgs: '{"filePath": "tests/test_email_search.py"}',
    toolResult: "File created: tests/test_email_search.py",
    toolSuccess: true,
    toolDisplay: "Create tests/test_email_search.py",
    toolDisplayFull: "Create tests/test_email_search.py",
    toolDurationMs: 32,
  },
  {
    jobId: "j-0007",
    seq: 14,
    timestamp: minutesAgo(5),
    role: "tool_call",
    content: "",
    turnId: TURN_C,
    toolName: "run_in_terminal",
    toolArgs: '{"command": "cd /home/dev/repos/demo-issue-tracker-api && python -m pytest tests/test_email_search.py -v"}',
    toolResult: "======================== test session starts =========================\ncollected 5 items\ntests/test_email_search.py::test_search_by_exact_email PASSED\ntests/test_email_search.py::test_search_by_partial_email PASSED\ntests/test_email_search.py::test_search_case_insensitive PASSED\ntests/test_email_search.py::test_search_no_results PASSED\ntests/test_email_search.py::test_search_combined_with_status PASSED\n========================= 5 passed in 1.87s =========================",
    toolSuccess: true,
    toolDisplay: "$ python -m pytest tests/test_email_search.py -v",
    toolDisplayFull: "$ python -m pytest tests/test_email_search.py -v",
    toolDurationMs: 2870,
  },
  {
    jobId: "j-0007",
    seq: 15,
    timestamp: minutesAgo(3),
    role: "tool_running",
    content: "",
    turnId: TURN_C,
    toolName: "run_in_terminal",
    toolDisplay: "$ python -m pytest tests/ -v --tb=short",
    toolDisplayFull: "$ python -m pytest tests/ -v --tb=short",
  },
];

export const runningJobTimeline = [
  {
    headline: "Cloning repository",
    headlinePast: "Cloned repository",
    summary: "demo-issue-tracker-api -> local workspace",
    timestamp: minutesAgo(18),
    active: false,
  },
  {
    headline: "Creating worktree",
    headlinePast: "Created worktree on cpl/j-0007",
    summary: "Isolated branch for safe modification",
    timestamp: minutesAgo(18),
    active: false,
  },
  {
    headline: "Analyzing codebase",
    headlinePast: "Analyzed ticket list endpoint and service layer",
    summary: "Found customer_email field in Ticket model",
    timestamp: minutesAgo(17),
    active: false,
  },
  {
    headline: "Implementing email search filter",
    headlinePast: "Added email search to ticket list endpoint",
    summary: "Case-insensitive partial match with ilike",
    timestamp: minutesAgo(14),
    active: false,
  },
  {
    headline: "Writing test suite",
    headlinePast: "Wrote email search tests",
    summary: "5 test cases covering exact, partial, case-insensitive, no results",
    timestamp: minutesAgo(8),
    active: false,
  },
  {
    headline: "Running full test suite",
    headlinePast: "Full test suite passing",
    summary: "",
    timestamp: minutesAgo(3),
    active: true,
  },
];

export const runningJobPlan = [
  { label: "Read existing ticket list endpoint", status: "done" as const },
  { label: "Add email query parameter to route", status: "done" as const },
  { label: "Update service layer with ilike filter", status: "done" as const },
  { label: "Write email search test cases", status: "done" as const },
  { label: "Run full test suite", status: "active" as const },
];

// ---------------------------------------------------------------------------
// Job detail: approval job j-0008
// ---------------------------------------------------------------------------

export const approvalJobApprovals = [
  {
    id: "apr-001",
    jobId: "j-0008",
    description: "Write file: src/components/SearchInput.tsx — Replace the placeholder text with keyboard shortcut hint markup",
    proposedAction: 'replace_string_in_file: src/components/SearchInput.tsx\n- placeholder="Search tickets..."\n+ placeholder="Search tickets..." aria-keyshortcuts="Control+K"\n+ <kbd className="shortcut-hint">⌘K</kbd>',
    requestedAt: minutesAgo(5),
    resolvedAt: null,
    resolution: null,
    requiresExplicitApproval: false,
  },
  {
    id: "apr-002",
    jobId: "j-0008",
    description: "Execute command: npm run build — Verify the shortcut hints render correctly after modification",
    proposedAction: "$ npm run build",
    requestedAt: minutesAgo(5),
    resolvedAt: null,
    resolution: null,
    requiresExplicitApproval: false,
  },
];

// ---------------------------------------------------------------------------
// Job detail: review job j-0003 (diff)
// ---------------------------------------------------------------------------

export const reviewJobDiff = [
  {
    path: "src/routes/tickets.py",
    status: "modified",
    additions: 12,
    deletions: 3,
    hunks: [
      {
        oldStart: 8,
        oldLines: 10,
        newStart: 8,
        newLines: 19,
        lines: [
          { type: "context", content: "@router.get(\"/\")" },
          { type: "context", content: "async def list_tickets(" },
          { type: "context", content: "    status: str | None = Query(None)," },
          { type: "context", content: "    priority: str | None = Query(None)," },
          { type: "deletion", content: "    limit: int = Query(20, ge=1, le=100)," },
          { type: "deletion", content: "    offset: int = Query(0, ge=0)," },
          { type: "addition", content: "    limit: int = Query(20, ge=1, le=100, description=\"Max items per page\")," },
          { type: "addition", content: "    offset: int = Query(0, ge=0, description=\"Number of items to skip\")," },
          { type: "context", content: "    svc: TicketService = Depends()," },
          { type: "deletion", content: ") -> list[Ticket]:" },
          { type: "addition", content: ") -> PaginatedResponse[Ticket]:" },
          { type: "addition", content: "    total = await svc.count_tickets(status=status, priority=priority)" },
          { type: "addition", content: "    items = await svc.list_tickets(" },
          { type: "addition", content: "        status=status, priority=priority, limit=limit, offset=offset" },
          { type: "addition", content: "    )" },
          { type: "addition", content: "    return PaginatedResponse(" },
          { type: "addition", content: "        items=items," },
          { type: "addition", content: "        total=total," },
          { type: "addition", content: "        limit=limit," },
          { type: "addition", content: "        offset=offset," },
          { type: "addition", content: "    )" },
        ],
      },
    ],
  },
  {
    path: "src/models/pagination.py",
    status: "added",
    additions: 18,
    deletions: 0,
    hunks: [
      {
        oldStart: 0,
        oldLines: 0,
        newStart: 1,
        newLines: 18,
        lines: [
          { type: "addition", content: "from typing import Generic, TypeVar" },
          { type: "addition", content: "from pydantic import BaseModel, Field" },
          { type: "addition", content: "" },
          { type: "addition", content: "T = TypeVar(\"T\")" },
          { type: "addition", content: "" },
          { type: "addition", content: "class PaginatedResponse(BaseModel, Generic[T]):" },
          { type: "addition", content: "    \"\"\"Paginated list response with total count.\"\"\"" },
          { type: "addition", content: "    items: list[T]" },
          { type: "addition", content: "    total: int = Field(ge=0)" },
          { type: "addition", content: "    limit: int = Field(ge=1)" },
          { type: "addition", content: "    offset: int = Field(ge=0)" },
          { type: "addition", content: "" },
          { type: "addition", content: "    @property" },
          { type: "addition", content: "    def has_more(self) -> bool:" },
          { type: "addition", content: "        return self.offset + self.limit < self.total" },
          { type: "addition", content: "" },
          { type: "addition", content: "    @property" },
          { type: "addition", content: "    def page(self) -> int:" },
          { type: "addition", content: "        return self.offset // self.limit + 1" },
        ],
      },
    ],
  },
  {
    path: "src/services/ticket_service.py",
    status: "modified",
    additions: 8,
    deletions: 0,
    hunks: [
      {
        oldStart: 15,
        oldLines: 4,
        newStart: 15,
        newLines: 12,
        lines: [
          { type: "context", content: "        result = await self.db.execute(q)" },
          { type: "context", content: "        return result.scalars().all()" },
          { type: "context", content: "" },
          { type: "addition", content: "    async def count_tickets(self, *, status=None, priority=None) -> int:" },
          { type: "addition", content: "        q = select(func.count()).select_from(Ticket)" },
          { type: "addition", content: "        if status:" },
          { type: "addition", content: "            q = q.where(Ticket.status == status)" },
          { type: "addition", content: "        if priority:" },
          { type: "addition", content: "            q = q.where(Ticket.priority == priority)" },
          { type: "addition", content: "        result = await self.db.execute(q)" },
          { type: "addition", content: "        return result.scalar_one()" },
          { type: "addition", content: "" },
          { type: "context", content: "    async def archive_ticket(self, ticket_id: str) -> Ticket:" },
        ],
      },
    ],
  },
  {
    path: "tests/test_pagination.py",
    status: "added",
    additions: 42,
    deletions: 0,
    hunks: [
      {
        oldStart: 0,
        oldLines: 0,
        newStart: 1,
        newLines: 42,
        lines: [
          { type: "addition", content: "import pytest" },
          { type: "addition", content: "from httpx import AsyncClient" },
          { type: "addition", content: "" },
          { type: "addition", content: "@pytest.mark.asyncio" },
          { type: "addition", content: "async def test_default_pagination(client: AsyncClient):" },
          { type: "addition", content: "    resp = await client.get(\"/tickets\")" },
          { type: "addition", content: "    assert resp.status_code == 200" },
          { type: "addition", content: "    data = resp.json()" },
          { type: "addition", content: "    assert \"items\" in data" },
          { type: "addition", content: "    assert \"total\" in data" },
          { type: "addition", content: "    assert data[\"limit\"] == 20" },
          { type: "addition", content: "    assert data[\"offset\"] == 0" },
          { type: "addition", content: "" },
          { type: "addition", content: "@pytest.mark.asyncio" },
          { type: "addition", content: "async def test_custom_limit_offset(client: AsyncClient):" },
          { type: "addition", content: "    resp = await client.get(\"/tickets?limit=5&offset=10\")" },
          { type: "addition", content: "    assert resp.status_code == 200" },
          { type: "addition", content: "    data = resp.json()" },
          { type: "addition", content: "    assert data[\"limit\"] == 5" },
          { type: "addition", content: "    assert data[\"offset\"] == 10" },
          { type: "addition", content: "" },
          { type: "addition", content: "@pytest.mark.asyncio" },
          { type: "addition", content: "async def test_total_reflects_filter(client: AsyncClient):" },
          { type: "addition", content: "    resp = await client.get(\"/tickets?status=open\")" },
          { type: "addition", content: "    data = resp.json()" },
          { type: "addition", content: "    all_resp = await client.get(\"/tickets\")" },
          { type: "addition", content: "    all_data = all_resp.json()" },
          { type: "addition", content: "    assert data[\"total\"] <= all_data[\"total\"]" },
          { type: "addition", content: "" },
          { type: "addition", content: "@pytest.mark.asyncio" },
          { type: "addition", content: "async def test_has_more_flag(client: AsyncClient):" },
          { type: "addition", content: "    resp = await client.get(\"/tickets?limit=1\")" },
          { type: "addition", content: "    data = resp.json()" },
          { type: "addition", content: "    # With test fixtures having multiple tickets, has_more should be True" },
          { type: "addition", content: "    if data[\"total\"] > 1:" },
          { type: "addition", content: "        assert data[\"offset\"] + data[\"limit\"] < data[\"total\"]" },
          { type: "addition", content: "" },
          { type: "addition", content: "@pytest.mark.asyncio" },
          { type: "addition", content: "async def test_invalid_limit_rejected(client: AsyncClient):" },
          { type: "addition", content: "    resp = await client.get(\"/tickets?limit=0\")" },
          { type: "addition", content: "    assert resp.status_code == 422" },
          { type: "addition", content: "    resp = await client.get(\"/tickets?limit=200\")" },
          { type: "addition", content: "    assert resp.status_code == 422" },
        ],
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// Health, SDKs, Models, Settings
// ---------------------------------------------------------------------------

export const mockHealth = {
  status: "healthy",
  version: "0.12.0",
  uptimeSeconds: 86400,
  activeJobs: 2,
  queuedJobs: 2,
};

export const mockSdks = {
  default: "copilot",
  sdks: [
    {
      id: "copilot",
      name: "GitHub Copilot",
      enabled: true,
      status: "ready",
      authenticated: true,
      hint: "",
    },
    {
      id: "claude",
      name: "Claude Code",
      enabled: true,
      status: "ready",
      authenticated: true,
      hint: "",
    },
  ],
};

export const mockModels = [
  { id: "gpt-4o", name: "GPT-4o", isDefault: true },
  { id: "claude-sonnet-4-5-20250514", name: "Claude Sonnet 4.5", isDefault: false },
  { id: "claude-3-5-haiku-20250120", name: "Claude 3.5 Haiku", isDefault: false },
  { id: "o3-mini", name: "o3-mini", isDefault: false },
];

export const mockSettings = {
  maxConcurrentJobs: 3,
  permissionMode: "full_auto",
  autoPush: false,
  cleanupWorktree: true,
  deleteBranchAfterMerge: false,
  artifactRetentionDays: 30,
  maxArtifactSizeMb: 50,
  autoArchiveDays: 0,
  verify: false,
  selfReview: false,
  maxTurns: 200,
  verifyPrompt: "",
  selfReviewPrompt: "",
};

export const mockRepoList = {
  items: [REPO_ISSUE_TRACKER, REPO_SUPPORT_DASH],
};

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------

export const mockScorecard = {
  period: 7,
  activity: {
    totalJobs: 42,
    running: 2,
    inReview: 4,
    merged: 28,
    prCreated: 3,
    discarded: 2,
    failed: 2,
    cancelled: 1,
  },
  budget: [
    {
      sdk: "copilot",
      totalCostUsd: 18.42,
      premiumRequests: 847,
      jobCount: 26,
      avgCostPerJob: 0.71,
      avgDurationMs: 480000,
    },
    {
      sdk: "claude",
      totalCostUsd: 24.86,
      premiumRequests: 0,
      jobCount: 16,
      avgCostPerJob: 1.55,
      avgDurationMs: 620000,
    },
  ],
  quotaJson: null,
  costTrend: [
    { date: "2026-03-25", cost: 4.2, jobs: 5 },
    { date: "2026-03-26", cost: 6.8, jobs: 7 },
    { date: "2026-03-27", cost: 5.1, jobs: 6 },
    { date: "2026-03-28", cost: 8.4, jobs: 9 },
    { date: "2026-03-29", cost: 7.2, jobs: 6 },
    { date: "2026-03-30", cost: 6.9, jobs: 5 },
    { date: "2026-03-31", cost: 4.6, jobs: 4 },
  ],
};

export const mockModelComparison = {
  period: 7,
  repo: null,
  models: [
    {
      model: "gpt-4o",
      sdk: "copilot",
      jobCount: 20,
      avgCost: 0.65,
      avgDurationMs: 440000,
      totalCostUsd: 13.0,
      premiumRequests: 620,
      merged: 16,
      prCreated: 2,
      discarded: 1,
      failed: 1,
      avgVerifyTurns: null,
      verifyJobCount: 0,
      avgDiffLines: 45,
      cacheHitRate: 0.72,
      costPerJob: 0.65,
      costPerMinute: 0.089,
      costPerTurn: 0.018,
      costPerToolCall: 0.005,
    },
    {
      model: "claude-sonnet-4-5-20250514",
      sdk: "claude",
      jobCount: 14,
      avgCost: 1.48,
      avgDurationMs: 580000,
      totalCostUsd: 20.72,
      premiumRequests: 0,
      merged: 10,
      prCreated: 1,
      discarded: 1,
      failed: 2,
      avgVerifyTurns: null,
      verifyJobCount: 0,
      avgDiffLines: 68,
      cacheHitRate: 0.65,
      costPerJob: 1.48,
      costPerMinute: 0.153,
      costPerTurn: 0.032,
      costPerToolCall: 0.009,
    },
    {
      model: "claude-3-5-haiku-20250120",
      sdk: "claude",
      jobCount: 8,
      avgCost: 0.52,
      avgDurationMs: 320000,
      totalCostUsd: 4.14,
      premiumRequests: 0,
      merged: 6,
      prCreated: 0,
      discarded: 1,
      failed: 1,
      avgVerifyTurns: null,
      verifyJobCount: 0,
      avgDiffLines: 28,
      cacheHitRate: 0.78,
      costPerJob: 0.52,
      costPerMinute: 0.098,
      costPerTurn: 0.012,
      costPerToolCall: 0.003,
    },
  ],
};

export const mockAnalyticsTools = {
  period: 7,
  tools: [
    { tool: "replace_string_in_file", category: "file_write", calls: 342, failures: 12, failureRate: 0.035, avgDurationMs: 48, p50: 42, p95: 85, p99: 120 },
    { tool: "read_file", category: "file_read", calls: 890, failures: 3, failureRate: 0.003, avgDurationMs: 35, p50: 28, p95: 72, p99: 110 },
    { tool: "run_in_terminal", category: "shell", calls: 215, failures: 28, failureRate: 0.13, avgDurationMs: 3200, p50: 1800, p95: 8500, p99: 15000 },
    { tool: "grep_search", category: "file_search", calls: 410, failures: 1, failureRate: 0.002, avgDurationMs: 120, p50: 95, p95: 280, p99: 450 },
    { tool: "create_file", category: "file_write", calls: 156, failures: 2, failureRate: 0.013, avgDurationMs: 42, p50: 38, p95: 75, p99: 100 },
    { tool: "semantic_search", category: "file_search", calls: 178, failures: 5, failureRate: 0.028, avgDurationMs: 850, p50: 620, p95: 2100, p99: 3800 },
  ],
};

export const mockAnalyticsRepos = {
  period: 7,
  repos: [
    { repo: REPO_ISSUE_TRACKER, jobCount: 24, totalCostUsd: 22.4, avgCostPerJob: 0.93, avgDurationMs: 520000, toolCalls: 1280, premiumRequests: 450 },
    { repo: REPO_SUPPORT_DASH, jobCount: 18, totalCostUsd: 20.88, avgCostPerJob: 1.16, avgDurationMs: 580000, toolCalls: 910, premiumRequests: 397 },
  ],
};

export const mockCostDrivers = {
  period: 7,
  dimension: "overall",
  summary: [],
};

export const mockObservations = {
  observations: [],
};

export const mockOverview = {
  period: 7,
  totalJobs: 42,
  succeeded: 28,
  review: 4,
  completed: 3,
  failed: 2,
  cancelled: 1,
  running: 2,
  totalCostUsd: 43.28,
  totalTokens: 2840000,
  avgDurationMs: 540000,
  totalPremiumRequests: 847,
  totalToolCalls: 2191,
  totalToolFailures: 51,
  totalAgentErrors: 3,
  totalToolErrors: 48,
  toolSuccessRate: 0.977,
  cacheHitRate: 0.71,
  costTrend: mockScorecard.costTrend,
};
