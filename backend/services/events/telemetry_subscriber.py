"""Read-side telemetry persistence subscriber for canonical TraceForge events."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.events import EventKind
from backend.services.events.observation import has_source_grouping, observed_operation_id

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.session_event import SessionEvent
    from backend.services.analytics.model_pricing import ModelPricingService
    from backend.services.events.event_bus import EventBus

log = structlog.get_logger()


class TelemetrySubscriber:
    """Persist CodePlane telemetry from the canonical ``traceforge.SessionEvent`` stream."""

    _TRANSCRIPT_BUFFER_SIZE = 10

    _MUTATIVE_SHELL_PREFIXES: frozenset[str] = frozenset(
        {
            "git commit",
            "git add",
            "git push",
            "git checkout",
            "git merge",
            "git rebase",
            "git reset",
            "git stash",
            "git cherry-pick",
            "git tag",
            "git branch -d",
            "git branch -D",
            "git branch -m",
            "mkdir",
            "mv",
            "rm",
            "cp",
            "ln",
            "chmod",
            "chown",
            "touch",
            "pip install",
            "pip uninstall",
            "uv add",
            "uv remove",
            "uv sync",
            "uv pip install",
            "npm install",
            "npm uninstall",
            "npm ci",
            "yarn add",
            "yarn remove",
            "pnpm add",
            "pnpm remove",
            "docker build",
            "docker run",
            "docker compose up",
            "make",
            "cargo build",
            "go build",
        }
    )

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        schedule_write: Callable[[Coroutine[Any, Any, None]], None],
        model_pricing: ModelPricingService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._schedule_write = schedule_write
        self._model_pricing = model_pricing

        self._job_start_times: dict[str, float] = {}
        self._turn_counters: dict[str, int] = {}
        self._current_phases: dict[str, str] = {}
        self._retry_trackers: dict[str, Any] = {}
        self._transcript_buffers: dict[str, list[dict[str, str]]] = {}
        self._tool_turn_ids: dict[str, dict[str, str | None]] = {}

    def subscribe(self, event_bus: EventBus) -> None:
        """Attach this subscriber to an ``EventBus``."""
        event_bus.subscribe(self.handle_event)

    async def handle_event(self, event: SessionEvent) -> None:
        """Handle a single canonical TraceForge event."""
        job_id = event.session_id
        if not job_id:
            return

        metadata = event.metadata
        sdk = (metadata.source_framework if metadata is not None else None) or "unknown"
        kind = str(event.kind)
        operation = observed_operation_id(event)
        if has_source_grouping(event) and kind in (EventKind.tool_call_completed, EventKind.tool_call_failed):
            self._schedule_write(self._record_observed_tool(event, operation or f"event:{event.id}", sdk))
            return
        if kind == EventKind.execution_phase_changed:
            phase = event.payload.get("phase")
            if phase:
                self.set_execution_phase(job_id, str(phase))
        elif kind == EventKind.telemetry_usage:
            self._record_usage(job_id, event.payload, sdk)
        elif kind in (EventKind.tool_call_completed, EventKind.tool_call_failed):
            self._record_completed_tool(job_id, event, sdk)
        elif kind == EventKind.message_user:
            self._buffer_message(job_id, "operator", event.payload)
            self._record_message_count(job_id, "operator", sdk)
        elif kind == EventKind.message_assistant:
            self.set_job_start_time(job_id)
            self._buffer_message(job_id, "agent", event.payload)
            self._record_message_count(job_id, "agent", sdk)

    def cleanup(self, job_id: str) -> None:
        """Remove all per-job telemetry subscriber state."""
        self._job_start_times.pop(job_id, None)
        self._turn_counters.pop(job_id, None)
        self._current_phases.pop(job_id, None)
        self._retry_trackers.pop(job_id, None)
        self._transcript_buffers.pop(job_id, None)
        self._tool_turn_ids.pop(job_id, None)

    def set_job_start_time(self, job_id: str, start: float | None = None) -> None:
        """Record when a job started for telemetry span offsets."""
        self._job_start_times.setdefault(job_id, start or time.monotonic())

    def set_execution_phase(self, job_id: str, phase: str) -> None:
        """Update the current execution phase for spans."""
        self._current_phases[job_id] = phase

    def advance_turn(self, job_id: str) -> int:
        """Advance and return the per-job turn counter."""
        turn = self._turn_counters.get(job_id, 0) + 1
        self._turn_counters[job_id] = turn
        return turn

    def get_turn(self, job_id: str) -> int:
        """Return the current per-job turn counter."""
        return self._turn_counters.get(job_id, 0)

    def _record_usage(self, job_id: str, payload: dict[str, Any], sdk: str) -> None:
        from backend.services.analytics import telemetry as tel

        input_tokens = int(payload.get("input_tokens") or 0)
        output_tokens = int(payload.get("output_tokens") or 0)
        cache_read_tokens = int(payload.get("cache_read_tokens") or 0)
        cache_write_tokens = int(payload.get("cache_write_tokens") or 0)
        duration_ms = float(payload.get("duration_ms") or 0.0)
        model = str(payload.get("model") or "")
        is_subagent = bool(payload.get("is_subagent", False))
        advance_turn = bool(payload.get("advance_turn", True))
        num_turns = int(payload.get("num_turns") or 1)
        cost_usd = self._resolve_cost_usd(
            payload.get("cost_usd"),
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
        )

        attrs: dict[str, Any] = {"job_id": job_id, "sdk": sdk}
        tel.tokens_input.add(input_tokens, {**attrs, "model": model})
        tel.tokens_output.add(output_tokens, {**attrs, "model": model})
        tel.tokens_cache_read.add(cache_read_tokens, attrs)
        tel.tokens_cache_write.add(cache_write_tokens, attrs)
        if cost_usd:
            tel.cost_usd.add(cost_usd, attrs)
        if duration_ms:
            tel.llm_duration.record(duration_ms, {**attrs, "is_subagent": is_subagent})

        db_counters: dict[str, Any] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "llm_call_count": num_turns,
        }
        if cost_usd:
            db_counters["total_cost_usd"] = cost_usd
        if is_subagent and cost_usd:
            db_counters["subagent_cost_usd"] = cost_usd
        if duration_ms:
            db_counters["total_llm_duration_ms"] = int(duration_ms)
        if advance_turn:
            db_counters["total_turns"] = num_turns
        premium_requests = payload.get("premium_requests")
        if premium_requests is not None:
            db_counters["premium_requests"] = float(premium_requests)
            tel.premium_requests_counter.add(float(premium_requests), attrs)

        self._schedule_write(self._db_increment(job_id, sdk=sdk, **db_counters))

        if advance_turn:
            for _ in range(num_turns):
                self.advance_turn(job_id)

        if model:
            self._schedule_write(self._db_set_model(job_id, model))

        if duration_ms or input_tokens or output_tokens:
            self._record_llm_span(
                job_id,
                model,
                duration_ms,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
                sdk,
            )

    def _resolve_cost_usd(
        self,
        raw_cost: Any,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> float:
        if raw_cost is not None:
            return float(raw_cost or 0.0)
        if self._model_pricing is None or not model:
            return 0.0
        return self._model_pricing.compute_cost(
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
        )

    def _record_completed_tool(self, job_id: str, event: SessionEvent, sdk: str) -> None:
        payload = event.payload
        tool_name = str(payload.get("tool_name") or "tool")
        tool_args_str = self._stringify_tool_arguments(payload.get("arguments"))
        supplied = payload.get("success")
        success = supplied if isinstance(supplied, bool) else None
        result_text = str(payload.get("result") or "")
        duration_ms = float(event.metadata.duration_ms or 0.0)
        turn_id = payload.get("turn_id")
        turn_id = str(turn_id) if turn_id is not None else None
        tool_call_id = payload.get("tool_call_id")
        if tool_call_id:
            self._tool_turn_ids.setdefault(job_id, {})[str(tool_call_id)] = turn_id

        self._buffer_tool_call(job_id, tool_name, tool_args_str, result_text, success)
        self._record_tool_telemetry(
            job_id,
            tool_name=tool_name,
            tool_args_str=tool_args_str,
            success=success,
            duration_ms=duration_ms,
            result_text=result_text,
            turn_id=turn_id,
            motivation_summary=self._motivation_summary(event.metadata.motivation),
            sdk=sdk,
        )

    async def _record_observed_tool(self, event: SessionEvent, operation_id: str, sdk: str) -> None:
        from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
        from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository
        from backend.services.tools.tool_classifier import classify_tool, extract_tool_target

        payload = event.payload
        supplied = payload.get("success")
        success = supplied if isinstance(supplied, bool) else None
        tool_name = str(payload.get("tool_name") or "tool")
        arguments = self._stringify_tool_arguments(payload.get("arguments"))
        async with self._get_db_session() as session:
            spans = TelemetrySpansRepository(session)
            previous = await spans.observed_tool(event.session_id, operation_id)
            old = previous["attrs"] if previous is not None else {}
            old_success = old.get("success")
            conflict = old.get("outcome_conflict") is True or (
                isinstance(old_success, bool) and isinstance(success, bool) and success != old_success
            )
            if conflict:
                success = None
            elif success is None and isinstance(old_success, bool):
                success = old_success
            duration = event.metadata.duration_ms
            if duration is None:
                duration = float(previous["duration_ms"]) if previous is not None else 0.0
            attrs = {
                **old,
                "observation_operation_id": operation_id,
                "operation_identity_known": observed_operation_id(event) is not None,
                "latest_observation_event_id": event.id,
                "integration_binding_id": event.metadata.integration_binding_id,
                "span_id": event.metadata.span_id,
                "native_tool_call_id": payload.get("tool_call_id"),
                "success": success,
                "outcome_conflict": conflict,
                "source_timestamp": event.timestamp.isoformat(),
            }
            if event.metadata.duration_ms is not None:
                attrs["source_duration_ms"] = event.metadata.duration_ms
            if previous is None:
                observed_at = time.monotonic()
                await spans.insert(
                    job_id=event.session_id,
                    span_type="tool",
                    name=tool_name,
                    started_at=round(observed_at - self._job_start_times.get(event.session_id, observed_at), 2),
                    duration_ms=duration,
                    attrs=attrs,
                    tool_category=classify_tool(tool_name),
                    tool_target=extract_tool_target(tool_name, arguments),
                    tool_args_json=arguments,
                    turn_id=payload.get("turn_id"),
                )
            else:
                await spans.update_observed_tool(previous["id"], attrs=attrs, duration_ms=duration)
            summary = TelemetrySummaryRepository(session)
            await summary.init_job(event.session_id, sdk=sdk)
            await summary.increment(
                job_id=event.session_id,
                tool_call_count=int(previous is None),
                tool_failure_count=int(success is False) - int(old_success is False),
                total_tool_duration_ms=int(duration)
                - int(float(previous["duration_ms"]) if previous is not None else 0),
            )

    def _record_tool_telemetry(
        self,
        job_id: str,
        *,
        tool_name: str,
        tool_args_str: str | None,
        success: bool | None,
        duration_ms: float,
        result_text: str,
        turn_id: str | None = None,
        motivation_summary: str | None = None,
        sdk: str,
    ) -> None:
        from backend.services.analytics import telemetry as tel
        from backend.services.job.retry_tracker import RetryResult, RetryTracker
        from backend.services.tools.tool_classifier import (
            classify_tool,
            extract_file_paths,
            extract_tool_target,
            refine_shell_category,
        )

        attrs: dict[str, Any] = {
            "job_id": job_id,
            "sdk": sdk,
            "tool_name": tool_name,
        }
        if success is not None:
            attrs["success"] = success
        tel.tool_duration.record(duration_ms, attrs)

        category = classify_tool(tool_name)
        if category == "shell":
            refined = refine_shell_category(tool_args_str)
            if refined:
                category = refined
        target = extract_tool_target(tool_name, tool_args_str)
        current_phase = self._current_phases.get(job_id, "agent_reasoning")
        turn_num = self._turn_counters.get(job_id, 0)

        if job_id not in self._retry_trackers:
            self._retry_trackers[job_id] = RetryTracker()
        retry_result = (
            self._retry_trackers[job_id].record(tool_name, target, 0, success)
            if success is not None
            else RetryResult(False, None)
        )

        result_size = len(result_text.encode("utf-8", errors="replace")) if result_text else None

        file_rw_increment: dict[str, int] = {}
        if category in ("file_read", "file_write"):
            paths = extract_file_paths(tool_name, tool_args_str)
            access_type = "write" if category == "file_write" else "read"
            if access_type == "read":
                file_rw_increment["file_read_count"] = 1
            else:
                file_rw_increment["file_write_count"] = 1
            for fpath in paths:
                self._schedule_write(
                    self._db_record_file_access(
                        job_id=job_id,
                        file_path=fpath,
                        access_type=access_type,
                        turn_number=turn_num,
                    )
                )

        self._schedule_write(
            self._db_increment(
                job_id,
                sdk=sdk,
                tool_call_count=1,
                tool_failure_count=int(success is False),
                total_tool_duration_ms=int(duration_ms),
                retry_count=1 if retry_result.is_retry else 0,
                **file_rw_increment,
            )
        )

        job_start = self._job_start_times.get(job_id, time.monotonic())
        offset = time.monotonic() - job_start
        preceding_context = self._maybe_capture_context(job_id, category, tool_args_str)

        self._schedule_write(
            self._db_insert_span(
                job_id=job_id,
                span_type="tool",
                name=tool_name,
                started_at=round(offset, 2),
                duration_ms=duration_ms,
                attrs={
                    "success": success,
                    **({"error_snippet": result_text} if success is False and result_text else {}),
                },
                tool_category=category,
                tool_target=target,
                turn_number=turn_num,
                execution_phase=current_phase,
                is_retry=retry_result.is_retry,
                retries_span_id=retry_result.prior_failure_span_id,
                tool_args_json=tool_args_str,
                result_size_bytes=result_size,
                turn_id=turn_id,
                preceding_context=preceding_context,
                motivation_summary=motivation_summary,
            )
        )

    def _record_llm_span(
        self,
        job_id: str,
        model: str,
        duration_ms: float,
        input_tokens: int,
        output_tokens: int,
        cache_read: int,
        cache_write: int,
        cost_usd: float,
        sdk: str,
    ) -> None:
        _ = sdk
        turn_num = self._turn_counters.get(job_id, 0)
        current_phase = self._current_phases.get(job_id, "agent_reasoning")
        job_start = self._job_start_times.get(job_id, time.monotonic())
        offset = time.monotonic() - job_start

        self._schedule_write(
            self._db_insert_span(
                job_id=job_id,
                span_type="llm",
                name=model or "unknown",
                started_at=round(offset, 2),
                duration_ms=float(duration_ms),
                attrs={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write,
                    "cost": cost_usd,
                },
                turn_number=turn_num,
                execution_phase=current_phase,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost_usd=cost_usd,
            )
        )

    def _record_message_count(self, job_id: str, role: str, sdk: str) -> None:
        from backend.services.analytics import telemetry as tel

        tel.messages_counter.add(1, {"job_id": job_id, "sdk": sdk, "role": role})
        key = "agent_messages" if role == "agent" else "operator_messages"
        self._schedule_write(self._db_increment(job_id, sdk=sdk, **{key: 1}))

    def _buffer_message(self, job_id: str, role: str, payload: dict[str, Any]) -> None:
        content = payload.get("content", "")
        self._buffer_transcript(job_id, {"role": role, "content": str(content or "")})

    def _buffer_tool_call(
        self,
        job_id: str,
        tool_name: str,
        tool_args_str: str | None,
        result_text: str,
        success: bool | None,
    ) -> None:
        payload: dict[str, Any] = {
            "role": "tool_call",
            "tool_name": tool_name,
            "tool_success": success,
        }
        if tool_args_str:
            payload["tool_args"] = tool_args_str
        if result_text:
            payload["tool_result"] = result_text
        self._buffer_transcript(job_id, payload)

    def _buffer_transcript(self, job_id: str, payload: dict[str, Any]) -> None:
        role = str(payload.get("role", ""))
        if role in ("agent_delta", "reasoning_delta", "tool_output_delta", "tool_running"):
            return
        entry: dict[str, str] = {"role": role}
        tool_name = payload.get("tool_name")
        if role == "tool_call":
            if tool_name:
                entry["tool_name"] = str(tool_name)
            tool_args = payload.get("tool_args")
            if tool_args:
                entry["tool_args"] = str(tool_args)
            raw_result = str(payload.get("tool_result", "") or "")
            if raw_result:
                entry["tool_result"] = raw_result
        else:
            entry["content"] = str(payload.get("content", ""))
            if tool_name:
                entry["tool_name"] = str(tool_name)
        buf = self._transcript_buffers.setdefault(job_id, [])
        buf.append(entry)
        if len(buf) > self._TRANSCRIPT_BUFFER_SIZE:
            del buf[: len(buf) - self._TRANSCRIPT_BUFFER_SIZE]

    def _snapshot_preceding_context(self, job_id: str) -> str | None:
        buf = self._transcript_buffers.get(job_id)
        if not buf:
            return None
        return json.dumps(list(buf), ensure_ascii=False)

    def _maybe_capture_context(
        self,
        job_id: str,
        category: str,
        tool_args_str: str | None,
    ) -> str | None:
        from backend.services.tools.parsing_utils import ensure_dict

        if category in {"file_write", "git_write"}:
            return self._snapshot_preceding_context(job_id)
        if category == "shell" and tool_args_str:
            parsed = ensure_dict(tool_args_str)
            if parsed:
                cmd = str(parsed.get("command", "")).strip().lower()
                if any(cmd.startswith(p) for p in self._MUTATIVE_SHELL_PREFIXES):
                    return self._snapshot_preceding_context(job_id)
        return None

    def _stringify_tool_arguments(self, arguments: Any) -> str | None:
        if arguments is None:
            return None
        if isinstance(arguments, str):
            return arguments
        try:
            return json.dumps(arguments)
        except (TypeError, ValueError):
            return str(arguments)

    def _motivation_summary(self, motivation: Any) -> str | None:
        if motivation is None:
            return None
        intent = getattr(motivation, "intent", None)
        reasoning = getattr(motivation, "reasoning", None)
        parts = [str(part) for part in (intent, reasoning) if part]
        return "\n".join(parts) if parts else None

    async def _db_increment(self, job_id: str, *, sdk: str, **counters: Any) -> None:
        try:
            async with self._get_db_session() as session:
                from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

                repo = TelemetrySummaryRepository(session)
                totals = await repo.increment(job_id=job_id, **counters)
                if not totals.get("_row_found") and counters:
                    await repo.init_job(job_id, sdk=sdk)
                    await repo.increment(job_id=job_id, **counters)
        except Exception:
            log.warning("telemetry_subscriber_db_write_failed", fn="increment", exc_info=True)

    async def _db_insert_span(self, *, job_id: str, **span_fields: Any) -> None:
        try:
            async with self._get_db_session() as session:
                from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository

                await TelemetrySpansRepository(session).insert(job_id=job_id, **span_fields)
        except Exception:
            log.warning("telemetry_subscriber_db_write_failed", fn="insert_span", exc_info=True)

    async def _db_set_model(self, job_id: str, model: str) -> None:
        try:
            async with self._get_db_session() as session:
                from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository

                await TelemetrySummaryRepository(session).set_model(job_id=job_id, model=model)
        except Exception:
            log.warning("telemetry_subscriber_db_write_failed", fn="set_model", exc_info=True)

    async def _db_record_file_access(
        self,
        *,
        job_id: str,
        file_path: str,
        access_type: str,
        turn_number: int,
    ) -> None:
        try:
            async with self._get_db_session() as session:
                from backend.persistence.file_access_repo import FileAccessRepository

                await FileAccessRepository(session).record(
                    job_id=job_id,
                    file_path=file_path,
                    access_type=access_type,
                    turn_number=turn_number,
                )
        except Exception:
            log.warning("telemetry_subscriber_db_write_failed", fn="record_file_access", exc_info=True)

    @asynccontextmanager
    async def _get_db_session(self) -> AsyncIterator[AsyncSession]:
        from backend.persistence.database import serialized_write

        async with serialized_write(self._session_factory) as session:
            yield session
