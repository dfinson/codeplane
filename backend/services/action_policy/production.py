"""Own the local standalone catalogue, assessment pool, ledger and audit lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import StateConflictError
from backend.models.events import EventKind, SessionEvent
from backend.models.usage import UsageAmount
from backend.persistence.governance_ownership import GovernanceRuntimeLease
from backend.persistence.governance_repo import GovernanceLedger
from backend.services.action_policy.agt_policy import AgtPolicy
from backend.services.action_policy.assessment import AssessmentService
from backend.services.action_policy.audit import AuditIntegrityError, GovernanceAudit
from backend.services.action_policy.authority import PolicyAuthority
from backend.services.action_policy.standalone_router import StandalonePolicyRouter
from backend.services.events.title_refinement import TitleRefinement
from backend.services.runtime.contract_catalog import standalone_contract

if TYPE_CHECKING:
    from pathlib import Path

    from backend.models.execution_contracts import ExecutionContractPin
    from backend.services.action_policy.classifier import RepoPolicy
    from backend.services.runtime.service import RuntimeService

log = structlog.get_logger()


class StandaloneGovernance:
    def __init__(self, host: RuntimeService, directory: Path) -> None:
        self._host = host
        self._ledger = GovernanceLedger(directory / "governance.db", application_database=directory / "data.db")
        self._runtime_ownership = GovernanceRuntimeLease(directory)
        self._audit = GovernanceAudit(directory / "audit", self._ledger)
        self._assessments = AssessmentService()
        self._titles = TitleRefinement(host, self._assessments)
        self._policy = AgtPolicy()
        self._delivery: asyncio.Task[None] | None = None
        self._closing: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.audit_error: str | None = None
        self.accounting_error: str | None = None

    async def start(self) -> None:
        self._runtime_ownership.acquire()
        started = False
        try:
            await self._start()
            started = True
        finally:
            if not started:
                await self.close()

    async def _start(self) -> None:
        from backend.persistence.legacy_governance_repo import LegacyGovernanceRepository

        await asyncio.to_thread(
            LegacyGovernanceRepository(self._ledger.path, self._ledger.path.parent / "data.db").require_verified
        )
        contract = await asyncio.to_thread(standalone_contract)
        try:
            await self._ledger.initialize()
        except (OSError, sqlite3.DatabaseError, asyncio.CancelledError):
            await self._ledger.close()
            raise
        try:
            await self._audit.initialize()
        except (AuditIntegrityError, OSError) as exc:
            self.audit_error = str(exc)
            log.error("governance_audit_unavailable", reason=self.audit_error, exc_info=True)
        self._host.configure_execution_contracts(available={contract.id: contract}, selected_for_new=contract.id)
        self._host._event_bus.subscribe(self.observe)
        self._titles.start()
        self._delivery = asyncio.create_task(self._deliver(), name="governance-audit-delivery")

    async def require_admission(self) -> None:
        await self._ledger.require_admission()

    async def _deliver(self) -> None:
        while not self._stop.is_set():
            try:
                observed = await self._reconcile_events()
                delivered = await self._audit.deliver_one()
            except (RuntimeError, OSError, sqlite3.DatabaseError, ValueError) as exc:
                self.audit_error = str(exc)
                log.error("governance_audit_delivery_failed", reason=self.audit_error, exc_info=True)
                return
            if not delivered and not observed:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=0.25)

    async def _reconcile_events(self) -> bool:
        from backend.persistence.event_repo import EventRepository

        cursor = await self._ledger.event_cursor()
        async with self._host._session_factory() as session:
            rows = await EventRepository(session).list_after(cursor, limit=128)
        for row in rows:
            if self._stop.is_set():
                break
            await self.observe(row.event)
            if self.accounting_error is not None:
                raise RuntimeError(f"Governance accounting needs reconciliation: {self.accounting_error}")
            await self._ledger.advance_event_cursor(cursor, row.storage_cursor)
            cursor = row.storage_cursor
        return bool(rows)

    async def observe(self, event: SessionEvent) -> None:
        metadata = event.metadata
        if metadata.integration_binding_id is not None or not event.session_id or not metadata.execution_epoch:
            return
        try:
            if event.kind in {"execution.native.tool_result", "execution.native.usage"}:
                from backend.persistence.native_execution_repo import NativeExecutionRepository

                if not await NativeExecutionRepository(self._host._session_factory).trusted_receipt(event):
                    log.warning("native_accounting_receipt_rejected", event_id=event.id, job_id=event.session_id)
                    return
                if event.kind == "execution.native.usage":
                    for raw in event.payload.get("usage", []):
                        usage = UsageAmount.model_validate(raw)
                        if (
                            usage.job_id != event.session_id
                            or usage.execution_epoch != metadata.execution_epoch
                            or usage.producer != event.payload["family"]
                        ):
                            raise ValueError("Native usage receipt changed execution ownership")
                        await self._ledger.record_usage(event.id, usage)
                    return
                started = event.payload.get("started")
                if started is False:
                    return
                call_id = event.payload.get("operation_id")
                if started is not True or not isinstance(call_id, str) or not metadata.execution_token:
                    await self._ledger.record_uncertainty(
                        event.session_id, metadata.execution_epoch, event.id, "native_execution_unconfirmed"
                    )
                    return
                decision = await self._ledger.authorized_tool(
                    event.session_id, metadata.execution_epoch, metadata.execution_token, call_id
                )
                if decision is None or decision.decision != "allow":
                    await self._ledger.record_uncertainty(
                        event.session_id, metadata.execution_epoch, event.id, "native_authorization_unreconciled"
                    )
                    return
                if decision.metadata.get("execution") != event.payload["execution"]:
                    raise ValueError("Native completion changed its authorized binding")
                if not isinstance(decision.metadata.get("destructive"), bool):
                    await self._ledger.record_uncertainty(
                        event.session_id, metadata.execution_epoch, event.id, "unknown_executed_effect"
                    )
                await self._ledger.record_completion(
                    job_id=event.session_id,
                    epoch=metadata.execution_epoch,
                    event_id=event.id,
                    tool_call_id=call_id,
                    source_id=event.payload["execution"]["binding_id"],
                    action_digest=decision.action_digest,
                    executed=True,
                    succeeded=event.payload.get("succeeded") is True,
                    destructive=decision.metadata.get("destructive") is True,
                )
            elif event.kind == "execution.command.result":
                from backend.models.schemas.recipes import RecipeCommandResult

                if metadata.source_framework != "codeplane.recipe" or not metadata.attempt_id:
                    return
                result = RecipeCommandResult.model_validate(event.payload["result"])
                if result.started is False:
                    return
                if result.started is None or not metadata.execution_token:
                    await self._ledger.record_uncertainty(
                        event.session_id,
                        metadata.execution_epoch,
                        event.id,
                        "command_execution_unconfirmed",
                    )
                    return
                decision = await self._ledger.authorized_tool(
                    event.session_id,
                    metadata.execution_epoch,
                    metadata.execution_token,
                    metadata.execution_token,
                )
                if decision is None:
                    await self._ledger.record_uncertainty(
                        event.session_id,
                        metadata.execution_epoch,
                        event.id,
                        "command_authorization_unreconciled",
                    )
                    return
                if result.uncertain or not isinstance(decision.metadata.get("destructive"), bool):
                    await self._ledger.record_uncertainty(
                        event.session_id,
                        metadata.execution_epoch,
                        event.id,
                        "command_effect_unresolved",
                    )
                await self._ledger.record_completion(
                    job_id=event.session_id,
                    epoch=metadata.execution_epoch,
                    event_id=event.id,
                    tool_call_id=metadata.attempt_id,
                    source_id=f"recipe:{metadata.recipe_run_id}",
                    action_digest=decision.action_digest,
                    executed=True,
                    succeeded=result.exit_status == 0,
                    destructive=decision.metadata.get("destructive") is True,
                )
            elif event.kind in {EventKind.tool_call_completed, EventKind.tool_call_failed}:
                # Failure severity alone is not proof that a native operation ran.
                if event.kind == EventKind.tool_call_failed and event.payload.get("executed") is not True:
                    return
                call_id = event.payload.get("tool_call_id")
                if (
                    metadata.source_framework not in {"copilot", "claude"}
                    or not metadata.run_id
                    or not metadata.execution_token
                    or not isinstance(call_id, str)
                    or not call_id
                ):
                    await self._ledger.record_uncertainty(
                        event.session_id,
                        metadata.execution_epoch,
                        event.id,
                        "missing_native_execution_identity",
                    )
                    return
                decision = await self._ledger.authorized_tool(
                    event.session_id,
                    metadata.execution_epoch,
                    metadata.execution_token,
                    call_id,
                )
                if decision is None:
                    await self._ledger.record_uncertainty(
                        event.session_id,
                        metadata.execution_epoch,
                        event.id,
                        "unreconciled_native_execution",
                    )
                    return
                if decision.decision != "allow":
                    return
                if not isinstance(decision.metadata.get("destructive"), bool):
                    await self._ledger.record_uncertainty(
                        event.session_id,
                        metadata.execution_epoch,
                        event.id,
                        "unknown_executed_effect",
                    )
                await self._ledger.record_completion(
                    job_id=event.session_id,
                    epoch=metadata.execution_epoch,
                    event_id=event.id,
                    tool_call_id=call_id,
                    source_id=json.dumps([metadata.source_framework, metadata.run_id, metadata.execution_token]),
                    action_digest=decision.action_digest,
                    executed=True,
                    succeeded=event.kind != EventKind.tool_call_failed and event.payload.get("success") is not False,
                    destructive=decision.metadata.get("destructive") is True,
                )
            elif event.kind == "utility.completion.completed":
                if metadata.source_framework != "codeplane" or metadata.namespace != ("control", "utility"):
                    return
                for raw in event.payload.get("usage", []):
                    usage = UsageAmount.model_validate(raw)
                    if usage.job_id != event.session_id or usage.execution_epoch != metadata.execution_epoch:
                        raise ValueError("Utility usage receipt changed execution ownership")
                    await self._ledger.record_usage(event.id, usage)
            elif event.kind == EventKind.telemetry_usage and metadata.source_framework in {"copilot", "claude"}:
                for raw in event.payload.get("native_usage", []):
                    usage = UsageAmount.model_validate(raw)
                    if usage.job_id != event.session_id or usage.execution_epoch != metadata.execution_epoch:
                        raise ValueError("Native usage receipt changed execution ownership")
                    await self._ledger.record_usage(event.id, usage)
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError) as exc:
            self.accounting_error = str(exc)
            log.error("governance_accounting_failed", event_id=event.id, job_id=event.session_id, exc_info=True)

    def for_job(self, pin: ExecutionContractPin, policy: RepoPolicy) -> StandalonePolicyRouter:
        from backend.services.action_policy.checkpoint_service import CheckpointService
        from backend.services.adapters.agent_adapter import UtilityCompletionError
        from backend.services.adapters.integration_policy import IntegrationPolicy

        host = self._host
        if host._execution_contracts is None or host._approval_service is None or host._git_service is None:
            raise RuntimeError("Standalone Job policy requires its catalogue, durable approvals and Git service")
        if self.audit_error is not None or self.accounting_error is not None:
            raise StateConflictError(f"Governance requires reconciliation: {self.audit_error or self.accounting_error}")
        try:
            provider = host._adapter_registry.get_completion_provider(host._config.runtime.utility_sdk)
        except UtilityCompletionError as exc:
            provider = None
            log.warning("policy_assessment_provider_unavailable", job_id=pin.job_id, reason=str(exc))

        async def current(job_id: str, provider: str) -> bool:
            job = await host._get_job(job_id)
            return bool(
                job is not None
                and not job.observation_only
                and job.state == "running"
                and not host._shutting_down
                and self.audit_error is None
                and self.accounting_error is None
                and (provider == "codeplane.command" or IntegrationPolicy(host._config).enabled(provider))
            )

        return StandalonePolicyRouter(
            pin=pin,
            execution_epoch=host.execution_epoch,
            policy=policy,
            authority=PolicyAuthority(
                contracts=host._execution_contracts,
                ledger=self._ledger,
                approvals=host._approval_service,
                events=host._event_bus,
                assessments=self._assessments,
                policy=self._policy,
                provider=provider,
                model=host._config.runtime.utility_model,
            ),
            contracts=host._execution_contracts,
            events=host._event_bus,
            checkpoint=CheckpointService(host._git_service),
            job_is_current=current,
        )

    async def close(self) -> None:
        self._stop.set()
        self._host._event_bus.unsubscribe(self.observe)
        if self._closing is None:
            self._closing = asyncio.create_task(self._close_resources(), name="governance-shutdown")
        interrupted = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                interrupted = True
        self._closing.result()
        if interrupted:
            raise asyncio.CancelledError

    async def _close_resources(self) -> None:
        try:
            await self._titles.close()
        finally:
            try:
                await self._assessments.shutdown()
            finally:
                try:
                    if self._delivery is not None:
                        await self._delivery
                finally:
                    try:
                        await self._ledger.close()
                    finally:
                        self._runtime_ownership.close()
