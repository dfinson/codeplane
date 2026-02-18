"""Unified confirmation gate system for CodePlane MCP server.

Provides a single two-phase confirmation protocol used by all gated operations:
- Destructive actions (git reset --hard)
- Expensive reads (read_file_full on large files, read_source cap violations)
- Budget resets
- Pattern-break interventions (thrash detection)

Every gate follows the same protocol:
1. Server detects a gated condition
2. Server returns normal results + a gate block
3. Agent's next relevant call must include gate_token + gate_reason
4. Server validates token + reason, then either proceeds or rejects
"""

from __future__ import annotations

import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# =============================================================================
# Gate Specs and Results
# =============================================================================


@dataclass(frozen=True)
class GateSpec:
    """Definition of a gate kind.

    Attributes:
        kind: Category of gate (destructive_action, expensive_read, etc.)
        reason_min_chars: Minimum characters required in the gate_reason.
        reason_prompt: The question posed to the agent to justify continuation.
        expires_calls: Token dies after this many non-confirming tool calls.
        message: Human-readable explanation of why the gate fired.
    """

    kind: str
    reason_min_chars: int
    reason_prompt: str
    expires_calls: int = 3
    message: str = ""


@dataclass
class GateResult:
    """Outcome of a gate validation attempt."""

    ok: bool
    error: str | None = None
    hint: str | None = None
    reason: str | None = None
    kind: str | None = None


@dataclass
class PendingGate:
    """An issued gate waiting for confirmation."""

    gate_id: str
    spec: GateSpec
    issued_at: float
    calls_remaining: int


# =============================================================================
# Gate Manager
# =============================================================================


class GateManager:
    """Unified gate issuance and validation.

    Composed into ScopeBudget. All gated operations go through
    issue() to create a gate and validate() to confirm it.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingGate] = {}

    def issue(self, spec: GateSpec) -> dict[str, Any]:
        """Issue a new gate. Returns the gate block for the response.

        The returned dict should be included in the tool response under
        the ``gate`` key.
        """
        gate_id = f"gate_{secrets.token_hex(8)}"
        self._pending[gate_id] = PendingGate(
            gate_id=gate_id,
            spec=spec,
            issued_at=time.monotonic(),
            calls_remaining=spec.expires_calls,
        )
        return {
            "id": gate_id,
            "kind": spec.kind,
            "reason_required": True,
            "reason_min_chars": spec.reason_min_chars,
            "reason_prompt": spec.reason_prompt,
            "expires_calls": spec.expires_calls,
            "message": spec.message,
        }

    def validate(self, gate_token: str, gate_reason: str) -> GateResult:
        """Validate a gate confirmation.

        Returns GateResult with ok=True if the token matches and the
        reason meets the minimum character requirement. Consumes the
        gate on success.
        """
        pending = self._pending.get(gate_token)
        if not pending:
            return GateResult(
                ok=False,
                error="Invalid or expired gate token. Request a new one.",
            )

        reason = gate_reason.strip()
        min_chars = pending.spec.reason_min_chars
        if len(reason) < min_chars:
            return GateResult(
                ok=False,
                error=(f"Reason must be at least {min_chars} characters (got {len(reason)})"),
                hint=pending.spec.reason_prompt,
            )

        # Gate passed - consume it
        del self._pending[gate_token]
        return GateResult(
            ok=True,
            reason=reason,
            kind=pending.spec.kind,
        )

    def tick(self) -> None:
        """Decrement expiry on all pending gates. Call after every tool call.

        Gates whose calls_remaining reaches 0 are evicted.
        """
        expired: list[str] = []
        for gate_id, gate in self._pending.items():
            gate.calls_remaining -= 1
            if gate.calls_remaining <= 0:
                expired.append(gate_id)
        for gate_id in expired:
            del self._pending[gate_id]

    def has_pending(self, kind: str | None = None) -> bool:
        """Check if any gates are pending, optionally filtered by kind."""
        if kind is None:
            return bool(self._pending)
        return any(g.spec.kind == kind for g in self._pending.values())

    def clear(self) -> None:
        """Clear all pending gates."""
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        """Number of currently pending gates."""
        return len(self._pending)


# =============================================================================
# Standard Gate Specs (reusable across handlers)
# =============================================================================

DESTRUCTIVE_RESET_GATE = GateSpec(
    kind="destructive_action",
    reason_min_chars=50,
    reason_prompt=(
        "What uncommitted work will be lost and why is this reset necessary? "
        "Have you confirmed with your user?"
    ),
    expires_calls=1,
    message=(
        "DESTRUCTIVE ACTION: git reset --hard will permanently discard "
        "all uncommitted changes. This cannot be undone."
    ),
)

EXPENSIVE_READ_GATE = GateSpec(
    kind="expensive_read",
    reason_min_chars=50,
    reason_prompt=(
        "Why do you need the entire file? What specific information can't you "
        "get via search(mode=references) + read_source on the spans?"
    ),
    expires_calls=3,
)

READ_CAP_EXCEEDED_GATE = GateSpec(
    kind="expensive_read",
    reason_min_chars=50,
    reason_prompt=(
        "Why do you need to exceed read caps? Can you split into smaller "
        "read_source calls or use search to narrow down the relevant spans?"
    ),
    expires_calls=3,
)


def budget_reset_gate(has_mutations: bool) -> GateSpec:
    """Create a gate spec for budget reset based on mutation state."""
    min_chars = 50 if has_mutations else 250
    return GateSpec(
        kind="budget_reset",
        reason_min_chars=min_chars,
        reason_prompt=(
            "What new information do you need that the previous budget window didn't provide?"
        ),
        expires_calls=3,
        message=f"Budget reset (min {min_chars} char justification required).",
    )


# =============================================================================
# Call Pattern Detector
# =============================================================================

TOOL_CATEGORIES: dict[str, str] = {
    "search": "search",
    "read_source": "read",
    "read_file_full": "read_full",
    "write_source": "write",
    "refactor_rename": "refactor",
    "refactor_move": "refactor",
    "refactor_delete": "refactor",
    "refactor_apply": "refactor",
    "refactor_cancel": "meta",
    "refactor_inspect": "meta",
    "lint_check": "lint",
    "lint_tools": "meta",
    "run_test_targets": "test",
    "discover_test_targets": "meta",
    "get_test_run_status": "meta",
    "cancel_test_run": "meta",
    "semantic_diff": "diff",
    "map_repo": "meta",
    "list_files": "meta",
    "describe": "meta",
    "reset_budget": "meta",
    "inspect_affected_tests": "meta",
}

# Git tools are all "git" category
_GIT_TOOL_NAMES = [
    "git_status",
    "git_diff",
    "git_commit",
    "git_stage_and_commit",
    "git_log",
    "git_push",
    "git_pull",
    "git_checkout",
    "git_merge",
    "git_reset",
    "git_stage",
    "git_branch",
    "git_remote",
    "git_stash",
    "git_rebase",
    "git_inspect",
    "git_history",
    "git_submodule",
    "git_worktree",
]
for _name in _GIT_TOOL_NAMES:
    TOOL_CATEGORIES[_name] = "git"

# Categories that represent "action" (clear pattern window)
ACTION_CATEGORIES = frozenset({"write", "refactor", "lint", "test", "git", "diff"})


def categorize_tool(tool_name: str) -> str:
    """Map a tool name to its category."""
    return TOOL_CATEGORIES.get(tool_name, "meta")


@dataclass
class CallRecord:
    """A single tool call in the sliding window."""

    category: str
    tool_name: str
    files: list[str] = field(default_factory=list)
    timestamp: float = 0.0
    hit_count: int = 0


@dataclass
class PatternMatch:
    """Result of a pattern detection."""

    pattern_name: str
    severity: str  # "warn" or "break"
    cause: str  # "over_gathering" or "inefficient" or "wasted"
    message: str
    reason_prompt: str
    suggested_workflow: dict[str, str]


# Window size for pattern detection
WINDOW_SIZE = 15


class CallPatternDetector:
    """Sliding-window call pattern detector.

    Records recent tool calls and evaluates them against known
    anti-patterns. Composed into ScopeBudget.
    """

    def __init__(self, window_size: int = WINDOW_SIZE) -> None:
        self._window: deque[CallRecord] = deque(maxlen=window_size)

    def record(
        self,
        tool_name: str,
        files: list[str] | None = None,
        hit_count: int = 0,
    ) -> None:
        """Record a tool call into the window."""
        category = categorize_tool(tool_name)
        self._window.append(
            CallRecord(
                category=category,
                tool_name=tool_name,
                files=files or [],
                timestamp=time.monotonic(),
                hit_count=hit_count,
            )
        )
        # Action calls clear the window (agent made progress)
        if category in ACTION_CATEGORIES:
            self._window.clear()

    def evaluate(self) -> PatternMatch | None:
        """Evaluate the current window against known anti-patterns.

        Returns the highest-severity match, or None if no patterns fire.
        """
        if len(self._window) < 5:
            return None

        # Check patterns in severity order (break first)
        for check in _PATTERN_CHECKS:
            match = check(self._window)
            if match is not None:
                return match
        return None

    def clear(self) -> None:
        """Clear the window (e.g. after a mutation)."""
        self._window.clear()

    @property
    def window_length(self) -> int:
        """Number of calls currently in the window."""
        return len(self._window)


# =============================================================================
# Pattern Detection Functions
# =============================================================================

_SEARCH_WORKFLOW: dict[str, str] = {
    "if_exploring_structure": (
        "Use map_repo(include=['structure','dependencies']) for overview, then one targeted search"
    ),
    "if_finding_references": (
        "Use search(mode='references', enrichment='function') - "
        "one call gets callers with full function bodies"
    ),
    "if_reading_code": ("Switch to read_source with multiple targets per call (up to 20)"),
    "if_ready_to_act": (
        "Proceed to write_source, refactor_rename, lint_check, or run_test_targets"
    ),
}

_READ_WORKFLOW: dict[str, str] = {
    "if_looking_for_callers": ("Use search(mode='references') instead of reading files manually"),
    "if_understanding_a_function": (
        "Use search(mode='definitions', enrichment='function') for edit-ready code"
    ),
    "if_reading_multiple_spans": ("Batch up to 20 targets in one read_source call"),
    "if_ready_to_act": (
        "Proceed to write_source, refactor_rename, lint_check, or run_test_targets"
    ),
}


def _check_pure_search_chain(window: deque[CallRecord]) -> PatternMatch | None:
    """Detect 8+ of last 10 calls being searches."""
    recent = list(window)[-10:]
    if len(recent) < 8:
        return None

    search_count = sum(1 for r in recent if r.category == "search")
    if search_count < 8:
        return None

    # Classify cause: check if searches hit overlapping files
    search_records = [r for r in recent if r.category == "search"]
    all_file_sets = [set(r.files) for r in search_records if r.files]

    # Check overlap between consecutive search results
    overlap_count = 0
    for i in range(1, len(all_file_sets)):
        if all_file_sets[i] & all_file_sets[i - 1]:
            overlap_count += 1

    if overlap_count >= len(all_file_sets) // 2 and all_file_sets:
        cause = "over_gathering"
        reason_prompt = (
            "What specific question can you NOT answer with the context "
            "you already have? If you cannot articulate a specific unknown, "
            "you likely have enough context to proceed."
        )
    else:
        cause = "inefficient"
        reason_prompt = (
            "You're making many individual searches. Can you use "
            "search(mode='references', enrichment='function') to get "
            "callers with bodies in one call? Or map_repo for structure?"
        )

    return PatternMatch(
        pattern_name="pure_search_chain",
        severity="break",
        cause=cause,
        message=(
            f"{search_count} of your last {len(recent)} calls are searches. "
            f"Cause: {cause.replace('_', ' ')}."
        ),
        reason_prompt=reason_prompt,
        suggested_workflow=_SEARCH_WORKFLOW,
    )


def _check_read_spiral(window: deque[CallRecord]) -> PatternMatch | None:
    """Detect 8+ reads touching <= 1 unique file (re-reading same file)."""
    recent = list(window)[-10:]
    read_records = [r for r in recent if r.category in ("read", "read_full")]
    if len(read_records) < 8:
        return None

    all_files: set[str] = set()
    for r in read_records:
        all_files.update(r.files)

    if len(all_files) > 1:
        return None

    return PatternMatch(
        pattern_name="read_spiral",
        severity="break",
        cause="over_gathering",
        message=(
            f"{len(read_records)} reads touching only {len(all_files)} unique file(s). "
            "You're re-reading files you've already seen."
        ),
        reason_prompt=(
            "You've read these files multiple times. What specific uncertainty "
            "remains? State what would change your confidence to act."
        ),
        suggested_workflow=_READ_WORKFLOW,
    )


def _check_scatter_read(window: deque[CallRecord]) -> PatternMatch | None:
    """Detect 8+ reads across 8+ different files (unfocused)."""
    read_records = [r for r in window if r.category in ("read", "read_full")]
    if len(read_records) < 8:
        return None

    all_files: set[str] = set()
    for r in read_records:
        all_files.update(r.files)

    if len(all_files) < 8:
        return None

    # Check if reads have 1 target each (inefficient batching)
    single_target_reads = sum(1 for r in read_records if len(r.files) == 1)
    if single_target_reads >= 6:
        cause = "inefficient"
        reason_prompt = (
            "You're reading files one at a time. Batch up to 20 targets "
            "in a single read_source call to reduce round-trips."
        )
    else:
        cause = "over_gathering"
        reason_prompt = (
            f"You've read {len(all_files)} different files. Which are "
            "actually relevant to your change? State your plan before "
            "reading more."
        )

    return PatternMatch(
        pattern_name="scatter_read",
        severity="warn",
        cause=cause,
        message=(f"{len(read_records)} reads across {len(all_files)} different files."),
        reason_prompt=reason_prompt,
        suggested_workflow=_READ_WORKFLOW,
    )


def _check_search_read_loop(window: deque[CallRecord]) -> PatternMatch | None:
    """Detect 5+ alternating search/read cycles."""
    categories = [r.category for r in window]
    # Collapse consecutive same-category entries
    collapsed: list[str] = []
    for cat in categories:
        if cat in ("search", "read", "read_full"):
            norm = "search" if cat == "search" else "read"
            if not collapsed or collapsed[-1] != norm:
                collapsed.append(norm)

    # Count transitions between search and read
    transitions = 0
    for i in range(1, len(collapsed)):
        if collapsed[i - 1] != collapsed[i]:
            transitions += 1

    if transitions < 8:  # 5 cycles = ~10 transitions, be conservative
        return None

    return PatternMatch(
        pattern_name="search_read_loop",
        severity="warn",
        cause="inefficient",
        message=(
            f"{transitions} search/read alternations detected. "
            "You're bouncing between searching and reading."
        ),
        reason_prompt=(
            "Use search(enrichment='function') to get source code with "
            "search results directly, eliminating the search-then-read "
            "round-trip."
        ),
        suggested_workflow=_SEARCH_WORKFLOW,
    )


def _check_zero_result_searches(window: deque[CallRecord]) -> PatternMatch | None:
    """Detect 3+ searches with 0 results."""
    search_records = [r for r in window if r.category == "search"]
    zero_result_count = sum(1 for r in search_records if r.hit_count == 0)

    if zero_result_count < 3:
        return None

    return PatternMatch(
        pattern_name="zero_result_searches",
        severity="warn",
        cause="inefficient",
        message=(
            f"{zero_result_count} searches returned 0 results. "
            "Your search strategy needs adjustment."
        ),
        reason_prompt=(
            "Multiple searches returned nothing. Try: mode='lexical' for "
            "text patterns, map_repo to discover correct module/symbol names, "
            "or list_files to verify paths exist."
        ),
        suggested_workflow=_SEARCH_WORKFLOW,
    )


def _check_full_file_creep(window: deque[CallRecord]) -> PatternMatch | None:
    """Detect 3+ read_file_full calls in the window."""
    full_reads = [r for r in window if r.category == "read_full"]
    if len(full_reads) < 3:
        return None

    return PatternMatch(
        pattern_name="full_file_creep",
        severity="warn",
        cause="inefficient",
        message=(
            f"{len(full_reads)} full-file reads in recent calls. "
            "Full reads are expensive - most tasks only need specific spans."
        ),
        reason_prompt=(
            "Use search to find the relevant spans, then read_source on "
            "just those spans instead of reading entire files."
        ),
        suggested_workflow=_READ_WORKFLOW,
    )


# Pattern checks in severity order (break before warn)
_PATTERN_CHECKS = [
    _check_pure_search_chain,  # break
    _check_read_spiral,  # break
    _check_scatter_read,  # warn
    _check_search_read_loop,  # warn
    _check_zero_result_searches,  # warn
    _check_full_file_creep,  # warn
]


# =============================================================================
# Helper: build gate response fields for pattern breaks
# =============================================================================


def build_pattern_gate_spec(match: PatternMatch) -> GateSpec:
    """Create a GateSpec from a PatternMatch."""
    return GateSpec(
        kind="pattern_break",
        reason_min_chars=50,
        reason_prompt=match.reason_prompt,
        expires_calls=3,
        message=match.message,
    )


def build_pattern_hint(match: PatternMatch) -> dict[str, Any]:
    """Build the agentic_hint + suggested_workflow for a pattern warning."""
    return {
        "agentic_hint": (
            f"PATTERN: {match.pattern_name} - {match.message}\n\n{match.reason_prompt}"
        ),
        "detected_pattern": match.pattern_name,
        "pattern_cause": match.cause,
        "suggested_workflow": match.suggested_workflow,
    }
