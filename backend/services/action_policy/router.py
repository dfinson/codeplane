"""Action policy router — central enforcement point for every agent action.

The router **acts on** an application ``RecommendedAction`` produced by the
:class:`~backend.services.action_policy.governance.GovernanceDecider`. It owns no
policy logic of its own — classification, rules, budget, and trust waivers all
live in the governance substrate. The router is CodePlane's product/enforcement
surface: it turns a recommendation into a concrete outcome (proceed, git
savepoint, LLM auto-approval, or human approval gate).

Enforcement bindings (SPEC §18.3 posture is encoded in the per-preset governance
profiles, not here):

* ``ALLOW``      → proceed, no interruption.
* ``WARN``       → git savepoint + proceed + notify (operator can intervene).
* ``TRANSFORM``  → treated as ``WARN`` (advisory; CodePlane applies no auto-edit).
* ``ESCALATE``   → LLM monitor (if enabled and not security-critical) → human gate.
* ``DENY``       → human approval gate (block-leaning; CodePlane has no hard
  auto-block, so the strongest surface is an operator decision). The monitor is
  skipped — a DENY always reaches a human.

Trust is applied **inside** governance (an active reason-code grant makes the
decision come back ``ALLOW``), so the router keeps no separate trust check.
Security-critical verdicts (SPEC §18.2 / binding condition §3) skip the LLM
auto-approver and go straight to the human gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from backend.services.action_policy.batcher import BatchResolution, BatchResult
from backend.services.action_policy.classifier import RecommendedAction, is_security_critical
from backend.services.action_policy.monitor import MonitorSession, MonitorVerdict

if TYPE_CHECKING:
    from backend.services.action_policy.batcher import ApprovalBatcher
    from backend.services.action_policy.checkpoint_service import CheckpointService
    from backend.services.action_policy.classifier import Action, Classification
    from backend.services.action_policy.governance import GovernanceDecider

log = structlog.get_logger()

# Human-approved reason-code grants are time-boxed so an approval does not become
# a permanent escalation — the operator must re-approve after it expires. 24h
# aligns with a working day. (Security-critical reason codes are never granted.)
_HUMAN_GRANT_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class Decision:
    """Result of routing an action through governance + enforcement."""

    recommended_action: RecommendedAction
    proceed: bool
    checkpoint_ref: str | None = None
    batch_id: str | None = None
    monitor_approved: bool = False
    monitor_evidence: str | None = None
    classification: Classification | None = None


class PolicyRouter:
    """Enforce a governance ``RecommendedAction``: proceed / savepoint / gate."""

    def __init__(
        self,
        checkpoint_service: CheckpointService,
        governance: GovernanceDecider,
        batcher: ApprovalBatcher,
        *,
        monitor: MonitorSession | None = None,
    ) -> None:
        self._checkpoint = checkpoint_service
        self._governance = governance
        self._batcher = batcher
        self._monitor = monitor

    async def route(self, action: Action, *, cwd: str | None = None) -> Decision:
        """Score an action through governance and enforce the recommendation.

        For ``ESCALATE``/``DENY`` without an auto-approval this blocks until the
        operator resolves the batch.
        """
        classification = self._governance.classify(action)
        ra = classification.recommended_action

        # ALLOW — no interruption (covers governance-waived actions too).
        if ra == RecommendedAction.ALLOW:
            return Decision(recommended_action=ra, proceed=True, classification=classification)

        # WARN / TRANSFORM — savepoint + proceed + notify (advisory).
        if ra in (RecommendedAction.WARN, RecommendedAction.TRANSFORM):
            checkpoint_ref = await self._savepoint(action, classification, cwd)
            return Decision(
                recommended_action=ra,
                proceed=True,
                checkpoint_ref=checkpoint_ref,
                classification=classification,
            )

        # ESCALATE / DENY — gate. Savepoint first so the operator can roll back.
        checkpoint_ref = await self._savepoint(action, classification, cwd)

        # LLM monitor auto-approver — only for ESCALATE, only when the verdict is
        # not security-critical, and only when a monitor is wired (non-locked).
        monitor_eligible = (
            self._monitor is not None
            and ra == RecommendedAction.ESCALATE
            and not is_security_critical(classification.reason_code)
        )
        if monitor_eligible:
            assert self._monitor is not None
            verdict, evidence = await self._monitor.evaluate(action, classification)

            if verdict == MonitorVerdict.approve:
                log.info("monitor_approved", action_kind=action.kind, evidence=evidence)
                return Decision(
                    recommended_action=ra,
                    proceed=True,
                    checkpoint_ref=checkpoint_ref,
                    monitor_approved=True,
                    monitor_evidence=evidence,
                    classification=classification,
                )

            if verdict == MonitorVerdict.reject:
                log.info("monitor_rejected", action_kind=action.kind, evidence=evidence)
                return Decision(
                    recommended_action=ra,
                    proceed=False,
                    checkpoint_ref=checkpoint_ref,
                    monitor_evidence=evidence,
                    classification=classification,
                )

            log.info("monitor_escalated", action_kind=action.kind, evidence=evidence)

        # Human approval gate — submit to the batcher and block for a resolution.
        result: BatchResult = await self._batcher.submit_and_wait(
            action.job_id or "",
            action,
            classification,
            checkpoint_ref or "",
        )
        proceed = result.resolution in (BatchResolution.approved, BatchResolution.partial)

        # A full human approval grants a time-boxed reason-code trust waiver so the
        # operator is not re-asked for the same class of action. Security-critical
        # reason codes are refused by ``grant_trust`` (binding condition §3).
        if proceed and result.resolution == BatchResolution.approved:
            self._governance.grant_trust(
                action.job_id or "",
                classification.reason_code,
                _HUMAN_GRANT_TTL_SECONDS,
                reason="human approved",
            )

        return Decision(
            recommended_action=ra,
            proceed=proceed,
            checkpoint_ref=checkpoint_ref,
            classification=classification,
        )

    async def _savepoint(self, action: Action, classification: Classification, cwd: str | None) -> str | None:
        """Create a git savepoint before a non-trivial action (best effort)."""
        if not cwd:
            return None
        return await self._checkpoint.create(
            action.job_id or "",
            classification.reason,
            cwd=cwd,
        )

    def cleanup_job(self, job_id: str) -> None:
        """Clean up router state for a completed/failed job."""
        self._checkpoint.cleanup_job(job_id)
        self._batcher.cleanup_job(job_id)
        self._monitor = None  # release monitor resources
