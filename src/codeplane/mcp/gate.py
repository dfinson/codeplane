"""Unified confirmation gate system for CodePlane MCP server.

Provides a single two-phase confirmation protocol used by all gated operations:
- Destructive actions (git reset --hard)
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
        expires_seconds: Token dies after this many seconds (if set).
        message: Human-readable explanation of why the gate fired.
    """

    kind: str
    reason_min_chars: int
    reason_prompt: str
    expires_calls: int = 3
    expires_seconds: float | None = None
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
            "expires_seconds": spec.expires_seconds,
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

        if self._is_expired(pending):
            del self._pending[gate_token]
            return GateResult(
                ok=False,
                error="Gate token expired. Request a new one.",
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
            if self._is_expired(gate):
                expired.append(gate_id)
                continue
            gate.calls_remaining -= 1
            if gate.calls_remaining <= 0:
                expired.append(gate_id)
        for gate_id in expired:
            del self._pending[gate_id]

    def _is_expired(self, gate: PendingGate) -> bool:
        """Check time-based expiration for a pending gate."""
        if gate.spec.expires_seconds is None:
            return False
        return (time.monotonic() - gate.issued_at) >= gate.spec.expires_seconds

    def _expire_time_based_gates(self) -> None:
        """Remove gates that have exceeded their time-based expiry."""
        expired = [gate_id for gate_id, gate in self._pending.items() if self._is_expired(gate)]
        for gate_id in expired:
            del self._pending[gate_id]

    def expire_time_based_gates(self) -> None:
        """Public helper to evict time-expired gates."""
        self._expire_time_based_gates()

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

EXPENSIVE_READ_GATE = GateSpec(
    kind="expensive_read",
    reason_min_chars=50,
    reason_prompt=(
        "Why do you need the entire file? recon provides scaffolds. "
        "Read files via terminal (cat/head) using paths from scaffolds."
    ),
    expires_calls=3,
)


BROAD_FILTER_TEST_GATE = GateSpec(
    kind="broad_test_run",
    reason_min_chars=50,
    reason_prompt=(
        "You ran scoped tests recently. Why do you also need a filtered broad run? "
        "What specific test targets can't be reached via affected_by?"
    ),
    expires_calls=3,
    message=(
        "Broad test run via target_filter requires justification. "
        "Prefer checkpoint(changed_files=[...]) for impact-aware selection."
    ),
)

FULL_SUITE_TEST_GATE = GateSpec(
    kind="full_test_suite",
    reason_min_chars=250,
    reason_prompt=(
        "Why were impacted tests insufficient? What specific failures or coverage gaps "
        "require the full suite? List the symptoms that led you here and confirm "
        "you understand this will run all tests in the repository."
    ),
    expires_calls=3,
    message=(
        "Full test suite run is extremely expensive. You must explain why "
        "scoped testing (affected_by) was insufficient."
    ),
)


def has_recent_scoped_test(window: deque[CallRecord]) -> bool:
    """Check if a successful scoped test run exists in the window."""
    return any(r.category == "test_scoped" for r in window)


# =============================================================================
# Call Pattern Detector
# =============================================================================

TOOL_CATEGORIES: dict[str, str] = {
    "recon": "search",
    "refactor_edit": "write",
    "refactor_plan": "write",
    "refactor_rename": "refactor",
    "refactor_move": "refactor",
    "recon_impact": "search",
    "refactor_commit": "refactor",
    "refactor_cancel": "meta",
    "semantic_diff": "diff",
    "describe": "meta",
    "checkpoint": "test",
}

# Categories that represent mutation (clear pattern window).
# "diff", "test", and "lint" are NOT here — they are
# information gathering / verification and should not reset bypass
# detection windows.  Lint clears conditionally (only when it
# auto-fixed files); see CallPatternDetector.record(clears_window=True).
ACTION_CATEGORIES = frozenset({"write", "refactor"})


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
        category_override: str | None = None,
        clears_window: bool = False,
    ) -> None:
        """Record a tool call into the window.

        Args:
            category_override: Force a specific category instead of auto-detecting.
                Used by test handler to record ``test_scoped`` which must persist
                in the window as evidence for the broad-test prerequisite.
            clears_window: Explicitly clear the window after recording.
                Used for conditional mutations (e.g. verify that auto-fixed
                files).  Unconditional mutations use ACTION_CATEGORIES instead.
        """
        category = category_override or categorize_tool(tool_name)
        self._window.append(
            CallRecord(
                category=category,
                tool_name=tool_name,
                files=files or [],
                timestamp=time.monotonic(),
                hit_count=hit_count,
            )
        )
        # Mutation calls clear the window (agent made progress).
        # test_scoped is exempt — it must persist as prerequisite evidence.
        should_clear = (
            category in ACTION_CATEGORIES and category != "test_scoped"
        ) or clears_window
        if should_clear:
            scoped = [r for r in self._window if r.category == "test_scoped"]
            self._window.clear()
            self._window.extend(scoped)

    def evaluate(self, current_tool: str | None = None) -> PatternMatch | None:
        """Evaluate the current window against known anti-patterns.

        Args:
            current_tool: The tool being invoked NOW but not yet recorded.
                A temporary record is appended so pattern checks can see it,
                then removed after evaluation.

        Returns the highest-severity match, or None if no patterns fire.
        """
        # Temporarily include the current call so bypass patterns can
        # detect write/commit at the moment it happens (before record()
        # which may clear the window for action categories).
        if current_tool:
            temp = CallRecord(
                category=categorize_tool(current_tool),
                tool_name=current_tool,
                timestamp=time.monotonic(),
            )
            self._window.append(temp)

        try:
            if len(self._window) < 5:
                return None

            # Check patterns in severity order (break first)
            for check in _PATTERN_CHECKS:
                match = check(self._window)
                if match is not None:
                    return match
            return None
        finally:
            if current_tool:
                self._window.pop()

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
    "if_exploring_structure": ("recon includes a repo_map — use it for structural orientation"),
    "if_reading_code": ("Read files via terminal (cat/head) using paths from scaffolds"),
    "if_ready_to_act": ("Proceed to refactor_plan → refactor_edit → checkpoint"),
}


def _check_pure_search_chain(window: deque[CallRecord]) -> PatternMatch | None:
    """Detect 5+ of last 7 calls being searches."""
    recent = list(window)[-7:]
    if len(recent) < 5:
        return None

    search_count = sum(1 for r in recent if r.category == "search")
    if search_count < 5:
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
            "You're making many recon calls. Do you have enough context "
            "to proceed? Read files via terminal (cat/head)."
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
            "Multiple recon calls returned nothing. Try using more specific "
            "terms, symbol names, or file paths in your task description."
        ),
        suggested_workflow=_SEARCH_WORKFLOW,
    )


_BYPASS_WORKFLOW: dict[str, str] = {
    "for_editing": "Use refactor_edit with find-and-replace — NOT sed, awk, echo, or tee",
    "for_git": "Use checkpoint with commit_message for staging+committing — for other git ops, use terminal",
}


# Pattern checks in priority order:
# 1. Break patterns (block immediately)
# 2. Bypass patterns (terminal command detection — more critical than efficiency)
# 3. General warn patterns (workflow efficiency hints)
_PATTERN_CHECKS = [
    _check_pure_search_chain,  # break
    _check_zero_result_searches,  # warn
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
