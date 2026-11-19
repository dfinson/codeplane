"""Persistence for the denormalized job telemetry summary table.

Each adapter ``record_*()`` call triggers an atomic upsert so the row is
always up-to-date.  No timers, no flush intervals.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

import structlog
from pydantic import ValidationError
from sqlalchemy import select, text

from backend.models.db import EventRow
from backend.models.session_event import EventMetadata
from backend.models.usage import UsageAmount
from backend.persistence.currency_projection import currency_query
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from backend.models.domain import TelemetrySummaryRow

log = structlog.get_logger()


class TelemetrySummaryRepository(BaseRepository):
    """Event-driven upserts into ``job_telemetry_summary``."""

    async def batch_cost_usage(self, job_ids: list[str], *, all_roles: bool = False) -> dict[str, list[UsageAmount]]:
        """Read source-typed primary cost evidence without relabeling historical totals."""
        if not job_ids:
            return {}
        rows = await self._session.stream(
            select(EventRow.job_id, EventRow.event_id, EventRow.kind, EventRow.payload, EventRow.event_metadata)
            .where(
                EventRow.job_id.in_(job_ids),
                EventRow.kind.in_(("telemetry.usage", "execution.native.usage", "utility.completion.completed")),
            )
            .order_by(EventRow.id)
            .execution_options(yield_per=100)
        )
        totals: dict[str, dict[str, UsageAmount]] = {}
        async for row in rows:
            assert row.job_id is not None  # The query selects only the supplied Job identities.
            metadata = EventMetadata.model_validate_json(row.event_metadata or "{}")
            payload = json.loads(row.payload)
            if metadata.integration_binding_id and (metadata.namespace or ())[:1] == ("observation-source",):
                continue
            if row.kind == "telemetry.usage":
                if metadata.source_framework not in {"copilot", "claude"}:
                    continue
                entries = payload.get("native_usage", [])
            else:
                if metadata.source_framework != "codeplane" or metadata.namespace not in {
                    ("control", "native-execution"),
                    ("control", "utility"),
                }:
                    continue
                entries = payload.get("usage", [])
            if not isinstance(entries, list):
                log.warning("job_cost_usage_invalid", event_id=row.event_id, reason="expected_usage_list")
                continue
            for entry in entries:
                try:
                    usage = UsageAmount.model_validate(entry)
                except ValidationError:
                    log.warning("job_cost_usage_invalid", event_id=row.event_id, reason="invalid_source_usage")
                    continue
                if usage.unit not in {"USD", "copilot_cost_unit", "premium_requests"} or (
                    not all_roles and usage.role != "primary"
                ):
                    continue
                if (
                    usage.job_id != row.job_id
                    or usage.execution_epoch != metadata.execution_epoch
                    or (row.kind == "telemetry.usage" and usage.producer != metadata.source_framework)
                ):
                    log.warning("job_cost_usage_invalid", event_id=row.event_id, reason="source_identity_mismatch")
                    continue
                key = usage.model_dump_json(exclude={"amount"})
                by_source = totals.setdefault(row.job_id, {})
                previous = by_source.get(key)
                by_source[key] = usage.model_copy(
                    update={"amount": (previous.amount if previous is not None else Decimal(0)) + usage.amount}
                )
        return {job_id: list(entries.values()) for job_id, entries in totals.items()}

    async def init_job(
        self,
        job_id: str,
        *,
        sdk: str,
        model: str = "",
        repo: str = "",
        branch: str = "",
        session_kind: str = "job",
    ) -> None:
        """Insert the initial summary row when a job starts running."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                INSERT INTO job_telemetry_summary
                    (job_id, session_kind, sdk, model, repo, branch, status, duration_ms,
                     input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                     total_cost_usd, premium_requests,
                     llm_call_count, total_llm_duration_ms,
                     tool_call_count, tool_failure_count, total_tool_duration_ms,
                     compactions, tokens_compacted,
                     approval_count, approval_wait_ms,
                     agent_messages, operator_messages,
                     context_window_size, current_context_tokens,
                     created_at, updated_at)
                VALUES
                    (:job_id, :session_kind, :sdk, :model, :repo, :branch, 'running', 0,
                     0, 0, 0, 0,
                     0.0, 0.0,
                     0, 0,
                     0, 0, 0,
                     0, 0,
                     0, 0,
                     0, 0,
                     0, 0,
                     :now, :now)
                ON CONFLICT(job_id, session_kind) DO UPDATE SET
                    model = CASE WHEN excluded.model != '' THEN excluded.model ELSE job_telemetry_summary.model END,
                    updated_at = excluded.updated_at
            """),
            {
                "job_id": job_id,
                "session_kind": session_kind,
                "sdk": sdk,
                "model": model,
                "repo": repo,
                "branch": branch,
                "now": now,
            },
        )
        await self._session.flush()

    async def increment(
        self,
        job_id: str,
        *,
        session_kind: str = "job",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        total_cost_usd: float = 0.0,
        premium_requests: float = 0.0,
        llm_call_count: int = 0,
        total_llm_duration_ms: int = 0,
        tool_call_count: int = 0,
        tool_failure_count: int = 0,
        total_tool_duration_ms: int = 0,
        compactions: int = 0,
        tokens_compacted: int = 0,
        approval_count: int = 0,
        approval_wait_ms: int = 0,
        agent_messages: int = 0,
        operator_messages: int = 0,
        total_turns: int = 0,
        retry_count: int = 0,
        retry_cost_usd: float = 0.0,
        file_read_count: int = 0,
        file_write_count: int = 0,
        agent_error_count: int = 0,
        subagent_cost_usd: float = 0.0,
    ) -> dict[str, float | int]:
        """Atomically increment counters for a job.  Idempotent per field.

        Returns the new running totals for ``total_cost_usd`` and token counts
        so callers can include them in SSE broadcasts without an extra query.
        """
        now = datetime.now(UTC).isoformat()
        result = await self._session.execute(
            text("""
                UPDATE job_telemetry_summary SET
                    input_tokens          = input_tokens + :input_tokens,
                    output_tokens         = output_tokens + :output_tokens,
                    cache_read_tokens     = cache_read_tokens + :cache_read_tokens,
                    cache_write_tokens    = cache_write_tokens + :cache_write_tokens,
                    total_cost_usd        = total_cost_usd + :total_cost_usd,
                    premium_requests      = premium_requests + :premium_requests,
                    llm_call_count        = llm_call_count + :llm_call_count,
                    derived_currency_qualified = CASE WHEN :llm_call_count > 0
                        THEN 0 ELSE derived_currency_qualified END,
                    total_llm_duration_ms = total_llm_duration_ms + :total_llm_duration_ms,
                    tool_call_count       = tool_call_count + :tool_call_count,
                    tool_failure_count    = tool_failure_count + :tool_failure_count,
                    total_tool_duration_ms= total_tool_duration_ms + :total_tool_duration_ms,
                    compactions           = compactions + :compactions,
                    tokens_compacted      = tokens_compacted + :tokens_compacted,
                    approval_count        = approval_count + :approval_count,
                    approval_wait_ms      = approval_wait_ms + :approval_wait_ms,
                    agent_messages        = agent_messages + :agent_messages,
                    operator_messages     = operator_messages + :operator_messages,
                    total_turns           = total_turns + :total_turns,
                    retry_count           = retry_count + :retry_count,
                    retry_cost_usd        = retry_cost_usd + :retry_cost_usd,
                    file_read_count       = file_read_count + :file_read_count,
                    file_write_count      = file_write_count + :file_write_count,
                    agent_error_count     = agent_error_count + :agent_error_count,
                    subagent_cost_usd     = subagent_cost_usd + :subagent_cost_usd,
                    updated_at            = :now
                WHERE job_id = :job_id AND session_kind = :session_kind
                RETURNING total_cost_usd, input_tokens, output_tokens,
                          cache_read_tokens, cache_write_tokens
            """),
            {
                "job_id": job_id,
                "session_kind": session_kind,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
                "total_cost_usd": total_cost_usd,
                "premium_requests": premium_requests,
                "llm_call_count": llm_call_count,
                "total_llm_duration_ms": total_llm_duration_ms,
                "tool_call_count": tool_call_count,
                "tool_failure_count": tool_failure_count,
                "total_tool_duration_ms": total_tool_duration_ms,
                "compactions": compactions,
                "tokens_compacted": tokens_compacted,
                "approval_count": approval_count,
                "approval_wait_ms": approval_wait_ms,
                "agent_messages": agent_messages,
                "operator_messages": operator_messages,
                "total_turns": total_turns,
                "retry_count": retry_count,
                "retry_cost_usd": retry_cost_usd,
                "file_read_count": file_read_count,
                "file_write_count": file_write_count,
                "agent_error_count": agent_error_count,
                "subagent_cost_usd": subagent_cost_usd,
                "now": now,
            },
        )
        row = result.mappings().first()
        await self._session.flush()
        if row:
            total_tokens = (
                int(row["input_tokens"])
                + int(row["output_tokens"])
                + int(row["cache_read_tokens"])
                + int(row["cache_write_tokens"])
            )
            return {
                "total_cost_usd": float(row["total_cost_usd"]),
                "total_tokens": total_tokens,
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "_row_found": 1,
            }
        return {"total_cost_usd": 0.0, "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "_row_found": 0}

    async def set_model(self, job_id: str, model: str) -> None:
        """Update the model once confirmed by the SDK."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                UPDATE job_telemetry_summary
                SET model = :model, updated_at = :now
                WHERE job_id = :job_id AND session_kind = 'job'
            """),
            {"job_id": job_id, "model": model, "now": now},
        )
        await self._session.flush()

    async def set_context(
        self, job_id: str, *, current_tokens: int | None = None, window_size: int | None = None
    ) -> None:
        """Update the point-in-time context window state."""
        parts: list[str] = []
        params: dict[str, Any] = {"job_id": job_id, "now": datetime.now(UTC).isoformat()}
        if current_tokens is not None:
            parts.append("current_context_tokens = :current_tokens")
            params["current_tokens"] = current_tokens
        if window_size is not None:
            parts.append("context_window_size = :window_size")
            params["window_size"] = window_size
        if not parts:
            return
        parts.append("updated_at = :now")
        set_clause = ", ".join(parts)
        await self._session.execute(
            text(f"UPDATE job_telemetry_summary SET {set_clause} WHERE job_id = :job_id AND session_kind = 'job'"),  # noqa: S608
            params,
        )
        await self._session.flush()

    async def set_quota(self, job_id: str, quota_json: str) -> None:
        """Merge a Copilot quota observation without discarding other resources."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                UPDATE job_telemetry_summary
                SET quota_json = json_patch(COALESCE(NULLIF(quota_json, ''), '{}'), :quota_json),
                    updated_at = :now
                WHERE job_id = :job_id AND session_kind = 'job'
            """),
            {"job_id": job_id, "quota_json": quota_json, "now": now},
        )
        await self._session.flush()

    async def finalize(self, job_id: str, *, status: str, duration_ms: int) -> None:
        """Set terminal status and completion timestamp."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                UPDATE job_telemetry_summary
                SET status = :status, completed_at = :now, duration_ms = :duration_ms, updated_at = :now
                WHERE job_id = :job_id AND session_kind = 'job'
            """),
            {"job_id": job_id, "status": status, "duration_ms": duration_ms, "now": now},
        )
        await self._session.flush()

    async def set_turn_stats(
        self,
        job_id: str,
        *,
        unique_files_read: int = 0,
        file_reread_count: int = 0,
        peak_turn_cost_usd: float = 0.0,
        avg_turn_cost_usd: float = 0.0,
        cost_first_half_usd: float = 0.0,
        cost_second_half_usd: float = 0.0,
        diff_lines_added: int = 0,
        diff_lines_removed: int = 0,
        derived_currency_qualified: bool = False,
    ) -> None:
        """Set computed turn economics stats (called by post-job attribution)."""
        now = datetime.now(UTC).isoformat()
        await self._session.execute(
            text("""
                UPDATE job_telemetry_summary SET
                    unique_files_read   = :unique_files_read,
                    file_reread_count   = :file_reread_count,
                    peak_turn_cost_usd  = :peak_turn_cost_usd,
                    avg_turn_cost_usd   = :avg_turn_cost_usd,
                    cost_first_half_usd = :cost_first_half_usd,
                    cost_second_half_usd= :cost_second_half_usd,
                    diff_lines_added    = :diff_lines_added,
                    diff_lines_removed  = :diff_lines_removed,
                    derived_currency_qualified = :derived_currency_qualified,
                    updated_at          = :now
                WHERE job_id = :job_id AND session_kind = 'job'
            """),
            {
                "job_id": job_id,
                "unique_files_read": unique_files_read,
                "file_reread_count": file_reread_count,
                "peak_turn_cost_usd": peak_turn_cost_usd,
                "avg_turn_cost_usd": avg_turn_cost_usd,
                "cost_first_half_usd": cost_first_half_usd,
                "cost_second_half_usd": cost_second_half_usd,
                "diff_lines_added": diff_lines_added,
                "diff_lines_removed": diff_lines_removed,
                "derived_currency_qualified": derived_currency_qualified,
                "now": now,
            },
        )
        await self._session.flush()

    async def get(self, job_id: str, *, session_kind: str = "job") -> TelemetrySummaryRow | None:
        """Load summary row.  Returns None if not found."""

        result = await self._session.execute(
            text("SELECT * FROM job_telemetry_summary WHERE job_id = :job_id AND session_kind = :session_kind"),
            {"job_id": job_id, "session_kind": session_kind},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return cast("TelemetrySummaryRow", dict(row))

    async def list_sidecars(self, job_id: str) -> list[TelemetrySummaryRow]:
        """Return all non-job session_kind rows for a job."""
        result = await self._session.execute(
            currency_query(
                "SELECT session_kind, input_tokens, output_tokens, total_cost_usd, "
                "llm_call_count, tool_call_count "
                "FROM job_telemetry_summary "
                "WHERE job_id = :job_id AND session_kind != 'job'"
            ),
            {"job_id": job_id},
        )
        return [cast("TelemetrySummaryRow", dict(r)) for r in result.mappings().all()]

    async def get_currency(self, job_id: str) -> TelemetrySummaryRow | None:
        result = await self._session.execute(
            currency_query("SELECT * FROM job_telemetry_summary WHERE job_id = :job_id AND session_kind = 'job'"),
            {"job_id": job_id},
        )
        row = result.mappings().first()
        return cast("TelemetrySummaryRow", dict(row)) if row is not None else None

    async def batch_cost_tokens(self, job_ids: list[str]) -> dict[str, dict[str, float | int]]:
        """Return {job_id: {total_cost_usd, total_tokens}} for a batch of jobs.

        Jobs without telemetry data are omitted from the result.
        """
        if not job_ids:
            return {}
        # Use IN clause with positional placeholders for SQLite/Postgres compat
        placeholders = ", ".join(f":id_{i}" for i in range(len(job_ids)))
        params = {f"id_{i}": jid for i, jid in enumerate(job_ids)}
        result = await self._session.execute(
            text(
                f"SELECT job_id, total_cost_usd, input_tokens, output_tokens, "
                f"cache_read_tokens, cache_write_tokens "
                f"FROM job_telemetry_summary WHERE job_id IN ({placeholders}) AND session_kind = 'job'"
            ),
            params,
        )
        out: dict[str, dict[str, float | int]] = {}
        for row in result.mappings().all():
            total_tokens = (
                int(row["input_tokens"])
                + int(row["output_tokens"])
                + int(row["cache_read_tokens"])
                + int(row["cache_write_tokens"])
            )
            out[row["job_id"]] = {
                "total_cost_usd": float(row["total_cost_usd"]),
                "total_tokens": total_tokens,
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
            }
        return out
