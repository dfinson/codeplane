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
from typing import TYPE_CHECKING

import structlog

from backend.models.events import DomainEventKind

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.services.agent_adapter import AgentAdapterInterface

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

{
  "original_task": "<verbatim from the original prompt>",
  "session_number": <integer>,
  "accomplished": [
    {
      "what": "<what was achieved>",
      "how": "<approach taken>",
      "files_affected": ["<relative path>", ...],
      "notes": "<non-obvious decisions, gotchas, or null>"
    }
  ],
  "file_states": [
    {
      "path": "<relative path>",
      "status": "<complete|partial|read_only|created|deleted>",
      "summary": "<what changed and why>",
      "partial_state": "<if partial: exactly what is done vs remaining, else null>",
      "known_issues": "<TODOs, FIXMEs, known problems, else null>"
    }
  ],
  "decisions": [
    {
      "decision": "<what was decided>",
      "rationale": "<why>",
      "alternatives_rejected": "<what else was considered and why not, or null>",
      "affects": ["<file path or component name>"]
    }
  ],
  "operator_instructions": [
    {
      "seq": <integer starting at 1>,
      "content": "<verbatim operator message>",
      "agent_response_summary": "<one sentence: what the agent did in response>"
    }
  ],
  "in_progress": [
    {
      "description": "<what is being worked on>",
      "file": "<which file>",
      "done_so_far": "<what part is complete>",
      "remaining": "<what still needs to happen>"
    }
  ],
  "resume_instructions": "<imperative: 'Complete X in file Y...' — next action for the new agent>",
  "blockers_and_open_questions": [
    {
      "issue": "<the issue>",
      "context": "<relevant context>",
      "suggested_resolution": "<suggestion or null>"
    }
  ],
  "verification_state": {
    "tests_run": <boolean>,
    "tests_passed": <boolean or null>,
    "build_run": <boolean>,
    "build_passed": <boolean or null>,
    "notes": "<anything relevant about test/build state, or null>"
  }
}

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
        adapter: AgentAdapterInterface,
    ) -> None:
        self._session_factory = session_factory
        self._adapter = adapter

    async def summarize_and_store(
        self,
        job_id: str,
        session_number: int,
        original_task: str,
    ) -> str:
        """Generate a JSON session summary, store it as an artifact, and return the JSON string."""
        from backend.persistence.artifact_repo import ArtifactRepository
        from backend.persistence.event_repo import EventRepository
        from backend.services.artifact_service import ArtifactService

        async with self._session_factory() as session:
            event_repo = EventRepository(session)
            artifact_repo = ArtifactRepository(session)

            # --- Fetch and clean transcript ---
            transcript_events = await event_repo.list_by_job(
                job_id,
                kinds=[DomainEventKind.transcript_updated],
            )
            cleaned_turns = _clean_transcript(transcript_events)

            # --- Fetch changed file paths ---
            diff_events = await event_repo.list_by_job(
                job_id,
                kinds=[DomainEventKind.diff_updated],
            )
            changed_files = _extract_changed_files(diff_events)

            # --- Build prompt ---
            transcript_text = _format_transcript(cleaned_turns)
            changed_files_text = "\n".join(sorted(changed_files)) if changed_files else "None recorded"
            prompt = _SYSTEM_PROMPT.format(
                transcript=transcript_text,
                changed_files=changed_files_text,
                original_task=original_task,
                session_number=session_number,
            )

            # --- Call LLM ---
            log.info("summarization_started", job_id=job_id, session=session_number)
            raw = await self._adapter.complete(prompt)
            summary_json = _extract_json(raw, job_id, original_task, session_number)

            # --- Store artifact ---
            artifact_svc = ArtifactService(artifact_repo)
            await artifact_svc.store_session_summary(job_id, session_number, summary_json)
            await session.commit()

        log.info("summarization_complete", job_id=job_id, session=session_number)
        return summary_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_transcript(events: list) -> list[dict]:  # type: ignore[type-arg]
    """Filter and deduplicate transcript events, keeping only agent+operator turns."""
    seen: set[str] = set()
    result = []
    prev_role = None
    prev_content = None

    for ev in events:
        role = ev.payload.get("role", "")
        content = (ev.payload.get("content") or "").strip()

        # Skip empty, skip tool scaffolding noise (role not agent/operator/user)
        if not content:
            continue
        if role not in ("agent", "operator", "user"):
            continue

        # Normalise "user" → "operator"
        if role == "user":
            role = "operator"

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
        result.append({"role": role, "content": content, "timestamp": ev.payload.get("timestamp", "")})

    return result


def _format_transcript(turns: list[dict]) -> str:  # type: ignore[type-arg]
    parts = []
    for i, turn in enumerate(turns, 1):
        parts.append(f"[{i}] {turn['role'].upper()}: {turn['content']}")
    return "\n---\n".join(parts) if parts else "(no transcript recorded)"


def _extract_changed_files(diff_events: list) -> list[str]:  # type: ignore[type-arg]
    """Extract unique changed file paths from diff_updated events."""
    paths: set[str] = set()
    for ev in diff_events:
        for f in ev.payload.get("changed_files", []):
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


def _build_resume_prompt(
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
