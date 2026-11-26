"""Post-session LLM summarization service.

After a job finishes, this service:
1. Fetches the job's transcript events from the event store
2. Cleans them (deduplicate, filter empty/tool-scaffolding turns)
3. Calls the agent adapter's single-turn complete() with a structured JSON prompt
4. Stores the result as an agent_summary artifact
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, TypedDict, cast

import structlog

from backend.models.events import EventKind


class TranscriptTurn(TypedDict):
    role: str
    content: str
    timestamp: str


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.events import SessionEvent
    from backend.services.completers.naming_service import Completable

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Summarization prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are creating a technical handoff document for a coding agent that is
resuming work on a previous session. Another agent instance will read this
document and pick up exactly where you left off — treat it like briefing
a colleague who is equally skilled but has no memory of the session.

Respond with a single JSON object that matches this schema exactly:

{{
  "original_task": "<verbatim from the original prompt>",
  "session_number": <integer>,
  "accomplished": [
    {{
      "what": "<what was achieved>",
      "how": "<approach taken>",
      "files_affected": ["<relative path>", ...],
      "notes": "<non-obvious decisions, gotchas, or null>"
    }}
  ],
  "file_states": [
    {{
      "path": "<relative path>",
      "status": "<complete|partial|read_only|created|deleted>",
      "summary": "<what changed and why>",
      "partial_state": "<if partial: exactly what is done vs remaining, else null>",
      "known_issues": "<TODOs, FIXMEs, known problems, else null>"
    }}
  ],
  "decisions": [
    {{
      "decision": "<what was decided>",
      "rationale": "<why>",
      "alternatives_rejected": "<what else was considered and why not, or null>",
      "affects": ["<file path or component name>"]
    }}
  ],
  "operator_instructions": [
    {{
      "seq": <integer starting at 1>,
      "content": "<verbatim operator message>",
      "agent_response_summary": "<one sentence: what the agent did in response>"
    }}
  ],
  "in_progress": [
    {{
      "description": "<what is being worked on>",
      "file": "<which file>",
      "done_so_far": "<what part is complete>",
      "remaining": "<what still needs to happen>"
    }}
  ],
  "resume_instructions": "<imperative: 'Complete X in file Y...' — next action for the new agent>",
  "blockers_and_open_questions": [
    {{
      "issue": "<the issue>",
      "context": "<relevant context>",
      "suggested_resolution": "<suggestion or null>"
    }}
  ],
  "verification_state": {{
    "tests_run": <boolean>,
    "tests_passed": <boolean or null>,
    "build_run": <boolean>,
    "build_passed": <boolean or null>,
    "notes": "<anything relevant about test/build state, or null>"
  }}
}}

Rules:
- Use exact file paths (relative to repo root)
- Quote exact function/class names — never say "some function"
- For partial_state: describe the exact line or logical boundary where work stopped
- For resume_instructions: a single direct imperative the new session executes first
- operator_instructions must be verbatim from the transcript, not paraphrased
- in_progress is null if everything is cleanly completed
- blockers_and_open_questions is null if there are none
- Respond ONLY with the JSON object — no preamble, no markdown fences

SESSION TRANSCRIPT (deduplicated, agent+operator turns only):
---
{transcript}
---

CHANGED FILES (from filesystem diff events):
{changed_files}

ORIGINAL TASK:
{original_task}

SESSION NUMBER: {session_number}
"""


class SummarizationService:
    """Generates and stores structured session summaries."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        adapter: Completable,
    ) -> None:
        self._session_factory = session_factory
        self._adapter = adapter

    async def summarize_and_store(
        self,
        job_id: str,
        session_number: int,
        original_task: str,
        *,
        pre_built_transcript: str | None = None,
        pre_built_changed_files: list[str] | None = None,
    ) -> str:
        """Generate a JSON session summary, store it as an artifact, and return the JSON string.

        If *pre_built_transcript* / *pre_built_changed_files* are provided
        (e.g. from a stored session snapshot), they are used directly instead
        of re-reading from the event store.
        """
        from backend.persistence.artifact_repo import ArtifactRepository
        from backend.persistence.database import serialized_write
        from backend.persistence.trail_repo import TrailNodeRepository
        from backend.services.artifacts.artifact_service import ArtifactService

        # --- Read phase (no write lock needed) ---
        trail_repo = TrailNodeRepository(self._session_factory)
        if pre_built_transcript is not None:
            transcript_text = pre_built_transcript
        else:
            transcript_nodes = await trail_repo.get_transcript_nodes(job_id)
            cleaned_turns = _clean_transcript_from_trail(transcript_nodes)
            transcript_text = _format_transcript(cleaned_turns)

        if pre_built_changed_files is not None:
            changed_files = pre_built_changed_files
        else:
            changed_files = await trail_repo.get_all_changed_files(job_id)

        # --- Build prompt ---
        changed_files_text = "\n".join(sorted(changed_files)) if changed_files else "None recorded"
        prompt = _SYSTEM_PROMPT.format(
            transcript=transcript_text,
            changed_files=changed_files_text,
            original_task=original_task,
            session_number=session_number,
        )

        # --- Call LLM ---
        log.info("summarization_started", job_id=job_id, session=session_number)
        from backend.services.inference_account import inference_scope

        with inference_scope(job_id=job_id):
            raw = await self._adapter.complete(prompt, timeout=60)
        summary_json = _extract_json(raw, job_id, original_task, session_number)

        # --- Write phase (serialized through global write lock) ---
        async with serialized_write(self._session_factory) as session:
            artifact_repo = ArtifactRepository(session)
            artifact_svc = ArtifactService(artifact_repo)
            await artifact_svc.store_session_summary(job_id, session_number, summary_json)

        log.info("summarization_complete", job_id=job_id, session=session_number)
        return summary_json

    async def save_snapshot_to_disk(self, job_id: str) -> None:
        """Store a raw session snapshot (cheap, no LLM) for future cold resumes.

        LLM-based summarization is deferred to resume_job() and only fires
        when the SDK session is no longer available for native reconnection.

        Builds the transcript from trail nodes (Phase 2c migration):
        - Assistant turns from step nodes with agent_message
        - Operator turns from request nodes
        - Tool call turns from write sub-nodes with tool metadata
        - Changed files from trail node file manifests
        """
        from backend.persistence.artifact_repo import ArtifactRepository
        from backend.persistence.job_repo import JobRepository
        from backend.persistence.trail_repo import TrailNodeRepository
        from backend.services.artifacts.artifact_service import ArtifactService

        try:
            async with self._session_factory() as session:
                job_repo = JobRepository(session)
                job = await job_repo.get(job_id)
            if job is None:
                raise RuntimeError(f"Cannot collect artifacts for missing job {job_id}")

            from backend.persistence.database import serialized_write

            # Read phase: check existing log and build snapshot
            async with self._session_factory() as session:
                artifact_svc = ArtifactService(ArtifactRepository(session))

                existing = await artifact_svc.get_session_log(job_id)
                if existing is not None:
                    try:
                        from pathlib import Path

                        log_data = json.loads(Path(existing.disk_path).read_text(encoding="utf-8"))
                        recorded = {s.get("session_number") for s in log_data.get("sessions", [])}
                        if job.session_count in recorded:
                            return  # already captured this session
                    except (json.JSONDecodeError, OSError):
                        log.warning("session_log_parse_failed", job_id=job_id, exc_info=True)

            # Build snapshot from trail nodes (read-only)
            trail_repo = TrailNodeRepository(self._session_factory)
            snapshot_nodes = await trail_repo.get_snapshot_turns(job_id)
            changed_files = await trail_repo.get_all_changed_files(job_id)

            # Build cleaned turns from trail nodes
            turns: list[dict[str, Any]] = []
            seen_assistant: set[str] = set()
            for node in snapshot_nodes:
                ts = node.timestamp.isoformat() if node.timestamp else ""

                if node.kind in ("modify", "shell", "explore") and node.agent_message:
                    content = node.agent_message.strip()
                    if not content or content in seen_assistant:
                        continue
                    seen_assistant.add(content)
                    turns.append(
                        {
                            "role": "assistant",
                            "content": content,
                            "timestamp": ts,
                        }
                    )
                elif node.kind == "request" and node.agent_message:
                    content = node.agent_message.strip()
                    if not content:
                        continue
                    turns.append(
                        {
                            "role": "operator",
                            "content": content,
                            "timestamp": ts,
                        }
                    )
                elif node.kind == "write" and node.tool_display:
                    turns.append(
                        {
                            "role": "tool_call",
                            "tool_name": node.tool_name or "tool",
                            "tool_display": node.tool_display or "",
                            "tool_intent": node.tool_intent or "",
                            "tool_success": node.tool_success if node.tool_success is not None else True,
                            "timestamp": ts,
                        }
                    )

            session_data = {
                "original_task": job.prompt,
                "session_number": job.session_count,
                "transcript_turns": turns,
                "changed_files": changed_files,
            }

            slug = (job.worktree_name or job.title or "").strip()

            # Write phase: persist everything through the global write lock
            async with serialized_write(self._session_factory) as session:
                artifact_svc = ArtifactService(ArtifactRepository(session))
                await artifact_svc.upsert_session_log(job_id, session_data, slug=slug)

                if job.worktree_path:
                    collected = await artifact_svc.collect_from_workspace(job_id, job.worktree_path)
                    if collected:
                        log.info("workspace_artifacts_collected", job_id=job_id, count=len(collected))

                if job.sdk_session_id:
                    md_collected = await artifact_svc.collect_from_session_storage(job_id, job.sdk_session_id)
                    if md_collected:
                        log.info("session_storage_markdowns_collected", job_id=job_id, count=len(md_collected))

            log.info("session_log_stored", job_id=job_id, session=job.session_count, turns=len(turns))
        except Exception:
            log.error("session_log_failed", job_id=job_id, exc_info=True)
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_transcript(events: list[SessionEvent]) -> list[TranscriptTurn]:
    """Filter and deduplicate transcript events, keeping only agent+operator turns."""
    seen: set[str] = set()
    result: list[TranscriptTurn] = []
    prev_role = None
    prev_content = None

    for ev in events:
        content = (str(ev.payload.get("content") or "")).strip()

        # Skip empty, skip tool scaffolding noise.
        if not content:
            continue
        if ev.kind == EventKind.message_assistant:
            role = "agent"
        elif ev.kind == EventKind.message_user:
            role = "operator"
        else:
            continue

        # Skip consecutive duplicates (same role + content)
        key = f"{role}:{content}"
        if key == f"{prev_role}:{prev_content}":
            continue
        # Skip globally duplicated content (SDK double-echo)
        if key in seen:
            continue

        seen.add(key)
        prev_role = role
        prev_content = content
        result.append({"role": role, "content": content, "timestamp": str(ev.payload.get("timestamp", ""))})

    return result


def _clean_transcript_from_trail(nodes: list[Any]) -> list[TranscriptTurn]:
    """Build cleaned transcript turns from trail nodes.

    Step nodes (modify/shell/explore) carry agent content in agent_message.
    Request nodes carry operator content in intent (approval interactions).

    Note: General operator chat messages are not yet captured in the trail.
    Only approval-request interactions appear as operator turns here.
    """
    seen: set[str] = set()
    result: list[TranscriptTurn] = []

    for node in nodes:
        if node.kind == "request":
            # Operator approval requests store content in intent
            content = (node.intent or "").strip()
            role = "operator"
        else:
            content = (node.agent_message or "").strip()
            role = "agent"

        if not content:
            continue

        key = f"{role}:{content}"
        if key in seen:
            continue
        seen.add(key)

        timestamp = node.timestamp.isoformat() if node.timestamp else ""
        result.append({"role": role, "content": content, "timestamp": timestamp})

    return result


def _format_transcript(turns: list[TranscriptTurn]) -> str:
    parts = []
    for i, turn in enumerate(turns, 1):
        parts.append(f"[{i}] {turn['role'].upper()}: {turn['content']}")
    return "\n---\n".join(parts) if parts else "(no transcript recorded)"


def extract_changed_files(diff_events: list[SessionEvent]) -> list[str]:
    """Extract unique changed file paths from diff_updated events."""
    paths: set[str] = set()
    for ev in diff_events:
        for f in cast("list[dict[str, Any]]", ev.payload.get("changed_files", [])):
            path = f.get("path") or f.get("new_path") or ""
            if path:
                paths.add(path)
    return sorted(paths)


def _extract_json(raw: str, job_id: str, original_task: str, session_number: int) -> str:
    """Extract valid JSON from the LLM response. Falls back to a minimal summary on failure."""
    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()

    # Try to parse as-is
    try:
        json.loads(text)
        return text
    except (json.JSONDecodeError, ValueError):
        log.debug("summarization_direct_json_parse_failed", job_id=job_id, text_len=len(text))
        pass

    # Try to extract first JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            json.loads(candidate)
            log.warning("summarization_json_extracted_from_text", job_id=job_id)
            return candidate
        except (json.JSONDecodeError, ValueError):
            log.debug("summarization_extracted_json_parse_failed", job_id=job_id)
            pass

    # Fallback: store raw text wrapped in a minimal envelope
    log.warning("summarization_json_parse_failed", job_id=job_id, raw_len=len(raw))
    fallback = {
        "original_task": original_task,
        "session_number": session_number,
        "summarized": False,
        "raw_response": raw[:50_000],  # cap to avoid huge artifacts
        "accomplished": [],
        "file_states": [],
        "decisions": [],
        "operator_instructions": [],
        "in_progress": None,
        "resume_instructions": "Review the raw_response field for context from the previous session.",
        "blockers_and_open_questions": None,
        "verification_state": {
            "tests_run": False,
            "tests_passed": None,
            "build_run": False,
            "build_passed": None,
            "notes": "Summarization failed — see raw_response",
        },
    }
    return json.dumps(fallback, indent=2)


def build_resume_prompt(
    summary_text: str | None,
    changed_files: list[str],
    instruction: str,
    session_number: int,
    job_id: str,
    original_task: str,
) -> str:
    """Build the resume prompt injected as the override_prompt for session N+1."""
    summary_section = summary_text or ("(no summary available \u2014 check the working directory for context)")
    files_section = "\n".join(f"  - {f}" for f in changed_files) if changed_files else "  (no file changes recorded)"

    return (
        f"[RESUMED SESSION \u2014 session {session_number} of job {job_id}]\n\n"
        f"## Original task\n{original_task}\n\n"
        f"## What happened in the previous session\n{summary_section}\n\n"
        f"## Files already modified (present in your working directory)\n{files_section}\n\n"
        f"## Your next instruction from the operator\n{instruction}\n\n"
        "---\n"
        "The working directory already contains all changes from the previous session.\n"
        "Do not re-describe the summary back to the operator.\n"
        "Act on the instruction directly."
    )


def build_followup_prompt(
    summary_text: str | None,
    changed_files: list[str],
    instruction: str,
    parent_job_id: str,
    original_task: str,
) -> str:
    """Build the startup prompt for a new follow-up job created from a finished parent job."""
    summary_section = summary_text or ("(no summary available — inspect the repo and prior job artifacts for context)")
    files_section = "\n".join(f"  - {f}" for f in changed_files) if changed_files else "  (no file changes recorded)"

    return (
        f"[FOLLOW-UP JOB derived from completed job {parent_job_id}]\n\n"
        f"## Original task\n{original_task}\n\n"
        f"## What the previous job accomplished\n{summary_section}\n\n"
        f"## Files touched by the previous job\n{files_section}\n\n"
        f"## New instruction from the operator\n{instruction}\n\n"
        "---\n"
        "This is a new job, not a resumed session.\n"
        "Do not assume the previous job's uncommitted worktree still exists.\n"
        "Use the summary and file list as context, inspect the repository's current state, "
        "and then carry out the new instruction."
    )
