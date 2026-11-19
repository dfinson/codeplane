"""Compose bounded assessment, AGT selection and durable operator/audit decisions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import ApprovalAlreadyResolvedError, ApprovalResolution, StateConflictError
from backend.models.events import EventKind, new_event
from backend.models.session_event import EventMetadata
from backend.services.action_policy.agt_policy import POLICY_VERSION, AgtPolicy, FactDecision, PolicyInput
from backend.services.action_policy.assessment import DECISION_SECONDS, ActionBinding, AssessmentService
from backend.services.action_policy.batcher import BatchResolution
from backend.services.action_policy.classifier import (
    USD_CEILING_DEFAULTS,
    WAIVABLE_REASON_CODES,
    Action,
    Classification,
    Preset,
    RecommendedAction,
    RepoPolicy,
)
from backend.services.action_policy.credential_filter import filter_credentials
from backend.services.action_policy.monitor import MonitorVerdict
from backend.services.action_policy.syntax import analyze_syntax_async
from backend.services.auth.permission_policy import is_hard_gated_syntax

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from backend.models.events import SessionEvent
    from backend.models.execution_contracts import ExecutionContractPin
    from backend.persistence.governance_repo import GovernanceLedger, GovernanceTotals
    from backend.services.action_policy.batcher import ApprovalBatcher
    from backend.services.adapters.contracts import ExecutionIdentity, TextCompletionProvider
    from backend.services.events.event_bus import EventBus
    from backend.services.job.approval_service import ApprovalService
    from backend.services.runtime.execution_contracts import ExecutionContracts

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Authorization:
    proceed: bool
    decision: FactDecision
    decision_id: str
    binding: ActionBinding
    approval_id: str | None = None
    checkpoint_ref: str | None = None


class PolicyAuthority:
    def __init__(
        self,
        *,
        contracts: ExecutionContracts,
        ledger: GovernanceLedger,
        approvals: ApprovalService,
        events: EventBus,
        assessments: AssessmentService,
        policy: AgtPolicy,
        provider: TextCompletionProvider | None,
        model: str | None,
        preserve_pending_on_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self._contracts = contracts
        self._ledger = ledger
        self._approvals = approvals
        self._events = events
        self._assessments = assessments
        self._policy = policy
        self._provider = provider
        self._model = model
        self._preserve_pending_on_cancel = preserve_pending_on_cancel

    @staticmethod
    def _budget(local: PolicyInput, totals: GovernanceTotals) -> PolicyInput:
        pressure = hard_limit = False
        if local.preset == Preset.autonomous:
            hard_limit = totals.destructive_calls >= 25
        elif local.preset == Preset.supervised:
            pressure = totals.executed_calls >= 750 or totals.destructive_calls >= 10
        else:
            pressure = totals.executed_calls >= 250 or totals.destructive_calls >= 3
            hard_limit = totals.executed_calls >= 400
        return replace(
            local,
            hard_gate=local.hard_gate or hard_limit,
            budget_pressure=local.budget_pressure or pressure,
            accounting_unresolved=local.accounting_unresolved or not totals.budget_accountable,
        )

    @staticmethod
    def policy_scope(pin: ExecutionContractPin, settings: RepoPolicy) -> str:
        return hashlib.sha256(
            json.dumps(
                {"policy": pin.contract.policy.model_dump(mode="json"), "settings": asdict(settings)},
                sort_keys=True,
                allow_nan=False,
            ).encode()
        ).hexdigest()

    def _select(
        self, local: PolicyInput, totals: GovernanceTotals, settings: RepoPolicy, grants: frozenset[str]
    ) -> FactDecision:
        if "budget_pressure" in grants:
            local = replace(local, budget_pressure=False)
        decision = self._policy.decide(local)
        if decision.individual_human or decision.action in {"reject", "deny"}:
            return decision
        if decision.reason_code in grants & WAIVABLE_REASON_CODES:
            decision = replace(decision, action="allow", monitor_eligible=False)
        warn, ceiling = settings.usd_ceilings.get(str(local.preset), USD_CEILING_DEFAULTS[local.preset])
        if totals.verified_usd_present:
            if (
                ceiling is not None
                and totals.verified_usd >= Decimal(str(ceiling))
                and "cp_usd_ceiling" not in grants
                and decision.action in {"allow", "warn"}
            ):
                return FactDecision("escalate", "cp_usd_ceiling", False, monitor_eligible=local.preset != Preset.locked)
            if (
                warn is not None
                and totals.verified_usd >= Decimal(str(warn))
                and "cp_usd_ceiling_warn" not in grants
                and decision.action == "allow"
            ):
                return FactDecision("warn", "cp_usd_ceiling_warn", False)
        return decision

    async def trust_job(self, pin: ExecutionContractPin, settings: RepoPolicy, epoch: str) -> None:
        self._contracts.require_available(pin)
        await self._ledger.require_policy_scope(pin.job_id, pin.contract.policy.id)
        await self._ledger.grant_reasons(
            pin.job_id,
            self.policy_scope(pin, settings),
            WAIVABLE_REASON_CODES,
            epoch=epoch,
            source_id=f"operator:{uuid.uuid4()}",
        )

    async def authorize(
        self,
        *,
        pin: ExecutionContractPin,
        execution: ExecutionIdentity,
        local: PolicyInput,
        fields: Mapping[str, str],
        is_current: Callable[[], Awaitable[bool]],
        dialect: str | None = None,
        savepoint: Callable[[], Awaitable[str | None]] | None = None,
        metadata: EventMetadata | None = None,
        tool_call_id: str | None = None,
        approval_callback: Callable[[SessionEvent], None] | None = None,
        decision_deadline: float | None = None,
        recovery_is_current: Callable[[float], Awaitable[bool]] | None = None,
        settings: RepoPolicy | None = None,
        action: Action | None = None,
        batcher: ApprovalBatcher | None = None,
        monitor: Callable[[Action, Classification], Awaitable[tuple[MonitorVerdict, str]]] | None = None,
    ) -> Authorization:
        """The caller owns interception and supplies a live ownership/policy fence.

        Local facts must come from trusted application inspection, never a model
        or producer hint. This method does not execute or retry the business call.
        """
        if (
            pin.job_id != execution.job_id
            or pin.budget_id != execution.job_id
            or local.job_id != execution.job_id
            or not all((execution.job_id, execution.binding_id, execution.epoch, execution.execution_token))
        ):
            raise ValueError("Policy invocation requires one exact Job, admission and live execution identity")
        if local.assessment is not None or local.syntax is not None:
            raise ValueError("Policy authority derives assessment and syntax from the bound source fields")
        if set(fields) != {"command", "arguments", "tool_definition", "context"} or any(
            not isinstance(value, str) for value in fields.values()
        ):
            raise ValueError("Policy invocation requires the exact four source fields")
        origin = metadata or EventMetadata()
        if (
            origin.integration_binding_id is not None
            or (origin.execution_epoch is not None and origin.execution_epoch != execution.epoch)
            or (origin.execution_token is not None and origin.execution_token != execution.execution_token)
        ):
            raise ValueError("Policy event scope differs from the actual execution")
        origin = origin.model_copy(
            update={
                "source_framework": "codeplane",
                "namespace": ("control", "policy"),
                "execution_epoch": execution.epoch,
                "execution_token": execution.execution_token,
            }
        )
        self._contracts.require_available(pin)
        selected_policy = POLICY_VERSION
        if pin.contract.policy.id != selected_policy:
            raise StateConflictError("The pinned policy cannot be enforced by this authority")
        await self._ledger.require_policy_scope(execution.job_id, pin.contract.policy.id)
        settings = settings or RepoPolicy(preset=local.preset)
        policy_scope = self.policy_scope(pin, settings)
        grants: frozenset[str] = frozenset()
        source_fields = dict(fields)
        if action is not None and (
            action.job_id != execution.job_id
            or (action.command or "") != source_fields["command"]
            or action.arguments_json != source_fields["arguments"]
            or action.tool_definition != source_fields["tool_definition"]
        ):
            raise ValueError("Monitor/batch action differs from the exact policy invocation")
        if source_fields["command"]:
            local = replace(local, trusted_file_read=False)
        binding = ActionBinding.create(
            execution.job_id,
            execution.epoch,
            {
                "execution": asdict(execution),
                "admission": pin.model_dump(mode="json"),
                "local": asdict(local),
                "dialect": dialect,
                "metadata": origin.model_dump(mode="json"),
                "tool_call_id": tool_call_id,
                "policy_scope": policy_scope,
            },
            fields=source_fields,
        )
        decision_id = str(uuid.uuid4())
        approval_id = checkpoint_ref = None
        totals: GovernanceTotals | None = None
        assessment_reason: str | None = None
        decision = FactDecision("reject", "execution_ineligible", False)
        proceed = False
        human_grant = False
        monitor_verdict: MonitorVerdict | None = None
        monitor_evidence: str | None = None
        deadline = (
            decision_deadline if decision_deadline is not None else asyncio.get_running_loop().time() + DECISION_SECONDS
        )
        try:
            async with asyncio.timeout_at(deadline):
                if await is_current():
                    grants = await self._ledger.active_reason_grants(execution.job_id, policy_scope)
                    totals = await self._ledger.job_totals(execution.job_id)
                    local = self._budget(local, totals)
                    if source_fields["command"]:
                        syntax = await analyze_syntax_async(source_fields["command"], dialect)
                        local = replace(local, syntax=syntax, hard_gate=local.hard_gate or is_hard_gated_syntax(syntax))
                    # These local decisions never need a model to preserve their priority.
                    preliminary = self._policy.decide(local)
                    if preliminary.reason_code in {"execution_ineligible", "hard_gate"} or local.trusted_file_read:
                        decision = self._select(local, totals, settings, grants)
                    else:
                        result = await self._assessments.assess(
                            binding,
                            source_fields,
                            provider=self._provider,
                            model=self._model,
                            is_current=is_current,
                            execution_contract=pin,
                        )
                        assessment_reason = result.reason
                        if result.completion is not None:
                            for usage in result.completion.usage:
                                if usage.job_id != execution.job_id or usage.execution_epoch != execution.epoch:
                                    raise ValueError("Assessment usage does not belong to the bound execution")
                                await self._ledger.record_usage(
                                    result.completion.usage_event_id or f"{decision_id}:assessment",
                                    usage,
                                )
                        # Utility usage belongs to the same Job budget as execution.
                        totals = await self._ledger.job_totals(execution.job_id)
                        local = self._budget(local, totals)
                        local = replace(local, assessment=result.assessment)
                        decision = self._select(local, totals, settings, grants)
                if not await is_current():
                    decision = FactDecision("reject", "execution_ineligible", False)
        except TimeoutError:
            decision = FactDecision("escalate", "decision_timeout", True)

        if decision.action != "reject" and not await is_current():
            decision = FactDecision("reject", "execution_ineligible", False)
        if decision.action == "warn":
            if savepoint is None:
                decision = FactDecision("escalate", "savepoint_unavailable", True)
            else:
                checkpoint_ref = await savepoint()
                if not checkpoint_ref:
                    decision = FactDecision("escalate", "savepoint_unavailable", True)

        decision = replace(decision, policy_version=selected_policy)
        if decision.action in {"escalate", "deny"} and not decision.individual_human and savepoint is not None:
            checkpoint_ref = await savepoint()
        if decision.monitor_eligible and monitor is not None and action is not None and await is_current():
            from backend.services.adapters.completion_context import CompletionContext, completion_context

            try:
                async with asyncio.timeout_at(deadline):
                    with completion_context(
                        CompletionContext(
                            role="monitor",
                            job_id=execution.job_id,
                            execution_epoch=execution.epoch,
                            execution_contract=pin,
                            max_output_bytes=512,
                        )
                    ):
                        monitor_verdict, monitor_evidence = await monitor(
                            action,
                            Classification(
                                RecommendedAction(decision.action),
                                decision.reason_code,
                                decision.risk_score,
                                decision.risk_band,
                                local.local_effect,
                                "native",
                                decision.reason_code,
                            ),
                        )
                await self._events.publish(
                    new_event(
                        execution.job_id,
                        {
                            MonitorVerdict.approve: EventKind.monitor_approved,
                            MonitorVerdict.reject: EventKind.monitor_rejected,
                            MonitorVerdict.escalate: EventKind.monitor_escalated,
                        }[monitor_verdict],
                        {"decision_id": decision_id, "reason_code": decision.reason_code, "evidence": monitor_evidence},
                        metadata=origin,
                    )
                )
            except TimeoutError:
                monitor_verdict = MonitorVerdict.escalate
                monitor_evidence = "Monitor decision deadline exceeded"
                log.warning("policy_monitor_timeout", job_id=execution.job_id, decision_id=decision_id)
            proceed = monitor_verdict == MonitorVerdict.approve
        if (
            decision.action in {"escalate", "deny"}
            and monitor_verdict not in {MonitorVerdict.approve, MonitorVerdict.reject}
            and await is_current()
        ):
            proposed = json.dumps(
                {
                    "kind": "policy_action",
                    "decision_id": decision_id,
                    "binding_sha256": binding.sha256,
                    "execution": asdict(execution),
                    "admission_id": pin.admission_id,
                    "policy": pin.contract.policy.model_dump(mode="json"),
                    "reason_code": decision.reason_code,
                    **({"correlation_id": origin.correlation_id} if origin.correlation_id is not None else {}),
                    "fields": {key: filter_credentials(value).text for key, value in source_fields.items()},
                },
                sort_keys=True,
                ensure_ascii=True,
            )
            approval = await self._approvals.create_request(
                execution.job_id,
                f"Policy approval required: {decision.reason_code}",
                proposed,
                # Application-owned grants, never ApprovalService's unbounded SDK trust flag,
                # determine whether a later policy action is waived.
                requires_explicit_approval=True,
            )
            approval_id = approval.id
            if not await is_current():
                try:
                    await self._approvals.resolve(
                        approval.id,
                        ApprovalResolution.rejected,
                        notes="Originating policy invocation is no longer live",
                    )
                except ApprovalAlreadyResolvedError:
                    log.info("policy_stale_approval_already_resolved", approval_id=approval.id)
            else:
                batch_managed = batcher is not None and action is not None and not decision.individual_human
                requested = new_event(
                    session_id=execution.job_id,
                    kind=EventKind.approval_requested,
                    payload={
                        "approval_id": approval.id,
                        "description": approval.description,
                        "proposed_action": proposed,
                        "requires_explicit_approval": True,
                        "batch_managed": batch_managed,
                    },
                    metadata=origin,
                )
                try:
                    if approval_callback is None:
                        await self._events.publish(requested)
                    else:
                        approval_callback(requested)
                    if batcher is not None and action is not None and not decision.individual_human:
                        batch_wait = asyncio.create_task(
                            batcher.submit_and_wait(
                                execution.job_id,
                                action,
                                Classification(
                                    RecommendedAction(decision.action),
                                    decision.reason_code,
                                    decision.risk_score,
                                    decision.risk_band,
                                    local.local_effect,
                                    "native",
                                    decision.reason_code,
                                ),
                                checkpoint_ref or "",
                                action_id=decision_id,
                                metadata=origin,
                                approval_id=approval.id,
                            )
                        )
                        approval_wait = asyncio.create_task(self._approvals.wait_for_resolution(approval.id))
                        try:
                            done, _ = await asyncio.wait(
                                {batch_wait, approval_wait}, return_when=asyncio.FIRST_COMPLETED
                            )
                            if approval_wait in done:
                                resolution = approval_wait.result()
                            else:
                                batch_result = batch_wait.result()
                                approved = batch_result.resolution == BatchResolution.approved or (
                                    batch_result.resolution == BatchResolution.partial
                                    and decision_id in batch_result.approved_ids
                                )
                                resolution = ApprovalResolution.approved if approved else ApprovalResolution.rejected
                                try:
                                    await self._approvals.resolve(
                                        approval.id, resolution, notes=f"Batch {batch_result.resolution}"
                                    )
                                except ApprovalAlreadyResolvedError:
                                    resolution = await self._approvals.wait_for_resolution(approval.id)
                                human_grant = (
                                    resolution == ApprovalResolution.approved
                                    and batch_result.resolution == BatchResolution.approved
                                )
                        finally:
                            batch_wait.cancel()
                            approval_wait.cancel()
                            await asyncio.gather(batch_wait, approval_wait, return_exceptions=True)
                    else:
                        # Human waits have no machine-decision timeout.
                        resolution = await self._approvals.wait_for_resolution(approval.id)
                        human_grant = resolution == ApprovalResolution.approved and not decision.individual_human
                except BaseException:
                    if self._preserve_pending_on_cancel is None or not self._preserve_pending_on_cancel():
                        try:
                            await self._approvals.resolve(
                                approval.id,
                                ApprovalResolution.rejected,
                                notes="Originating policy invocation ended before approval was consumed",
                            )
                        except ApprovalAlreadyResolvedError:
                            log.info("policy_ended_approval_already_resolved", approval_id=approval.id)
                    raise
                proceed = resolution == ApprovalResolution.approved
        elif decision.action in {"allow", "warn"}:
            proceed = True
        if not await is_current():
            proceed = False
            decision = FactDecision("reject", "execution_ineligible", False)
        if proceed and recovery_is_current is not None:
            consumption_deadline = (
                asyncio.get_running_loop().time() + DECISION_SECONDS if approval_id is not None else deadline
            )
            if not await recovery_is_current(consumption_deadline):
                proceed = False
                decision = FactDecision("reject", "execution_ineligible", False)
        if proceed and grants and not grants <= await self._ledger.active_reason_grants(execution.job_id, policy_scope):
            proceed = False
            decision = FactDecision("reject", "reason_grant_expired", False)

        try:
            await self._ledger.enqueue_decision(
                decision_id=decision_id,
                job_id=execution.job_id,
                epoch=execution.epoch,
                action_digest=binding.sha256,
                decision="allow" if proceed else "reject",
                metadata={
                    "admission_id": pin.admission_id,
                    "budget_id": pin.budget_id,
                    "preset": str(local.preset),
                    "contract": pin.contract.model_dump(mode="json"),
                    "execution": asdict(execution),
                    "metadata": origin.model_dump(mode="json"),
                    "tool_call_id": tool_call_id,
                    "destructive": (
                        local.local_effect == "destructive"
                        if local.local_effect not in {None, "unknown"}
                        else local.assessment.effect == "destructive"
                        if local.assessment is not None and local.assessment.effect != "unknown"
                        else False
                        if local.trusted_file_read
                        else None
                    ),
                    "policy_action": decision.action,
                    "reason_code": decision.reason_code,
                    "approval_id": approval_id,
                    "checkpoint_ref": checkpoint_ref,
                    "assessment_reason": assessment_reason,
                    "policy_scope": policy_scope,
                    "active_reason_grants": sorted(grants),
                    "monitor_verdict": monitor_verdict,
                    "monitor_evidence": monitor_evidence,
                    "git_recovery": asdict(local.git_recovery) if local.git_recovery is not None else None,
                    "retained_decision": None,
                    "retained_action": None,
                    "budget_snapshot": None
                    if totals is None
                    else {
                        "executed_calls": totals.executed_calls,
                        "destructive_calls": totals.destructive_calls,
                        "unaccounted": totals.unaccounted,
                        "verified_usd": str(totals.verified_usd) if totals.verified_usd_present else None,
                        "usd_guard_status": "verified_only" if totals.verified_usd_present else "abstained_unavailable",
                        "legacy_estimated_usd": str(totals.legacy_estimated_usd)
                        if totals.legacy_estimated_usd is not None
                        else None,
                        "legacy_currency_unverified": totals.legacy_currency_unverified,
                    },
                },
            )
        except (OSError, sqlite3.DatabaseError):
            log.error(
                "permission_audit_enqueue_failed",
                job_id=execution.job_id,
                decision_id=decision_id,
                exc_info=True,
            )
            proceed = False
        if not await is_current():
            proceed = False
        if proceed and human_grant and decision.reason_code in WAIVABLE_REASON_CODES:
            await self._ledger.grant_reasons(
                execution.job_id,
                policy_scope,
                frozenset({decision.reason_code}),
                epoch=execution.epoch,
                source_id=decision_id,
            )
            if not await is_current():
                await self._ledger.revoke_reason_grants(execution.job_id, decision_id)
                proceed = False
        return Authorization(
            proceed,
            replace(decision, policy_version=selected_policy),
            decision_id,
            binding,
            approval_id,
            checkpoint_ref,
        )
