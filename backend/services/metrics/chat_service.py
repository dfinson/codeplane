"""Chat-driven metrics composer — stateful conversational agent.

Each conversation is a persistent multi-turn session kept in memory until
the user starts a new conversation or the server restarts.  The LLM
maintains full awareness of the conversation history and decides what to
do: ask for clarification, explain feasibility, run SQL queries, or
respond with a visualization.

The system prompt describes available tables, visualization templates,
and the JSON action protocol.  The LLM replies with a structured JSON
action on every turn; the service executes queries and feeds results
back into the conversation for the LLM to format.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog

from backend.services.metrics.query_executor import (
    QueryValidationError,
    execute_query,
)

if TYPE_CHECKING:
    from backend.services.sidecar.session import SidecarSessionManager

log = structlog.get_logger()

# Wall-clock budget per turn (seconds).
_TURN_DEADLINE_S = 30.0
_MIN_REMAINING_S = 3.0


# ---------------------------------------------------------------------------
# In-memory conversation store — lives until server restart
# ---------------------------------------------------------------------------

# conversation_id -> list of {"role": "user"|"assistant", "content": str}
_conversations: dict[str, list[dict[str, str]]] = {}


def clear_conversation(conversation_id: str) -> None:
    """Drop an in-memory conversation session."""
    _conversations.pop(conversation_id, None)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a data analyst assistant for CodePlane, an AI coding agent control \
plane.  You help users understand their agent telemetry through conversation.

## Your capabilities
- Run SQLite SELECT queries against the telemetry database
- Visualize results using dashboard templates
- Explain what the data means
- Ask clarifying questions when the request is ambiguous
- Explain what is and isn't feasible with the available data

## Available tables

### job_telemetry_spans
Columns: id, job_id, session_kind, span_type, name, started_at, \
duration_ms, attrs_json, tool_category, tool_target, turn_number, \
execution_phase, is_retry, retries_span_id, input_tokens, output_tokens, \
cache_read_tokens, cache_write_tokens, cost_usd, tool_args_json, \
result_size_bytes, error_kind, turn_id, preceding_context, \
motivation_summary, edit_motivations, created_at

Notes:
- session_kind is 'job' for main sessions, 'sidecar' for utility sessions
- span_type: 'llm_call', 'tool_call', 'turn'
- tool_category: 'file_read', 'file_write', 'shell', 'search', 'browser', etc.
- execution_phase: 'investigation', 'implementation', 'debugging', 'verification'
- cost_usd is a nullable verified-USD read projection. NULL is unknown, not zero.
- attrs_json.estimated_cost_usd is an estimate only and must not enter verified USD totals.
- Timestamps are ISO 8601 strings

### job_telemetry_summary
Columns: job_id, session_kind, sdk, model, repo, branch, status, \
created_at, completed_at, duration_ms, input_tokens, output_tokens, \
cache_read_tokens, cache_write_tokens, total_cost_usd, \
premium_requests, llm_call_count, tool_call_count, \
tool_failure_count, total_turns, retry_count, file_read_count, \
file_write_count, diff_lines_added, diff_lines_removed, \
peak_turn_cost_usd, avg_turn_cost_usd, compactions, \
tokens_compacted, subagent_cost_usd, agent_error_count, \
tool_error_count, description, job_mode, total_cost_with_sidecar_usd

Notes:
- Composite PK: (job_id, session_kind)
- total_cost_usd is source-qualified USD; legacy_cost_unverified retains the old numeric value without currency proof.
- estimated_cost_usd is separately labeled estimated API-equivalent cost, not verified spend.
- Filter session_kind = 'job' for main session metrics
- repo contains full path -- use LIKE '%/repo_name' for matching
- status: 'completed', 'failed', 'cancelled', 'running', 'review', 'pending'

### job_cost_attribution
Columns: id, job_id, dimension, bucket, cost_usd, input_tokens, \
output_tokens, call_count, cache_read_tokens, cache_write_tokens, \
model, created_at

Notes:
- dimension: 'phase', 'tool_category', 'turn', 'activity', 'action', \
'purpose', 'action_purpose', 'model'
- activity buckets: 'implementation', 'debugging', 'investigation', \
'verification', 'git_ops', 'communication', 'setup', 'reasoning', 'overhead'

### jobs
Columns: id, status, error, error_kind, repo_path, branch, sdk, \
created_at, started_at, completed_at, parent_job_id, name, \
description, mode

## Visualization templates
- stat_card: single number. Data: [{"value": N, "label": "text"}]
- bar_chart: categorical bars. Data: [{"name": "X", "value": N}, ...]
- line_chart: time series. Data: [{"date": "YYYY-MM-DD", "value": N}, ...]
- stacked_bar: multi-series bars. Data: [{"name": "X", "s1": N, "s2": N}, ...]
- donut: proportional. Data: [{"name": "X", "value": N}, ...]
- table: tabular. Data: [{"col1": val, "col2": val}, ...]
- heatmap: 2D grid. Data: [{"x": "X", "y": "Y", "value": N}, ...]

## Response protocol
You MUST reply with a single JSON object -- no markdown fences, no \
commentary outside the JSON.  Choose ONE of these action types:

### 1. Run SQL queries
Use when you need data to answer the question.
{"action": "query", "queries": [{"sql": "SELECT ...", "purpose": "..."}], \
"viz": "template_name", "viz_config": {"title": "...", "x_key": "...", "y_key": "..."}}

### 2. Respond with a message (explanation, clarification, greeting)
Use for conversation, clarification, feasibility explanations, or when \
no data query is needed.
{"action": "message", "content": "your message to the user"}

### 3. Present formatted results (only after you receive query results)
Use after the system shows you query results.
{"action": "result", "narrative": "1-2 sentence summary", "title": "short title", \
"viz": "template_name", "viz_data": [...], "viz_config": {...}}

## SQL rules
1. ONLY SELECT/WITH queries  2. Filter session_kind = 'job' unless asked about sidecars
3. Missing currency is unknown, never zero. Native Copilot units and estimates are not USD.
4. Date filtering: WHERE created_at >= datetime('now', '-N days')
5. Use ASCII operators only (>=, <=, !=) -- never Unicode
6. Keep queries efficient with GROUP BY and LIMIT
"""


class MetricsChatService:
    """Stateful conversational metrics agent."""

    def __init__(
        self,
        sidecar: SidecarSessionManager,
        session_factory: Any,
    ) -> None:
        self._sidecar = sidecar
        self._session_factory = session_factory

    async def _complete(self, messages: list[dict[str, str]], *, timeout: float) -> str:
        """Flatten conversation history into a single prompt and send through the sidecar."""
        flat = (
            _SYSTEM_PROMPT
            + "\n\n"
            + "\n".join(f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in messages)
        )
        return await self._sidecar.complete(flat, timeout=timeout)

    async def ask(
        self,
        question: str,
        conversation_id: str,
        *,
        period_days: int | None = None,
    ) -> ChatResponse:
        """Process a user question within a persistent conversation.

        The full message history is sent to the LLM on every turn.
        If the LLM issues SQL queries, results are fed back and the
        LLM is called again to format the response.
        """
        t0 = time.monotonic()
        deadline = t0 + _TURN_DEADLINE_S

        messages = _conversations.setdefault(conversation_id, [])

        # Build the user message with optional period hint
        user_content = question
        if period_days:
            user_content += (
                f"\n\n(Default time filter: last {period_days} days"
                f" -- WHERE created_at >= datetime('now', '-{period_days} days'))"
            )
        messages.append({"role": "user", "content": user_content})

        # --- Agent loop: call LLM, handle actions, repeat if needed ---
        attempt = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining < _MIN_REMAINING_S:
                msg = "I ran out of time processing that. Could you try rephrasing?"
                messages.append({"role": "assistant", "content": json.dumps({"action": "message", "content": msg})})
                return ChatResponse(narrative=msg)

            attempt += 1
            try:
                raw = await asyncio.wait_for(
                    self._complete(messages, timeout=remaining),
                    timeout=remaining,
                )
            except (TimeoutError, Exception) as exc:
                log.warning("metrics_chat_llm_error", attempt=attempt, error=str(exc))
                msg = "I'm having trouble reaching the model right now. Please try again in a moment."
                messages.append({"role": "assistant", "content": json.dumps({"action": "message", "content": msg})})
                return ChatResponse(narrative=msg)

            parsed = _parse_json(raw)

            # If the LLM didn't return valid JSON, nudge it
            if parsed is None:
                log.debug("metrics_chat_bad_json", attempt=attempt, raw=raw[:200])
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your response wasn't valid JSON. Please reply with a "
                            "single JSON object using the action protocol."
                        ),
                    }
                )
                continue

            action = parsed.get("action", "message")

            # --- Action: message (conversation, clarification, explanation) ---
            if action == "message":
                content = parsed.get("content", "")
                messages.append({"role": "assistant", "content": raw})
                return ChatResponse(narrative=content)

            # --- Action: result (formatted viz + narrative) ---
            if action == "result":
                messages.append({"role": "assistant", "content": raw})
                return ChatResponse(
                    narrative=parsed.get("narrative", ""),
                    title=parsed.get("title", ""),
                    viz=parsed.get("viz", "table"),
                    viz_config=parsed.get("viz_config", {}),
                    viz_data=parsed.get("viz_data", []),
                    sql_queries=parsed.get("sql_queries", []),
                )

            # --- Action: query (run SQL then loop back for formatting) ---
            if action == "query":
                queries = parsed.get("queries", [])
                viz = parsed.get("viz", "table")

                messages.append({"role": "assistant", "content": raw})

                if not queries:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "No queries were provided. Either generate SQL queries or respond with a message."
                            ),
                        }
                    )
                    continue

                # Execute queries and build a results report
                results_parts: list[str] = []
                executed_sql: list[str] = []
                exec_remaining = deadline - time.monotonic()

                for i, q in enumerate(queries):
                    sql = q.get("sql", "")
                    purpose = q.get("purpose", "")
                    try:
                        rows = await execute_query(
                            self._session_factory,
                            sql,
                            timeout_seconds=min(exec_remaining, 10.0),
                        )
                        results_parts.append(
                            f"Query {i + 1} ({purpose}): {len(rows)} rows\nSQL: {sql}\nResults: {json.dumps(rows[:50])}"
                        )
                        executed_sql.append(sql)
                    except (QueryValidationError, Exception) as exc:
                        results_parts.append(f"Query {i + 1} ({purpose}): FAILED\nSQL: {sql}\nError: {exc}")

                # Inject results back into conversation for the LLM
                results_msg = "\n\n".join(results_parts)
                if executed_sql:
                    results_msg += (
                        f'\n\nFormat these results as a "{viz}" visualization. Respond with an action=result JSON.'
                    )
                else:
                    results_msg += (
                        "\n\nAll queries failed. You can fix the SQL and try again "
                        "(action=query), explain the issue to the user (action=message), "
                        "or ask for clarification (action=message)."
                    )
                messages.append({"role": "user", "content": results_msg})
                continue  # Loop back for the LLM to format or retry

            # Unknown action -- ask for correction
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": f"Unknown action '{action}'. Use 'query', 'message', or 'result'.",
                }
            )


class ChatResponse:
    """Result from a metrics chat question."""

    __slots__ = (
        "narrative",
        "title",
        "viz",
        "viz_config",
        "viz_data",
        "sql_queries",
        "suggestion",
        "error",
        "raw_response",
        "message_id",
    )

    def __init__(
        self,
        *,
        narrative: str = "",
        title: str = "",
        viz: str = "table",
        viz_config: dict[str, Any] | None = None,
        viz_data: list[Any] | None = None,
        sql_queries: list[str] | None = None,
        suggestion: str | None = None,
        error: bool = False,
        raw_response: str | None = None,
    ) -> None:
        self.narrative = narrative
        self.title = title
        self.viz = viz
        self.viz_config = viz_config or {}
        self.viz_data = viz_data or []
        self.sql_queries = sql_queries or []
        self.suggestion = suggestion
        self.error = error
        self.raw_response = raw_response
        self.message_id = uuid.uuid4().hex[:12]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "messageId": self.message_id,
            "narrative": self.narrative,
            "error": self.error,
        }
        if self.title:
            d["title"] = self.title
        if not self.error:
            d["viz"] = self.viz
            d["vizConfig"] = self.viz_config
            d["vizData"] = self.viz_data
        if self.sql_queries:
            d["sqlQueries"] = self.sql_queries
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d

    def condensed_summary(self) -> str:
        """One-line summary for conversation memory."""
        if self.error:
            return f"[error] {self.narrative[:200]}"
        return f"{self.title}: {self.narrative[:200]}"


def _parse_json(raw: str) -> dict[str, Any] | None:
    """Extract JSON from LLM response, handling markdown code fences."""
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines[1:] if not line.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
        return None
