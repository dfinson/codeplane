"""Classify tool errors as agent mistakes vs genuine tool failures.

When a tool call returns is_error/success=False, the raw boolean alone
conflates two very different situations:

- **agent_error**: The agent invoked the tool incorrectly — bad arguments,
  wrong file path, typo in an edit string, syntax error in a shell command.
  The tool itself works fine; the agent just made a mistake (and will
  typically retry).

- **tool_error**: The tool itself failed to perform its function — permission
  denied, I/O error, network timeout, disk full, internal crash.

Classification runs as a **batch LLM call** at job completion via
``UtilitySessionService``.  During execution, spans are written with
``error_kind = NULL``.  After the job ends, all unclassified failed spans
are sent to a cheap model in one shot and updated in-place.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

ErrorKind = Literal["agent_error", "tool_error"]

log = structlog.get_logger()

# Maximum error text length sent to the LLM per span (keeps prompt small)
_MAX_ERROR_CHARS = 500

_CLASSIFICATION_PROMPT = """\
You are classifying tool call errors from a coding agent session.

For each error below, decide:
- "agent_error" — the agent invoked the tool wrong (bad args, wrong path, \
typo in edit string, syntax error in shell command, file not found because \
the agent guessed the path, etc.). The tool works fine; the agent made a mistake.
- "tool_error" — the tool itself failed (permission denied, disk full, \
timeout, network error, I/O error, crash, OOM, etc.). The agent's request \
was reasonable but the environment couldn't fulfil it.

Errors:
{errors_block}

Return ONLY a JSON array of strings, one per error, in the same order.
Example: ["agent_error", "tool_error", "agent_error"]
"""


async def classify_tool_errors_batch(
    session: AsyncSession,
    job_id: str,
    utility_complete: Any,
) -> int:
    """Batch-classify all unclassified tool errors for a job via LLM.

    ``utility_complete`` is an async callable with signature
    ``(prompt: str, timeout: float) -> str`` — typically
    ``UtilitySessionService.complete``.

    Returns the number of spans updated.
    """
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepo

    repo = TelemetrySpansRepo(session)
    errors = await repo.get_unclassified_errors(job_id)

    if not errors:
        return 0

    # Build the prompt
    lines: list[str] = []
    for i, err in enumerate(errors, 1):
        snippet = (err["error_text"] or "")[:_MAX_ERROR_CHARS]
        lines.append(f"{i}. Tool: {err['name']}\n   Error: {snippet}")
    errors_block = "\n".join(lines)
    prompt = _CLASSIFICATION_PROMPT.format(errors_block=errors_block)

    try:
        raw = await utility_complete(prompt, 30.0)
    except Exception:
        log.warning("tool_error_classify_llm_failed", job_id=job_id, exc_info=True)
        return 0

    if not raw:
        return 0

    # Parse the JSON array from the response
    classifications = _parse_classifications(raw, len(errors))
    if classifications is None:
        log.warning(
            "tool_error_classify_parse_failed",
            job_id=job_id,
            response=raw[:200],
        )
        return 0

    # Build updates and apply
    updates: list[tuple[int, str]] = []
    agent_error_delta = 0
    for err, kind in zip(errors, classifications, strict=True):
        updates.append((err["id"], kind))
        if kind == "agent_error":
            agent_error_delta += 1

    await repo.batch_update_error_kind(updates)

    # Update the summary counter so it reflects the split
    if agent_error_delta:
        from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepo

        await TelemetrySummaryRepo(session).increment(job_id, agent_error_count=agent_error_delta)

    log.info(
        "tool_errors_classified",
        job_id=job_id,
        total=len(updates),
        agent_errors=agent_error_delta,
        tool_errors=len(updates) - agent_error_delta,
    )
    return len(updates)


def _parse_classifications(raw: str, expected_count: int) -> list[ErrorKind] | None:
    """Extract a JSON array of error kinds from the LLM response."""
    # Strip markdown fencing if present
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: -len("```")]
        text = text.strip()

    try:
        arr = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON array in the response
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return None
        try:
            arr = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

    if not isinstance(arr, list) or len(arr) != expected_count:
        return None

    valid: set[str] = {"agent_error", "tool_error"}
    result: list[ErrorKind] = []
    for item in arr:
        s = str(item).strip().lower()
        if s not in valid:
            return None
        result.append(s)  # type: ignore[arg-type]
    return result
