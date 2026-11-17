"""Service layer for step-level diff computation with motivation annotations.

Extracted from the ``get_step_diff`` API handler to keep route handlers thin.
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.api_schemas import (
    FileMotivation,
    HunkMotivation,
    StepDiffPayload,
)
from backend.models.events import EventKind
from backend.services.artifacts.diff_service import DiffService

if TYPE_CHECKING:
    from backend.models.domain import TelemetrySpanRow
    from backend.persistence.step_repo import StepRepository
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
    from backend.services.git.git_service import GitService
    from backend.services.job.job_service import JobService

log = structlog.get_logger()

# Event query ceiling — plan/step events use a higher limit because each
# event is small and completeness matters for SHA resolution.
_EVENT_QUERY_CEILING = 5000


class StepDiffService:
    """Resolves step SHAs and computes annotated diffs."""

    def __init__(
        self,
        job_svc: JobService,
        step_repo: StepRepository,
        git_service: GitService,
        spans_repo: TelemetrySpansRepository,
    ) -> None:
        self._job_svc = job_svc
        self._step_repo = step_repo
        self._git_service = git_service
        self._spans_repo = spans_repo

    async def get_step_diff(self, job_id: str, step_id: str) -> StepDiffPayload:
        """Compute the Git diff for a specific step with motivation annotations."""
        start_sha, end_sha, step_row = await self._resolve_shas(job_id, step_id)

        if not start_sha or not end_sha or start_sha == end_sha:
            return StepDiffPayload(step_id=step_id, diff="", files_changed=0)

        job = await self._job_svc.get_job(job_id)
        if not job.worktree_path:
            return StepDiffPayload(step_id=step_id, diff="", files_changed=0)

        diff_text = await self._git_service.diff_range(start_sha, end_sha, cwd=job.worktree_path)
        files_changed = diff_text.count("\ndiff --git ") + (1 if diff_text.startswith("diff --git ") else 0)
        changed_files = DiffService._parse_unified_diff(diff_text)

        step_context, file_motivations, hunk_motivations = await self._build_motivations(
            job_id,
            step_id,
            step_row,
            changed_files,
            worktree_path=job.worktree_path,
        )

        return StepDiffPayload(
            step_id=step_id,
            diff=diff_text,
            files_changed=files_changed,
            changed_files=changed_files,
            step_context=step_context,
            file_motivations=file_motivations,
            hunk_motivations=hunk_motivations,
        )

    async def _resolve_shas(
        self,
        job_id: str,
        step_id: str,
    ) -> tuple[str | None, str | None, Any]:
        """Resolve start/end SHAs for a step via events, then StepRow fallbacks."""
        start_sha: str | None = None
        end_sha: str | None = None
        step_row = None

        # Try plan_step_updated events first (plan step IDs like ps-XXXX)
        events = await self._job_svc.list_events_by_job(
            job_id,
            [EventKind.plan_step_updated],
            limit=_EVENT_QUERY_CEILING,
        )
        for ev in events:
            if ev.payload.get("plan_step_id") == step_id:
                start = ev.payload.get("start_sha")
                end = ev.payload.get("end_sha")
                if start:
                    start_sha = str(start)
                if end:
                    end_sha = str(end)

        # Fallback: try StepRow table (internal step IDs like step-XXXX)
        if not start_sha or not end_sha:
            step_row = await self._step_repo.get(step_id)
            if step_row and step_row.start_sha and step_row.end_sha:
                start_sha = str(step_row.start_sha)
                end_sha = str(step_row.end_sha)

        # Fallback 2: try StepRow by turn_id (frontend passes turnId from transcript)
        if not start_sha or not end_sha:
            step_row = await self._step_repo.get_by_turn_id(job_id, step_id)
            if step_row and step_row.start_sha and step_row.end_sha:
                start_sha = str(step_row.start_sha)
                end_sha = str(step_row.end_sha)

        return start_sha, end_sha, step_row

    async def _build_motivations(
        self,
        job_id: str,
        step_id: str,
        step_row: Any,
        changed_files: list[Any],
        *,
        worktree_path: str | None = None,
    ) -> tuple[str | None, dict[str, FileMotivation], dict[str, HunkMotivation]]:
        """Build motivation annotations from telemetry spans."""
        step_context: str | None = None
        file_motivations: dict[str, FileMotivation] = {}
        hunk_motivations: dict[str, HunkMotivation] = {}

        # Telemetry stores absolute paths; diffs use repo-relative paths.
        wt_prefix = ((worktree_path or "") + "/").replace("//", "/")

        def _rel(path: str) -> str:
            """Normalize an absolute tool_target to a repo-relative path."""
            return path[len(wt_prefix) :] if wt_prefix != "/" and path.startswith(wt_prefix) else path

        if step_row and hasattr(step_row, "preceding_context") and step_row.preceding_context:
            step_context = str(step_row.preceding_context)

        # Find the turn_id to look up telemetry spans
        turn_id_for_lookup = step_id
        if step_row and hasattr(step_row, "turn_id") and step_row.turn_id:
            turn_id_for_lookup = str(step_row.turn_id)

        try:
            spans = await self._spans_repo.file_write_spans_for_step(
                job_id=job_id,
                turn_id=turn_id_for_lookup,
            )
            if not spans:
                all_spans = await self._spans_repo.motivated_spans_for_job(job_id=job_id)
                changed_paths = {f.path for f in changed_files}
                spans = [s for s in all_spans if _rel(s.get("tool_target") or "") in changed_paths]

            for span in spans:
                target = _rel(span.get("tool_target") or "")
                summary = span.get("motivation_summary")
                if not target or not summary:
                    continue
                lines = summary.strip().split("\n", 1)
                title = lines[0].strip()
                why = lines[1].strip() if len(lines) > 1 else ""
                file_motivations[target] = FileMotivation(title=title, why=why)

                self._extract_hunk_motivations(
                    span,
                    target,
                    changed_files,
                    file_motivations,
                    hunk_motivations,
                    job_id,
                )
        except (KeyError, ValueError, IndexError, TypeError):
            log.debug("motivation_annotation_failed", job_id=job_id, step_id=step_id, exc_info=True)

        return step_context, file_motivations, hunk_motivations

    @staticmethod
    def _extract_hunk_motivations(
        span: TelemetrySpanRow,
        target: str,
        changed_files: list[Any],
        file_motivations: dict[str, FileMotivation],
        hunk_motivations: dict[str, HunkMotivation],
        job_id: str,
    ) -> None:
        """Extract hunk-level motivations from a span's edit_motivations."""
        edit_mots_raw = span.get("edit_motivations")
        if not edit_mots_raw:
            return
        try:
            edit_mots = json.loads(edit_mots_raw) if isinstance(edit_mots_raw, str) else edit_mots_raw
        except (json.JSONDecodeError, TypeError):
            log.debug("edit_motivation_parse_failed", job_id=job_id, exc_info=True)
            return
        if not isinstance(edit_mots, list) or not edit_mots:
            return

        tool_args_raw = span.get("tool_args_json")
        parsed_args: dict[str, Any] = {}
        if tool_args_raw:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                parsed_args = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw

        old_str = str(
            parsed_args.get("old_str", "")
            or parsed_args.get("oldString", "")
            or parsed_args.get("old_string", "")
            or ""
        )

        matched_hunk_idx: int | None = None
        for cf in changed_files:
            if cf.path != target:
                continue
            if old_str and len(cf.hunks) > 1:
                old_lines = [ln.strip() for ln in old_str.strip().splitlines() if ln.strip()]
                if old_lines:
                    best_idx, best_ratio = 0, 0.0
                    for hi, hunk in enumerate(cf.hunks):
                        del_content = " ".join(ln.content.strip() for ln in hunk.lines if ln.type == "deletion")
                        hits = sum(1 for ol in old_lines if ol in del_content)
                        ratio = hits / len(old_lines)
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best_idx = hi
                    if best_ratio >= 0.5:
                        matched_hunk_idx = best_idx
            elif len(cf.hunks) == 1:
                matched_hunk_idx = 0
            break

        em = edit_mots[0]
        em_summary = em.get("summary", "")
        em_lines = em_summary.strip().split("\n", 1)
        em_title = em_lines[0].strip()
        em_why = em_lines[1].strip() if len(em_lines) > 1 else ""
        edit_key = em.get("edit_key", "")

        if matched_hunk_idx is not None:
            hunk_motivations[f"{target}:{matched_hunk_idx}"] = HunkMotivation(
                edit_key=edit_key,
                title=em_title,
                why=em_why,
            )
        else:
            if target in file_motivations:
                file_motivations[target].unmatched_edits.append(
                    HunkMotivation(edit_key=edit_key, title=em_title, why=em_why),
                )
