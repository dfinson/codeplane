"""Compose bounded assessment, AGT selection and durable operator/audit decisions."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import ApprovalAlreadyResolvedError, ApprovalResolution, StateConflictError
from backend.models.events import EventKind, new_event
from backend.models.session_event import EventMetadata
from backend.services.action_policy.agt_policy import POLICY_VERSION, AgtPolicy, FactDecision, PolicyInput
from backend.services.action_policy.assessment import DECISION_SECONDS, ActionBinding, AssessmentService
from backend.services.action_policy.classifier import Preset
from backend.services.action_policy.credential_filter import filter_credentials
from backend.services.action_policy.syntax import analyze_syntax_async
from backend.services.auth.permission_policy import is_hard_gated_syntax

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from backend.models.execution_contracts import ExecutionContractPin
    from backend.persistence.governance_repo import GovernanceLedger, GovernanceTotals
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
    ) -> None:
        self._contracts = contracts
        self._ledger = ledger
        self._approvals = approvals
        self._events = events
        self._assessments = assessments
        self._policy = policy
        self._provider = provider
        self._model = model

    @staticmethod
    def _budget(local: PolicyInput, totals: GovernanceTotals) -> PolicyInput:
        pressure = hard_limit = False
        if local.preset == Preset.autonomous:
            hard_limit = totals.destructive_calls >= 25
        elif local.preset == Preset.supervised:
            pressure = totals.executed_calls >= 750 or totals.destructive_calls >= 10
            if totals.verified_usd_present:
                pressure |= totals.verified_usd >= 20
                hard_limit = totals.verified_usd >= 40
        else:
            pressure = totals.executed_calls >= 250 or totals.destructive_calls >= 3
            hard_limit = totals.executed_calls >= 400
            if totals.verified_usd_present:
                pressure |= totals.verified_usd >= 5
                hard_limit |= totals.verified_usd >= 10
        return replace(
            local,
            hard_gate=local.hard_gate or hard_limit,
            budget_pressure=local.budget_pressure or pressure,
            accounting_unresolved=local.accounting_unresolved or not totals.budget_accountable,
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
        if pin.contract.policy.id != POLICY_VERSION:
            raise StateConflictError("The pinned policy cannot be enforced by this authority")
        source_fields = dict(fields)
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
            },
            fields=source_fields,
        )
        decision_id = str(uuid.uuid4())
        approval_id = checkpoint_ref = None
        totals: GovernanceTotals | None = None
        assessment_reason: str | None = None
        decision = FactDecision("reject", "execution_ineligible", False)
        proceed = False
        try:
            async with asyncio.timeout(DECISION_SECONDS):
                if await is_current():
                    totals = await self._ledger.job_totals(execution.job_id)
                    local = self._budget(local, totals)
                    if source_fields["command"]:
                        syntax = await analyze_syntax_async(source_fields["command"], dialect)
                        local = replace(local, syntax=syntax, hard_gate=local.hard_gate or is_hard_gated_syntax(syntax))
                    # These local decisions never need a model to preserve their priority.
                    preliminary = self._policy.decide(local)
                    if preliminary.reason_code in {"execution_ineligible", "hard_gate"} or local.trusted_file_read:
                        decision = preliminary
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
                        decision = self._policy.decide(local)
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

        if decision.action in {"escalate", "deny"} and await is_current():
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
                requires_explicit_approval=decision.individual_human,
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
                await self._events.publish(
                    new_event(
                        session_id=execution.job_id,
                        kind=EventKind.approval_requested,
                        payload={
                            "approval_id": approval.id,
                            "description": approval.description,
                            "proposed_action": proposed,
                            "requires_explicit_approval": decision.individual_human,
                        },
                        metadata=origin,
                    )
                )
                # Human waits have no machine-decision timeout.
                resolution = await self._approvals.wait_for_resolution(approval.id)
                proceed = resolution == ApprovalResolution.approved
        elif decision.action in {"allow", "warn"}:
            proceed = True
        if not await is_current():
            proceed = False
            decision = FactDecision("reject", "execution_ineligible", False)

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
                    "budget_snapshot": None
                    if totals is None
                    else {
                        "executed_calls": totals.executed_calls,
                        "destructive_calls": totals.destructive_calls,
                        "unaccounted": totals.unaccounted,
                        "verified_usd": str(totals.verified_usd) if totals.verified_usd_present else None,
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
        return Authorization(proceed, decision, decision_id, binding, approval_id, checkpoint_ref)
