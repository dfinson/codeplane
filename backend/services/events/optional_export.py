"""Optional export admission after local persistence, never an execution dependency."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING

import structlog
from pydantic import ValidationError

from backend.models.usage import UsageAmount
from backend.services.events.export_binding import ExportConfigurationError, resolve_binding
from backend.services.events.export_pipeline import ExportAdmission, MetadataExportPipeline
from backend.services.events.export_status import ExportDropReason, ExportHealth
from backend.services.events.export_transport import export_readiness

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import ExportConfig
    from backend.models.session_event import SessionEvent
    from backend.services.events.event_bus import EventBus

log = structlog.get_logger()


class OptionalExportOwner:
    def __init__(self, config: ExportConfig, factory: async_sessionmaker[AsyncSession]) -> None:
        self.config = config
        self._factory = factory
        self._pipeline: MetadataExportPipeline | None = None
        self._health = ExportHealth()
        self._source: EventBus | None = None
        self._close_task: asyncio.Task[None] | None = None

    async def start(self, source: EventBus) -> None:
        if self._pipeline is not None:
            await self.close()
            self._pipeline = None
            self._close_task = None
            self._health = ExportHealth()
        if self.config.enabled is False:
            return
        from backend.persistence.credential_repo import CredentialRepository

        try:
            protocols = [self.config.protocol, *(["grpc"] if self.config.metrics_endpoint else [])]
            if any(not export_readiness(protocol).ready for protocol in protocols):
                raise ExportConfigurationError("export_dependency_unavailable")
            async with self._factory() as session:
                binding = await resolve_binding(self.config, CredentialRepository(session))
            if binding is None:
                return
            self._pipeline = MetadataExportPipeline(binding)
        except Exception:
            # Optional configuration, package and credential failures cannot
            # turn an otherwise valid local startup into a failure.
            self._health.record_drop(ExportDropReason.configuration, 0, disable=True)
            log.error("optional_export_disabled", reason=ExportDropReason.configuration.value)
            return
        self._health = self._pipeline.health
        self._source = source
        source.subscribe(self.on_event)

    def snapshot(self) -> dict[str, object]:
        value = asdict(self._health.snapshot())
        value["dropped_by_reason"] = dict(value["dropped_by_reason"])
        return value

    def enqueue_projection(self, projection: dict[str, object]) -> None:
        """Native subscriptions call this only after their canonical commit."""
        if self.config.enabled is not True:
            self.disable()
        if self._pipeline is not None:
            self._pipeline.enqueue_projection(projection)

    def enqueue_committed(
        self,
        event: SessionEvent,
        *,
        integration: str,
        provider: str,
        role: str,
        usage: tuple[UsageAmount, ...] = (),
    ) -> ExportAdmission:
        """Trusted post-commit callback for Runtime native subscriptions."""
        if self.config.enabled is not True:
            self.disable()
        if self._pipeline is None:
            return ExportAdmission("disabled", ExportDropReason.disabled)
        return self._pipeline.enqueue(event, integration=integration, provider=provider, role=role, usage=usage)

    async def on_event(self, event: SessionEvent) -> None:
        if self.config.enabled is not True:
            self.disable()
            return
        from backend.persistence.event_repo import EventRepository

        try:
            async with self._factory() as session:
                stored = await EventRepository(session).get(event.id)
            if stored is None:
                return  # Ephemeral deltas and uncommitted dead-letter delivery stay local.
            raw_usage = stored.payload.get("usage")
            usage = tuple(UsageAmount.model_validate(item) for item in raw_usage) if isinstance(raw_usage, list) else ()
            role = stored.payload.get("role")
            roles = {amount.role for amount in usage}
            if role is None and len(roles) == 1:
                role = roles.pop()
            if not isinstance(role, str):
                self._health.record_drop(ExportDropReason.metadata_withheld)
                return
            if any(amount.job_id not in (None, stored.session_id) for amount in usage):
                raise ValueError("usage_identity_mismatch")
            if any(amount.execution_epoch not in (None, stored.metadata.execution_epoch) for amount in usage):
                raise ValueError("usage_execution_mismatch")
            provider = stored.payload.get("provider") or stored.metadata.source_framework
            if not isinstance(provider, str):
                self._health.record_drop(ExportDropReason.metadata_withheld)
                return
            self.enqueue_committed(
                stored,
                integration=stored.metadata.source_framework or provider,
                provider=provider,
                role=role,
                usage=usage,
            )
        except (ValidationError, ValueError):
            self._health.record_drop(ExportDropReason.metadata_withheld)
            log.warning("optional_export_withheld", reason=ExportDropReason.metadata_withheld.value)
        except Exception:
            # This boundary intentionally cannot fail an already committed event.
            self._health.record_drop(ExportDropReason.sink_failure, disable=True)
            self.disable(ExportDropReason.sink_failure)
            log.error("optional_export_disabled", reason=ExportDropReason.sink_failure.value)

    def disable(self, reason: ExportDropReason = ExportDropReason.disabled) -> None:
        if self._source is not None:
            self._source.unsubscribe(self.on_event)
            self._source = None
        if self._pipeline is not None:
            self._pipeline.disable(reason)

    async def close(self) -> None:
        if self._source is not None:
            self._source.unsubscribe(self.on_event)
            self._source = None
        if self._pipeline is not None:
            if self._close_task is None:
                self._close_task = asyncio.create_task(self._pipeline.aclose(), name="optional-export-close")
            try:
                await asyncio.shield(self._close_task)
            except Exception:
                self._health.record_drop(ExportDropReason.sink_failure, 0, disable=True)
                log.error("optional_export_shutdown_failed", reason=ExportDropReason.sink_failure.value)
