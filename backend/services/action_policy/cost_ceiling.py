"""CodePlane USD cost-ceiling policy assessor — a native TraceForge extension.

TraceForge's budget is tool-call *count* based. The retained production adapter
supplies verified ledger USD, with unavailable currency represented by ``None``.
The product surfaces a per-preset dollar ceiling. Rather than translate that spend
into TraceForge's vocabulary (a charter-forbidden bridge), we extend TraceForge
in its own idiom: :class:`JobSpendCeilingAssessor` implements the documented
:class:`~traceforge.governance.rules.PolicyAssessor` protocol
(``assess(ctx, now) -> PolicyDecision | None``) and is registered alongside the
built-in assessors via ``GovernancePipeline(policy_assessors=…)``. Its
``PolicyDecision`` is folded into the verdict by TraceForge's own severity
lattice — no CodePlane ``Tier``, no shape or vocabulary translation.

The assessor is **read-only** (it only reads CP's spend accounting) so it is safe
on the preflight decision path. It is *elevate-only*: it raises ESCALATE at the
hard ceiling or WARN at the soft warn line, and abstains (``None``) otherwise —
TraceForge assessors may only ever increase severity.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from traceforge.governance.rules import PolicyDecision, RecommendedAction

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from decimal import Decimal

    from traceforge.governance import EnrichmentContext

log = structlog.get_logger()

# Reason codes emitted by the USD ceiling. Neither is security-critical (a USD
# ceiling is a spend guard, not a §18.2 hard gate), so both are waivable by an
# operator trust grant — kept out of ``SECURITY_CRITICAL_REASON_CODES``.
CEILING_REASON_CODE = "cp_usd_ceiling"
WARN_REASON_CODE = "cp_usd_ceiling_warn"


class JobSpendCeilingAssessor:
    """Fire ESCALATE/WARN when a job's USD spend crosses configured thresholds.

    ``get_job_spend_usd`` maps a governance session id (which is CodePlane's job
    id — the same key A2 threads through the event pipeline) to the job's current
    verified USD spend, or ``None`` when unavailable. It must be **cheap and synchronous** (an indexed
    read); it is called on the read decision path inside the event loop. A read
    failure fails **open** for the cost dimension (returns no decision) — the USD
    ceiling is a soft spend guard, and the security-critical gates (rules,
    protected paths, the §18.2 pre-check) are independent of it.
    """

    __slots__ = ("_ceiling_usd", "_warn_usd", "_get_job_spend_usd")

    def __init__(
        self,
        ceiling_usd: float | None,
        warn_usd: float | None,
        get_job_spend_usd: Callable[[str], float | Decimal | None],
    ) -> None:
        self._ceiling_usd = ceiling_usd
        self._warn_usd = warn_usd
        self._get_job_spend_usd = get_job_spend_usd

    def assess(self, ctx: EnrichmentContext, now: datetime) -> PolicyDecision | None:
        # No ceiling and no warn line configured → never fires.
        if self._ceiling_usd is None and self._warn_usd is None:
            return None
        session_id = getattr(ctx.event, "session_id", "") or ""
        if not session_id:
            return None
        try:
            spend = self._get_job_spend_usd(session_id)
        except Exception:  # noqa: BLE001 — fail open for the cost dimension only
            log.warning("job_spend_read_failed", session_id=session_id, exc_info=True)
            return None
        if spend is None:
            return None
        if self._ceiling_usd is not None and spend >= self._ceiling_usd:
            return PolicyDecision(action=RecommendedAction.ESCALATE, reason_code=CEILING_REASON_CODE)
        if self._warn_usd is not None and spend >= self._warn_usd:
            return PolicyDecision(action=RecommendedAction.WARN, reason_code=WARN_REASON_CODE)
        return None


def make_job_spend_reader(db_path: str | Path) -> Callable[[str], float]:
    """Retained historical estimate reader, not the production USD authority.

    The replacement retained adapter reads verified currency from its ledger
    instead. This legacy reader preserves old telemetry/re-enrichment behavior.

    Returns a closure ``job_id -> total_cost_usd`` (the main-agent
    ``session_kind='job'`` row of ``job_telemetry_summary`` — exactly what the
    retired ``_get_cost_context`` fed). It is synchronous because TraceForge's
    ``PolicyAssessor.assess`` is synchronous; it opens a short-lived **read-only**
    SQLite connection per call so it never contends with the async writer holding
    the summary table (WAL readers don't block). Any read error returns ``0.0`` so
    a missing/locked telemetry row can never *manufacture* a ceiling breach — the
    assessor also fails open, keeping the USD ceiling a soft guard.
    """
    uri = f"file:{Path(db_path)}?mode=ro"

    def _read(job_id: str) -> float:
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=1.0)
            try:
                cur = conn.execute(
                    "SELECT total_cost_usd FROM job_telemetry_summary WHERE job_id = ? AND session_kind = 'job'",
                    (job_id,),
                )
                row = cur.fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            log.warning("job_spend_reader_failed", job_id=job_id, exc_info=True)
            return 0.0
        if row is None or row[0] is None:
            return 0.0
        return float(row[0])

    return _read
