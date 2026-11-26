"""Handoff context loading and prompt building extracted from RuntimeService.

This module handles assembling the context (summaries, changed files, transcripts)
needed to resume or follow-up a job in a new session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.domain import Job
    from backend.services.completers.summarization_service import SummarizationService

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class PendingHandoff:
    admission_id: str | None
    instruction: str
    source_session_number: int
    target_session_number: int


async def load_handoff_context_for_job(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    job: Job,
    summarization_service: SummarizationService | None,
) -> tuple[str | None, list[str]]:
    """Load summary text and changed files for a job's handoff context."""
    from pathlib import Path

    from backend.persistence.artifact_repo import ArtifactRepository
    from backend.persistence.trail_repo import TrailNodeRepository
    from backend.services.artifacts.artifact_service import ArtifactService

    # Use short-lived sessions for reads so we don't hold an implicit
    # transaction open across the (potentially long) summarisation call.
    async with session_factory() as read_session:
        artifact_svc = ArtifactService(ArtifactRepository(read_session))
        summary_artifact = await artifact_svc.get_latest_session_summary(job.id)
        log_artifact = await artifact_svc.get_session_log(job.id) if summary_artifact is None else None

    trail_repo = TrailNodeRepository(session_factory)
    changed_files = await trail_repo.get_all_changed_files(job.id)

    if summary_artifact is None and summarization_service is not None:
        if log_artifact is not None:
            try:
                import json as _json

                log_text = Path(log_artifact.disk_path).read_text(encoding="utf-8")
                log_data = _json.loads(log_text)

                _parts: list[str] = []
                _counter = 0
                all_sessions = log_data.get("sessions", [])
                if not all_sessions and log_data.get("transcript_turns"):
                    all_sessions = [log_data]
                for sess in all_sessions:
                    sess_num = sess.get("session_number", "?")
                    _turns = sess.get("transcript_turns", [])
                    if len(all_sessions) > 1:
                        _counter += 1
                        _parts.append(f"=== Session {sess_num} ===")
                    for t in _turns:
                        role = t.get("role", "")
                        if role == "tool_call":
                            if t.get("tool_name") == "report_intent":
                                continue
                            _counter += 1
                            display = t.get("tool_display") or t.get("tool_intent") or t.get("tool_name", "tool")
                            ok = "\u2713" if t.get("tool_success", True) else "\u2717"
                            _parts.append(f"[{_counter}] TOOL {ok}: {display}")
                        else:
                            _counter += 1
                            _parts.append(f"[{_counter}] {role.upper()}: {t.get('content', '')}")
                transcript_text = "\n---\n".join(_parts) or "(no transcript)"
                log_changed = log_data.get("all_changed_files") or log_data.get("changed_files", [])
                if log_changed:
                    changed_files = log_changed
                await summarization_service.summarize_and_store(
                    job.id,
                    job.session_count,
                    job.prompt,
                    pre_built_transcript=transcript_text,
                    pre_built_changed_files=changed_files,
                )
                # summarize_and_store commits in its own inner session; the
                # outer session's WAL read snapshot predates that commit.
                async with session_factory() as fresh_session:
                    fresh_svc = ArtifactService(ArtifactRepository(fresh_session))
                    summary_artifact = await fresh_svc.get_latest_session_summary(job.id)
            except DBAPIError:
                log.warning("session_log_summarization_failed", job_id=job.id, exc_info=True)

        if summary_artifact is None:
            try:
                await summarization_service.summarize_and_store(job.id, job.session_count, job.prompt)
                async with session_factory() as fresh_session:
                    fresh_svc = ArtifactService(ArtifactRepository(fresh_session))
                    summary_artifact = await fresh_svc.get_latest_session_summary(job.id)
            except (DBAPIError, OSError):
                log.warning("inline_summarization_failed", job_id=job.id, exc_info=True)

    summary_text: str | None = None
    if summary_artifact is not None:
        try:
            summary_text = Path(summary_artifact.disk_path).read_text(encoding="utf-8")
        except OSError:
            log.warning("summary_read_failed", job_id=job.id, exc_info=True)

    return summary_text, changed_files


async def build_resume_handoff_prompt_for_job(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    job: Job,
    instruction: str,
    session_number: int,
    summarization_service: SummarizationService | None,
) -> str:
    """Build the resume handoff prompt for a job."""
    from backend.services.completers.summarization_service import build_resume_prompt

    summary_text, changed_files = await load_handoff_context_for_job(
        session, session_factory, job, summarization_service
    )
    return build_resume_prompt(summary_text, changed_files, instruction, session_number, job.id, job.prompt)


async def build_followup_handoff_prompt_for_job(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    job: Job,
    instruction: str,
    summarization_service: SummarizationService | None,
) -> str:
    """Build the follow-up handoff prompt for a job."""
    from backend.services.completers.summarization_service import build_followup_prompt

    summary_text, changed_files = await load_handoff_context_for_job(
        session, session_factory, job, summarization_service
    )
    return build_followup_prompt(summary_text, changed_files, instruction, job.id, job.prompt)
