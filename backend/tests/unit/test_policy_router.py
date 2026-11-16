"""Tests for backend.services.action_policy.router — PolicyRouter.

The router no longer owns any policy logic: it acts on the normalized
``RecommendedAction`` produced by ``GovernanceDecider.classify`` and turns it into
a concrete outcome (proceed / git savepoint / LLM monitor / human approval gate).
Trust is applied inside governance, so the router keeps no separate trust check.
These assert the enforcement bindings (SPEC §18.3 posture lives in the profiles):
ALLOW proceeds; WARN/TRANSFORM savepoint+proceed; ESCALATE runs the monitor then
gates; DENY skips the monitor and gates; a security-critical ESCALATE bypasses the
monitor; a full human approval grants a time-boxed reason-code trust waiver.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.action_policy.batcher import BatchResolution, BatchResult
from backend.services.action_policy.classifier import Action, ActionKind, Classification, RecommendedAction
from backend.services.action_policy.monitor import MonitorVerdict
from backend.services.action_policy.router import Decision, PolicyRouter


def _cls(ra: RecommendedAction, reason_code: str = "allow") -> Classification:
    return Classification(
        recommended_action=ra,
        reason_code=reason_code,
        risk_score=0,
        risk_band="low",
        effect=None,
        mechanism="unknown",
        reason=f"{ra.value}: {reason_code}",
    )


@pytest.fixture()
def checkpoint_svc() -> MagicMock:
    svc = MagicMock()
    svc.create = AsyncMock(return_value="cp-ref-1")
    svc.cleanup_job = MagicMock()
    return svc


@pytest.fixture()
def governance() -> MagicMock:
    g = MagicMock()
    g.classify = MagicMock(return_value=_cls(RecommendedAction.ALLOW))
    g.grant_trust = MagicMock(return_value=True)
    return g


@pytest.fixture()
def batcher() -> MagicMock:
    b = MagicMock()
    b.submit_and_wait = AsyncMock(return_value=BatchResult(resolution=BatchResolution.approved))
    b.cleanup_job = MagicMock()
    return b


@pytest.fixture()
def router(checkpoint_svc: MagicMock, governance: MagicMock, batcher: MagicMock) -> PolicyRouter:
    return PolicyRouter(checkpoint_svc, governance, batcher)


def _action() -> Action:
    return Action(kind=ActionKind.shell, command="ls", job_id="j1")


class TestDecision:
    def test_defaults(self) -> None:
        d = Decision(recommended_action=RecommendedAction.ALLOW, proceed=True)
        assert d.recommended_action == RecommendedAction.ALLOW
        assert d.proceed is True
        assert d.checkpoint_ref is None
        assert d.batch_id is None
        assert d.monitor_approved is False
        assert d.classification is None


class TestAllow:
    async def test_allow_proceeds_without_savepoint(
        self, router: PolicyRouter, governance: MagicMock, checkpoint_svc: MagicMock
    ) -> None:
        governance.classify.return_value = _cls(RecommendedAction.ALLOW)
        decision = await router.route(_action(), cwd="/work")
        assert decision.proceed is True
        assert decision.recommended_action == RecommendedAction.ALLOW
        assert decision.classification is not None
        checkpoint_svc.create.assert_not_awaited()


class TestWarnTransform:
    async def test_warn_savepoints_and_proceeds(
        self, router: PolicyRouter, governance: MagicMock, checkpoint_svc: MagicMock
    ) -> None:
        governance.classify.return_value = _cls(RecommendedAction.WARN, "mutating_savepoint")
        decision = await router.route(_action(), cwd="/work")
        assert decision.proceed is True
        assert decision.recommended_action == RecommendedAction.WARN
        assert decision.checkpoint_ref == "cp-ref-1"
        checkpoint_svc.create.assert_awaited_once()

    async def test_transform_treated_as_warn(
        self, router: PolicyRouter, governance: MagicMock, checkpoint_svc: MagicMock
    ) -> None:
        governance.classify.return_value = _cls(RecommendedAction.TRANSFORM, "advisory")
        decision = await router.route(_action(), cwd="/work")
        assert decision.proceed is True
        assert decision.recommended_action == RecommendedAction.TRANSFORM
        assert decision.checkpoint_ref == "cp-ref-1"
        checkpoint_svc.create.assert_awaited_once()

    async def test_warn_without_cwd_skips_savepoint(
        self, router: PolicyRouter, governance: MagicMock, checkpoint_svc: MagicMock
    ) -> None:
        governance.classify.return_value = _cls(RecommendedAction.WARN, "mutating_savepoint")
        decision = await router.route(_action(), cwd=None)
        assert decision.proceed is True
        assert decision.checkpoint_ref is None
        checkpoint_svc.create.assert_not_awaited()


class TestEscalateGate:
    async def test_escalate_gates_and_grants_trust_on_approval(
        self, router: PolicyRouter, governance: MagicMock, batcher: MagicMock
    ) -> None:
        governance.classify.return_value = _cls(RecommendedAction.ESCALATE, "mutating_with_network")
        batcher.submit_and_wait.return_value = BatchResult(resolution=BatchResolution.approved)
        decision = await router.route(_action(), cwd="/work")
        assert decision.proceed is True
        batcher.submit_and_wait.assert_awaited_once()
        # A full human approval grants a time-boxed reason-code trust waiver.
        governance.grant_trust.assert_called_once()
        assert governance.grant_trust.call_args.args[1] == "mutating_with_network"

    async def test_escalate_rejected_does_not_proceed_or_grant(
        self, router: PolicyRouter, governance: MagicMock, batcher: MagicMock
    ) -> None:
        governance.classify.return_value = _cls(RecommendedAction.ESCALATE, "mutating_with_network")
        batcher.submit_and_wait.return_value = BatchResult(resolution=BatchResolution.rejected)
        decision = await router.route(_action(), cwd="/work")
        assert decision.proceed is False
        governance.grant_trust.assert_not_called()

    async def test_partial_proceeds_without_trust_grant(
        self, router: PolicyRouter, governance: MagicMock, batcher: MagicMock
    ) -> None:
        governance.classify.return_value = _cls(RecommendedAction.ESCALATE, "mutating_with_network")
        batcher.submit_and_wait.return_value = BatchResult(resolution=BatchResolution.partial)
        decision = await router.route(_action(), cwd="/work")
        assert decision.proceed is True
        # Only a *full* approval grants trust — a partial does not.
        governance.grant_trust.assert_not_called()


class TestDenyAndMonitor:
    async def test_deny_skips_monitor_and_gates(
        self, checkpoint_svc: MagicMock, governance: MagicMock, batcher: MagicMock
    ) -> None:
        monitor = MagicMock()
        monitor.evaluate = AsyncMock(return_value=(MonitorVerdict.approve, "ok"))
        router = PolicyRouter(checkpoint_svc, governance, batcher, monitor=monitor)
        governance.classify.return_value = _cls(RecommendedAction.DENY, "piped_network_exec")
        batcher.submit_and_wait.return_value = BatchResult(resolution=BatchResolution.rejected)
        decision = await router.route(_action(), cwd="/work")
        # DENY never consults the monitor — it always reaches a human.
        monitor.evaluate.assert_not_awaited()
        batcher.submit_and_wait.assert_awaited_once()
        assert decision.proceed is False

    async def test_monitor_auto_approves_escalate(
        self, checkpoint_svc: MagicMock, governance: MagicMock, batcher: MagicMock
    ) -> None:
        monitor = MagicMock()
        monitor.evaluate = AsyncMock(return_value=(MonitorVerdict.approve, "related to project"))
        router = PolicyRouter(checkpoint_svc, governance, batcher, monitor=monitor)
        governance.classify.return_value = _cls(RecommendedAction.ESCALATE, "mutating_with_network")
        decision = await router.route(_action(), cwd="/work")
        assert decision.proceed is True
        assert decision.monitor_approved is True
        monitor.evaluate.assert_awaited_once()
        # Auto-approval means no human gate.
        batcher.submit_and_wait.assert_not_awaited()

    async def test_security_critical_escalate_bypasses_monitor(
        self, checkpoint_svc: MagicMock, governance: MagicMock, batcher: MagicMock
    ) -> None:
        monitor = MagicMock()
        monitor.evaluate = AsyncMock(return_value=(MonitorVerdict.approve, "ok"))
        router = PolicyRouter(checkpoint_svc, governance, batcher, monitor=monitor)
        # destructive_action is security-critical → must reach a human, never the monitor.
        governance.classify.return_value = _cls(RecommendedAction.ESCALATE, "destructive_action")
        decision = await router.route(_action(), cwd="/work")
        monitor.evaluate.assert_not_awaited()
        batcher.submit_and_wait.assert_awaited_once()
        assert decision.monitor_approved is False


class TestCleanup:
    def test_cleanup_job_delegates(self, router: PolicyRouter, checkpoint_svc: MagicMock, batcher: MagicMock) -> None:
        router.cleanup_job("j1")
        checkpoint_svc.cleanup_job.assert_called_once_with("j1")
        batcher.cleanup_job.assert_called_once_with("j1")
