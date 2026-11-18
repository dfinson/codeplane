"""Lazy retained TF evaluation and attributable accrual, using the working ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from backend.models.domain import StateConflictError
from backend.persistence.governance_ownership import GovernanceRuntimeLease
from backend.persistence.legacy_governance_repo import LEGACY_POLICY, LegacyGovernanceRepository, _encoded
from backend.services.action_policy.agt_policy import FactDecision
from backend.services.action_policy.classifier import Action, Preset, RecommendedAction, is_security_critical

if TYPE_CHECKING:
    from backend.models.session_event import SessionEvent
    from backend.persistence.governance_provider_repo import GovernanceProviderRepository
    from backend.persistence.governance_repo import GovernanceLedger
    from backend.services.action_policy.governance import GovernanceDecider


class RetainedGovernance:
    policy_id = LEGACY_POLICY

    def __init__(self, ledger: GovernanceLedger, repository: GovernanceProviderRepository) -> None:
        self.ledger = ledger
        self.repository = repository
        self._decider: GovernanceDecider | None = None
        self._usd_ceilings: dict[Preset, tuple[float | None, float | None]] = {}

    def set_usd_ceilings(self, ceilings: dict[Preset, tuple[float | None, float | None]]) -> None:
        self._usd_ceilings = dict(ceilings)

    def _new_provider(self, ceilings: dict[Preset, tuple[float | None, float | None]]) -> GovernanceDecider:
        from backend.services.action_policy.cost_ceiling import make_job_spend_reader
        from backend.services.action_policy.governance import GovernanceDecider

        return GovernanceDecider(
            self.ledger.path,
            spend_reader=make_job_spend_reader(self.repository.application_database),
            usd_ceilings=ceilings,
        )

    def _provider(self) -> GovernanceDecider:
        if self._decider is None:
            LegacyGovernanceRepository(self.ledger.path, self.repository.application_database).require_verified()
            self._decider = self._new_provider(self._usd_ceilings)
        return self._decider

    async def classify(self, action: Action, preset: Preset) -> tuple[FactDecision, dict[str, Any]]:
        return await self.ledger._io(self._classify, action, preset)

    def _classify(self, action: Action, preset: Preset) -> tuple[FactDecision, dict[str, Any]]:
        from traceforge.governance.codec import MetaCodec

        from backend.services.action_policy.governance import _classification_from_meta

        if action.job_id is None:
            raise StateConflictError("Retained evaluation requires exact Job ownership")
        with GovernanceRuntimeLease(self.ledger.path.parent / "retained-writer", exclusive=True):
            provider = self._provider()
            try:
                provider.register_job(action.job_id, preset)
                provider._pipeline_for(action.job_id).get_or_create_state(action.job_id)
                meta = provider.decide(action)
            finally:
                self._close()
        classification = _classification_from_meta(meta)
        selected = classification.recommended_action
        actions: dict[RecommendedAction, Literal["allow", "warn", "escalate", "deny"]] = {
            RecommendedAction.ALLOW: "allow",
            RecommendedAction.WARN: "warn",
            RecommendedAction.TRANSFORM: "warn",
            RecommendedAction.ESCALATE: "escalate",
            RecommendedAction.DENY: "deny",
        }
        if selected not in actions:
            raise StateConflictError("Retained provider returned an unsupported decision")
        action_name = actions[selected]
        decision = FactDecision(
            action=action_name,
            reason_code=classification.reason_code,
            individual_human=is_security_critical(classification.reason_code) or action_name == "deny",
            policy_version=LEGACY_POLICY,
            risk_score=classification.risk_score,
            risk_band=classification.risk_band,
        )
        return decision, {
            "producer": "traceforge",
            "policy_id": LEGACY_POLICY,
            "spend_basis": "legacy_estimate_or_unknown",
            "meta": MetaCodec().serialize_meta(meta),
        }

    async def record(self, event: SessionEvent, action: Action, preset: Preset) -> None:
        identity = {
            "job_id": event.session_id,
            "epoch": event.metadata.execution_epoch,
            "token": event.metadata.execution_token,
            "framework": event.metadata.source_framework,
            "run_id": event.metadata.run_id,
            "call_id": event.metadata.attempt_id
            if event.kind == "execution.command.result"
            else event.payload.get("tool_call_id"),
        }
        source = {
            "event": event.model_dump(mode="json"),
            "action": asdict(action),
            "preset": str(preset),
            "completion": {
                "identity": identity,
                "action": asdict(action),
                "preset": str(preset),
                "kind": event.kind,
                "payload": event.payload,
            },
        }
        source_key = "exec:cp-retained:" + hashlib.sha256(_encoded(identity).encode()).hexdigest()
        await self.ledger._io(self._record, event.id, event.session_id, source_key, source)

    def _record(self, receipt_id: str, job_id: str, key: str, source: dict[str, Any]) -> None:
        from traceforge.governance.codec import MetaCodec

        from backend.services.action_policy.governance import _build_tool_call_event

        with GovernanceRuntimeLease(self.ledger.path.parent / "retained-writer", exclusive=True):
            provider = self._provider()
            try:
                provider.register_job(job_id, Preset(source["preset"]))
                source["usd_ceilings"] = {str(name): values for name, values in self._usd_ceilings.items()}
                pipeline = provider._pipeline_for(job_id)
                native = _build_tool_call_event(Action(**source["action"]), source_event_key=key)
                source["native_event"] = {**asdict(native), "timestamp": native.timestamp.isoformat()}
                context = pipeline.enrich_event(native)
                transient = pipeline.get_or_create_state(job_id).clone_detached()
                pipeline._phase1.apply(context, transient)
                snapshot = MetaCodec().serialize_snapshot(transient.snapshot())
                budget = snapshot["budget"]
                expected = {
                    "budget": budget,
                    "snapshot": snapshot,
                    "projection": {
                        "session_state": [
                            {
                                "session_id": job_id,
                                "total_tool_calls": budget["total_tool_calls"],
                                "total_tokens": budget["total_tokens"],
                                "elapsed_seconds": budget["elapsed_seconds"],
                                "pressure": int(budget["pressure"]),
                                "phase_window_json": json.dumps(snapshot["phase_window"]),
                                "last_assistant_json": snapshot["last_assistant_event_id"],
                                "last_user_json": snapshot["last_user_event_id"],
                                "event_count": snapshot["event_count"],
                                "dropped_events": snapshot["dropped_events"],
                                "last_sequence": snapshot["last_sequence"],
                                "last_event_id": None,
                            }
                        ],
                        "budget_counters": sorted(
                            [
                                {
                                    "session_id": job_id,
                                    "dimension": name.removeprefix("by_"),
                                    "key": dimension,
                                    "count": count,
                                }
                                for name, values in budget.items()
                                if name.startswith("by_")
                                for dimension, count in values
                            ],
                            key=lambda row: (row["dimension"], row["key"]),
                        ),
                    },
                }
                if self.repository.prepare_forward(
                    receipt_id=receipt_id, job_id=job_id, source_key=key, source=source, expected=expected
                ):
                    self.repository.run_forward(receipt_id, lambda: pipeline.process_event(context))
            finally:
                self._close()

    async def reconcile(self, ownership: GovernanceRuntimeLease) -> int:
        """Resume journaled accounting only, never repeat business work."""
        ownership.require_exclusive()
        if ownership.directory != self.ledger.path.parent:
            raise StateConflictError("Retained reconciliation requires the exact stopped runtime home")
        return await self.ledger._io(self._reconcile)

    def _reconcile(self) -> int:
        from traceforge.governance.types import ToolCallEvent

        pending = self.repository.pending()
        with GovernanceRuntimeLease(self.ledger.path.parent / "retained-writer", exclusive=True):
            for receipt in pending:
                source = self.repository.recovery_source(receipt["receipt_id"])
                if source is not None:
                    provider = self._new_provider(
                        {Preset(name): (pair[0], pair[1]) for name, pair in source["usd_ceilings"].items()}
                    )
                    try:
                        provider.register_job(receipt["job_id"], Preset(source["preset"]))
                        fields = dict(source["native_event"])
                        fields["timestamp"] = datetime.fromisoformat(fields["timestamp"])
                        native = ToolCallEvent(**fields)
                        if native.session_id != receipt["job_id"] or native.source_event_key != receipt["source_key"]:
                            raise StateConflictError("Retained recovery event ownership changed")
                        pipeline = provider._pipeline_for(receipt["job_id"])
                        pipeline.process_event(pipeline.enrich_event(native))
                    finally:
                        provider.close()
                self.repository.finish_forward(receipt["receipt_id"])
        return len(pending)

    def _close(self) -> None:
        if self._decider is not None:
            self._decider.close()
            self._decider = None

    async def close(self) -> None:
        if self._decider is not None:
            await self.ledger._io(self._close)
