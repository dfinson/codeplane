"""Background motivation summarization service.

Processes telemetry spans that have preceding_context (captured at
tool-call time for mutative actions) but no motivation_summary yet.
Calls a cheap LLM (gpt-4o-mini via the configured utility model) to
generate a concise narrative explanation of *why* the change was made.

Two-pass pipeline:
1. File-level: preceding_context → motivation_summary (flowing paragraph per span)
2. Edit-level: tool_args → edit_motivations (flowing paragraph per edit, with edit_key)

Runs as a periodic drain loop during the application lifespan.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.exc import DBAPIError

from backend.services.tools.parsing_utils import ensure_dict

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.services.completers.naming_service import Completable

log = structlog.get_logger()

_PROMPT_BODY = (
    "Write a single short paragraph (2-4 sentences) that flows naturally as prose.\n"
    "Explain what was changed AND what prompted it — the upstream finding, bug, "
    "test failure, or prior read that led here. Cite concrete file paths, function "
    "names, finding IDs, or todo IDs from the context.\n\n"
    "Rules:\n"
    '- Third person, abstract voice — no "I", no "the agent", no "this change".\n'
    "- Never restate what the diff shows. Only explain what ISN'T obvious.\n"
    '- Never use filler ("aligns with", "ensures consistency", "improves").\n'
    "- Never fabricate references — only cite what appears in the provided context.\n"
    "- Plain text only — no markdown, no bullets, no headers.\n"
    "- {scope_instruction}\n"
)

_SYSTEM_PROMPT = "You narrate why a code change was made. " + _PROMPT_BODY.format(
    scope_instruction=(
        "Describe the change at the file level — what semantic object (function, class, module) was affected and why."
    )
)

_EDIT_SYSTEM_PROMPT = "You narrate why a specific code edit was made. " + _PROMPT_BODY.format(
    scope_instruction=(
        "Focus on this single edit within the file — what specific construct was modified and what triggered it."
    )
)

# Batch size for each drain cycle
_BATCH_SIZE = 20

# Pause between drain cycles (seconds)
_DRAIN_INTERVAL = 10.0


def _build_user_prompt(
    tool_name: str,
    tool_args_json: str | None,
    preceding_context: str,
    job_description: str | None = None,
) -> str:
    """Assemble the user prompt from span data and preceding context."""
    parts: list[str] = []
    if job_description:
        parts.append(f"JOB DESCRIPTION:\n{job_description}\n")
    parts.append(f"PRECEDING CONTEXT (most recent transcript):\n{preceding_context}\n")
    parts.append(f"TOOL CALLED: {tool_name}")
    if tool_args_json:
        parts.append(f"TOOL ARGS:\n{tool_args_json}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Edit-level helpers
# ---------------------------------------------------------------------------


def _compute_edit_key(tool_name: str, parsed_args: dict[str, Any]) -> str:
    """Compute a stable fingerprint for a single edit operation."""
    if tool_name in ("create", "create_file", "Write"):
        content = str(parsed_args.get("file_text", "") or parsed_args.get("content", ""))
        h = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"create:{h}"
    # Replace/edit tools — fingerprint from old_str
    old_str = str(
        parsed_args.get("old_str", "") or parsed_args.get("oldString", "") or parsed_args.get("old_string", "") or ""
    )
    if old_str:
        h = hashlib.sha256(old_str.encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"replace:{h}"
    # Insert tools
    line = parsed_args.get("insert_line") or parsed_args.get("insertLine")
    if line is not None:
        return f"insert:L{line}"
    return f"unknown:{hashlib.sha256(json.dumps(parsed_args, sort_keys=True).encode()).hexdigest()[:12]}"


def _format_mini_diff(tool_name: str, parsed_args: dict[str, Any], file_path: str | None) -> str:
    """Format tool_args into a readable mini-diff for the LLM prompt."""
    header = f"FILE: {file_path}" if file_path else "FILE: (unknown)"

    if tool_name in ("create", "create_file", "Write"):
        content = str(parsed_args.get("file_text", "") or parsed_args.get("content", ""))
        preview = content
        return f"{header}\nCREATED (new file):\n+ {preview}"

    old_str = str(
        parsed_args.get("old_str", "") or parsed_args.get("oldString", "") or parsed_args.get("old_string", "") or ""
    )
    new_str = str(
        parsed_args.get("new_str", "") or parsed_args.get("newString", "") or parsed_args.get("new_string", "") or ""
    )
    if old_str or new_str:
        old_lines = "\n".join(f"- {line}" for line in old_str.splitlines()) if old_str else "- (empty)"
        new_lines = "\n".join(f"+ {line}" for line in new_str.splitlines()) if new_str else "+ (empty)"
        return f"{header}\nREPLACED:\n{old_lines}\nWITH:\n{new_lines}"

    # Insert
    line = parsed_args.get("insert_line") or parsed_args.get("insertLine")
    new_text = str(parsed_args.get("new_text", "") or parsed_args.get("newText", "") or "")
    if line is not None:
        preview = new_text
        return f"{header}\nINSERTED at line {line}:\n+ {preview}"

    return f"{header}\nTOOL: {tool_name}\nARGS: {json.dumps(parsed_args)}"


def _build_edit_prompt(
    tool_name: str,
    parsed_args: dict[str, Any],
    file_path: str | None,
    preceding_context: str | None,
    file_level_summary: str | None,
) -> str:
    """Build the edit-level prompt with mini-diff and file context."""
    parts: list[str] = []
    if file_level_summary:
        parts.append(f"FILE-LEVEL SUMMARY:\n{file_level_summary}\n")
    if preceding_context:
        parts.append(f"PRECEDING CONTEXT:\n{preceding_context}\n")
    parts.append(f"SPECIFIC EDIT:\n{_format_mini_diff(tool_name, parsed_args, file_path)}")
    return "\n".join(parts)


class MotivationService:
    """Generates motivation summaries for mutative tool spans."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        completer: Completable,
    ) -> None:
        self._session_factory = session_factory
        self._completer = completer

    async def drain_unsummarized(self) -> int:
        """Process a batch of unsummarized spans. Returns count processed."""
        from backend.persistence.job_repo import JobRepository
        from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository

        async with self._session_factory() as session:
            repo = TelemetrySpansRepository(session)
            spans = await repo.unsummarized_spans(limit=_BATCH_SIZE)

            if not spans:
                return 0

            # Pre-fetch job descriptions for the batch
            job_ids = {s["job_id"] for s in spans}
            job_descriptions: dict[str, str | None] = {}
            job_repo = JobRepository(session)
            for jid in job_ids:
                job_row = await job_repo.get(jid)
                if job_row:
                    desc = getattr(job_row, "description", None) or getattr(job_row, "prompt", None)
                    job_descriptions[jid] = str(desc) if desc else None
                else:
                    job_descriptions[jid] = None

            processed = 0
            for span in spans:
                try:
                    prompt = _build_user_prompt(
                        tool_name=span["name"],
                        tool_args_json=span.get("tool_args_json"),
                        preceding_context=span["preceding_context"],
                        job_description=job_descriptions.get(span["job_id"]),
                    )
                    full_prompt = f"SYSTEM:\n{_SYSTEM_PROMPT}\n\nUSER:\n{prompt}"
                    from backend.services.inference_account import inference_scope

                    with inference_scope(job_id=span["job_id"]):
                        result = await self._completer.complete(full_prompt)
                    summary_text = result if isinstance(result, str) else getattr(result, "text", str(result))
                    summary_text = summary_text.strip()

                    if summary_text:
                        await repo.set_motivation_summary(span["id"], summary_text)
                        processed += 1
                except (DBAPIError, OSError, ValueError):
                    log.warning(
                        "motivation_summarize_failed",
                        span_id=span["id"],
                        exc_info=True,
                    )

            await session.commit()
            return processed

    async def drain_edit_motivations(self) -> int:
        """Second pass: generate per-edit motivations for file_write spans."""
        from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository

        async with self._session_factory() as session:
            repo = TelemetrySpansRepository(session)
            spans = await repo.unenriched_edit_spans(limit=_BATCH_SIZE)

            if not spans:
                return 0

            processed = 0
            for span in spans:
                try:
                    tool_args_raw = span.get("tool_args_json")
                    if not tool_args_raw:
                        # No args to parse — mark as empty array to skip next time
                        await repo.set_edit_motivations(span["id"], "[]")
                        processed += 1
                        continue

                    parsed_args = ensure_dict(tool_args_raw)
                    if parsed_args is None:
                        await repo.set_edit_motivations(span["id"], "[]")
                        processed += 1
                        continue

                    edit_key = _compute_edit_key(span["name"], parsed_args)
                    prompt = _build_edit_prompt(
                        tool_name=span["name"],
                        parsed_args=parsed_args,
                        file_path=span.get("tool_target"),
                        preceding_context=span.get("preceding_context"),
                        file_level_summary=span.get("motivation_summary"),
                    )
                    full_prompt = f"SYSTEM:\n{_EDIT_SYSTEM_PROMPT}\n\nUSER:\n{prompt}"
                    from backend.services.inference_account import inference_scope

                    with inference_scope(job_id=span["job_id"]):
                        result = await self._completer.complete(full_prompt)
                    summary_text = result if isinstance(result, str) else getattr(result, "text", str(result))
                    summary_text = summary_text.strip()

                    edit_entry = {
                        "edit_key": edit_key,
                        "summary": summary_text or "",
                    }
                    await repo.set_edit_motivations(
                        span["id"],
                        json.dumps([edit_entry], ensure_ascii=False),
                    )
                    processed += 1
                except (DBAPIError, OSError, ValueError):
                    log.warning(
                        "edit_motivation_failed",
                        span_id=span["id"],
                        exc_info=True,
                    )

            await session.commit()
            return processed

    async def drain_loop(self) -> None:
        """Run forever, periodically processing unsummarized spans."""
        while True:
            try:
                # Pass 1: file-level summaries
                count = await self.drain_unsummarized()
                if count:
                    log.info("motivation_batch_processed", count=count)

                # Pass 2: edit-level motivations
                edit_count = await self.drain_edit_motivations()
                if edit_count:
                    log.info("edit_motivation_batch_processed", count=edit_count)
            except (DBAPIError, OSError):
                log.warning("motivation_drain_error", exc_info=True)
            await asyncio.sleep(_DRAIN_INTERVAL)
