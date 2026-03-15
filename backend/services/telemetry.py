"""Agent-agnostic telemetry collection.

Defines a standard telemetry contract that any agent adapter
(Copilot SDK, Claude Code, etc.) feeds into via simple method calls.
The adapter is responsible for translating SDK-specific events into
these standardized telemetry operations.

Contract:
  telemetry.start_job(job_id)
  telemetry.end_job(job_id)
  telemetry.record_llm_usage(job_id, ...)     # token counts, model, cost
  telemetry.record_tool_call(job_id, ...)     # tool invocation
  telemetry.record_context_change(job_id, ...)# context window state
  telemetry.record_approval(job_id, ...)      # approval wait
  telemetry.record_message(job_id, ...)       # conversation messages
  telemetry.get(job_id) -> JobTelemetry       # read aggregated data
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ToolCallRecord:
    """A single tool invocation."""

    name: str
    duration_ms: float
    success: bool
    timestamp: float


@dataclass
class LLMCallRecord:
    """A single LLM API call."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost: float
    duration_ms: float
    timestamp: float


@dataclass
class JobTelemetry:
    """Aggregated telemetry for a single job run.

    This is the standard shape exposed to the API/UI regardless of
    which agent SDK produced the data.
    """

    job_id: str
    model: str = ""
    start_time: float = 0.0
    end_time: float = 0.0

    # Token totals (accumulated across all LLM calls)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_cost: float = 0.0

    # Context window
    context_window_size: int = 0
    current_context_tokens: int = 0
    compactions: int = 0
    tokens_compacted: int = 0

    # Tool tracking
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tool_call_count: int = 0
    total_tool_duration_ms: float = 0.0

    # LLM call tracking
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    llm_call_count: int = 0
    total_llm_duration_ms: float = 0.0

    # Approval tracking
    approval_count: int = 0
    total_approval_wait_ms: float = 0.0

    # Message counts
    agent_messages: int = 0
    operator_messages: int = 0

    @property
    def duration_ms(self) -> float:
        if not self.start_time:
            return 0.0
        end = self.end_time if self.end_time else time.monotonic()
        return (end - self.start_time) * 1000

    @property
    def context_utilization(self) -> float:
        """Fraction of context window used (0.0-1.0)."""
        if self.context_window_size <= 0:
            return 0.0
        return min(1.0, self.current_context_tokens / self.context_window_size)

    def to_dict(self) -> dict[str, object]:
        """Serialize to API-friendly dict."""
        return {
            "jobId": self.job_id,
            "model": self.model,
            "durationMs": round(self.duration_ms),
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "promptTokens": self.input_tokens,
            "completionTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "cacheReadTokens": self.cache_read_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
            "totalCost": round(self.total_cost, 6),
            "contextWindowSize": self.context_window_size,
            "currentContextTokens": self.current_context_tokens,
            "contextUtilization": round(self.context_utilization, 3),
            "compactions": self.compactions,
            "tokensCompacted": self.tokens_compacted,
            "toolCallCount": self.tool_call_count,
            "totalToolDurationMs": round(self.total_tool_duration_ms),
            "toolCalls": [
                {"name": tc.name, "durationMs": round(tc.duration_ms), "success": tc.success}
                for tc in self.tool_calls[-200:]
            ],
            "llmCallCount": self.llm_call_count,
            "totalLlmDurationMs": round(self.total_llm_duration_ms),
            "llmCalls": [
                {
                    "model": lc.model,
                    "inputTokens": lc.input_tokens,
                    "outputTokens": lc.output_tokens,
                    "cacheReadTokens": lc.cache_read_tokens,
                    "cacheWriteTokens": lc.cache_write_tokens,
                    "cost": round(lc.cost, 6),
                    "durationMs": round(lc.duration_ms),
                }
                for lc in self.llm_calls[-100:]
            ],
            "approvalCount": self.approval_count,
            "totalApprovalWaitMs": round(self.total_approval_wait_ms),
            "agentMessages": self.agent_messages,
            "operatorMessages": self.operator_messages,
        }


class TelemetryCollector:
    """Agent-agnostic telemetry aggregator.

    Each agent adapter calls these methods to feed telemetry.
    The collector aggregates per-job and exposes via get()/get_all().
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobTelemetry] = {}

    def start_job(self, job_id: str, model: str = "") -> None:
        self._jobs[job_id] = JobTelemetry(
            job_id=job_id,
            model=model,
            start_time=time.monotonic(),
        )

    def end_job(self, job_id: str) -> None:
        tel = self._jobs.get(job_id)
        if tel:
            tel.end_time = time.monotonic()

    def record_llm_usage(
        self,
        job_id: str,
        *,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost: float = 0.0,
        duration_ms: float = 0.0,
    ) -> None:
        """Record an LLM API call's token usage and cost."""
        tel = self._jobs.get(job_id)
        if not tel:
            return
        if model:
            tel.model = model
        tel.input_tokens += input_tokens
        tel.output_tokens += output_tokens
        tel.total_tokens += input_tokens + output_tokens
        tel.cache_read_tokens += cache_read_tokens
        tel.cache_write_tokens += cache_write_tokens
        tel.total_cost += cost
        tel.llm_call_count += 1
        tel.total_llm_duration_ms += duration_ms
        tel.llm_calls.append(
            LLMCallRecord(
                model=model or tel.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                cost=cost,
                duration_ms=duration_ms,
                timestamp=time.monotonic(),
            )
        )
        # Keep last 100 LLM calls
        if len(tel.llm_calls) > 100:
            tel.llm_calls = tel.llm_calls[-100:]

    def record_tool_call(
        self,
        job_id: str,
        *,
        tool_name: str,
        duration_ms: float = 0.0,
        success: bool = True,
    ) -> None:
        """Record a tool invocation."""
        tel = self._jobs.get(job_id)
        if not tel:
            return
        tel.tool_calls.append(
            ToolCallRecord(
                name=tool_name,
                duration_ms=duration_ms,
                success=success,
                timestamp=time.monotonic(),
            )
        )
        tel.tool_call_count += 1
        tel.total_tool_duration_ms += duration_ms
        # Keep last 200 tool calls
        if len(tel.tool_calls) > 200:
            tel.tool_calls = tel.tool_calls[-200:]

    def record_context_change(
        self,
        job_id: str,
        *,
        current_tokens: int = 0,
        window_size: int = 0,
    ) -> None:
        """Record a context window state change."""
        tel = self._jobs.get(job_id)
        if not tel:
            return
        if current_tokens:
            tel.current_context_tokens = current_tokens
        if window_size:
            tel.context_window_size = window_size

    def record_compaction(
        self,
        job_id: str,
        *,
        pre_tokens: int = 0,
        post_tokens: int = 0,
    ) -> None:
        """Record a context compaction event."""
        tel = self._jobs.get(job_id)
        if not tel:
            return
        tel.compactions += 1
        tel.tokens_compacted += max(0, pre_tokens - post_tokens)

    def record_approval(self, job_id: str, *, wait_ms: float = 0.0) -> None:
        tel = self._jobs.get(job_id)
        if tel:
            tel.approval_count += 1
            tel.total_approval_wait_ms += wait_ms

    def record_message(self, job_id: str, *, role: str) -> None:
        tel = self._jobs.get(job_id)
        if not tel:
            return
        if role == "agent":
            tel.agent_messages += 1
        else:
            tel.operator_messages += 1

    def get(self, job_id: str) -> JobTelemetry | None:
        return self._jobs.get(job_id)

    def get_all(self) -> dict[str, JobTelemetry]:
        return dict(self._jobs)


# Global singleton
collector = TelemetryCollector()
