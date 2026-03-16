"""Diff generation and parsing."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.models.api_schemas import (
    DiffFileModel,
    DiffFileStatus,
    DiffHunkModel,
    DiffLineModel,
    DiffLineType,
    DiffUpdatePayload,
)
from backend.models.events import DomainEvent, DomainEventKind

if TYPE_CHECKING:
    from backend.services.event_bus import EventBus
    from backend.services.git_service import GitService

log = structlog.get_logger()

# Per-job throttle window in seconds
_THROTTLE_WINDOW_S = 5.0

# Regex patterns for unified diff parsing
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NEW_FILE_RE = re.compile(r"^new file mode")
_DELETED_FILE_RE = re.compile(r"^deleted file mode")
_RENAME_FROM_RE = re.compile(r"^rename from (.+)$")
_RENAME_TO_RE = re.compile(r"^rename to (.+)$")
_SIMILARITY_RE = re.compile(r"^similarity index")


class DiffService:
    """Generates and parses unified diffs from git worktrees."""

    def __init__(self, git_service: GitService, event_bus: EventBus) -> None:
        self._git = git_service
        self._event_bus = event_bus
        # Monotonic timestamps of last diff calculation per job
        self._last_diff_at: dict[str, float] = {}
        # Per-job locks to prevent concurrent diff calculations
        self._locks: dict[str, asyncio.Lock] = {}

    async def handle_file_changed(
        self,
        job_id: str,
        worktree_path: str,
        base_ref: str,
    ) -> None:
        """Called when the agent writes a file. Throttled to 5-second windows."""
        lock = self._locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last = self._last_diff_at.get(job_id, 0.0)
            if now - last < _THROTTLE_WINDOW_S:
                return
            await self._calculate_and_publish(job_id, worktree_path, base_ref)

    async def finalize(
        self,
        job_id: str,
        worktree_path: str,
        base_ref: str,
    ) -> list[DiffFileModel]:
        """Calculate the final diff at job completion. Always runs (ignores throttle)."""
        files = await self._calculate_and_publish(job_id, worktree_path, base_ref)
        self._last_diff_at.pop(job_id, None)
        return files

    def cleanup(self, job_id: str) -> None:
        """Remove throttle tracking for a completed/failed job."""
        self._last_diff_at.pop(job_id, None)
        self._locks.pop(job_id, None)

    async def calculate_diff(
        self,
        worktree_path: str,
        base_ref: str,
    ) -> list[DiffFileModel]:
        """Run git diff and parse the output into structured models.

        Uses a three-dot style diff (merge-base of base_ref and HEAD vs
        working tree) so only the branch's own changes are shown, not
        unrelated commits added to base_ref after the branch diverged.
        Untracked new files are surfaced via ``git add -N``
        (intent-to-add) before diffing.
        """
        try:
            # Mark untracked files so they appear in the diff output.
            await self._git.add_intent_to_add(cwd=worktree_path)
            # Resolve merge-base so we only show branch-own changes,
            # not divergence on the base branch.
            try:
                effective_base = await self._git.merge_base(base_ref, "HEAD", cwd=worktree_path)
            except Exception:
                effective_base = base_ref  # fallback to two-dot if merge-base fails
            raw = await self._git.diff(
                effective_base,
                cwd=worktree_path,
            )
        except Exception:
            log.warning("diff_git_failed", worktree=worktree_path, base_ref=base_ref, exc_info=True)
            return []
        if not raw.strip():
            return []
        return self._parse_unified_diff(raw)

    async def _calculate_and_publish(
        self,
        job_id: str,
        worktree_path: str,
        base_ref: str,
    ) -> list[DiffFileModel]:
        """Calculate diff, publish event, update throttle timestamp."""
        files = await self.calculate_diff(worktree_path, base_ref)
        self._last_diff_at[job_id] = time.monotonic()
        # Use snake_case keys for internal domain event payload;
        # SSE manager re-serializes to camelCase for the wire.
        payload = DiffUpdatePayload(job_id=job_id, changed_files=files)
        await self._event_bus.publish(
            DomainEvent(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.diff_updated,
                payload=json.loads(payload.model_dump_json()),
            )
        )
        return files

    @staticmethod
    def _parse_unified_diff(raw: str) -> list[DiffFileModel]:
        """Parse a unified diff string into a list of DiffFileModel."""
        files: list[DiffFileModel] = []
        lines = raw.split("\n")
        i = 0

        while i < len(lines):
            header_match = _DIFF_HEADER_RE.match(lines[i])
            if not header_match:
                i += 1
                continue

            old_path = header_match.group(1)
            new_path = header_match.group(2)
            status = DiffFileStatus.modified
            i += 1

            # Parse extended headers
            while i < len(lines) and not lines[i].startswith("@@") and not _DIFF_HEADER_RE.match(lines[i]):
                if _NEW_FILE_RE.match(lines[i]):
                    status = DiffFileStatus.added
                elif _DELETED_FILE_RE.match(lines[i]):
                    status = DiffFileStatus.deleted
                elif _SIMILARITY_RE.match(lines[i]):
                    status = DiffFileStatus.renamed
                i += 1

            # Parse hunks
            hunks: list[DiffHunkModel] = []
            total_additions = 0
            total_deletions = 0

            while i < len(lines) and not _DIFF_HEADER_RE.match(lines[i]):
                hunk_match = _HUNK_HEADER_RE.match(lines[i])
                if hunk_match:
                    old_start = int(hunk_match.group(1))
                    old_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
                    new_start = int(hunk_match.group(3))
                    new_count = int(hunk_match.group(4)) if hunk_match.group(4) else 1
                    i += 1

                    hunk_lines: list[DiffLineModel] = []
                    while (
                        i < len(lines) and not _HUNK_HEADER_RE.match(lines[i]) and not _DIFF_HEADER_RE.match(lines[i])
                    ):
                        line = lines[i]
                        if line.startswith("+"):
                            hunk_lines.append(DiffLineModel(type=DiffLineType.addition, content=line[1:]))
                            total_additions += 1
                        elif line.startswith("-"):
                            hunk_lines.append(DiffLineModel(type=DiffLineType.deletion, content=line[1:]))
                            total_deletions += 1
                        elif line.startswith(" "):
                            hunk_lines.append(DiffLineModel(type=DiffLineType.context, content=line[1:]))
                        elif line == "\\ No newline at end of file":
                            pass  # skip
                        else:
                            # Unknown line in hunk – skip
                            pass
                        i += 1

                    hunks.append(
                        DiffHunkModel(
                            old_start=old_start,
                            old_lines=old_count,
                            new_start=new_start,
                            new_lines=new_count,
                            lines=hunk_lines,
                        )
                    )
                else:
                    i += 1

            path = new_path if status != DiffFileStatus.deleted else old_path
            files.append(
                DiffFileModel(
                    path=path,
                    status=status,
                    additions=total_additions,
                    deletions=total_deletions,
                    hunks=hunks,
                )
            )

        return files
