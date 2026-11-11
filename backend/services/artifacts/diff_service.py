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
from backend.models.events import EventKind, new_event
from backend.services.git.git_service import GitError

if TYPE_CHECKING:
    from backend.services.events.event_bus import EventBus
    from backend.services.git.git_service import GitService

log = structlog.get_logger()

# Per-job throttle window in seconds
_THROTTLE_WINDOW_S = 5.0

# Maximum hunk content size (bytes) before a file's hunks are truncated from the list response
DIFF_TRUNCATION_THRESHOLD_BYTES = 20_000

# File patterns that are always truncated regardless of size (generated/binary artifacts)
_GENERATED_FILE_PATTERNS = re.compile(
    r"(\.lock$|[\-_]lock\.json$|\.tsbuildinfo$|\.min\.js$|\.min\.css$)",
    re.IGNORECASE,
)

# Regex patterns for unified diff parsing
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NEW_FILE_RE = re.compile(r"^new file mode")
_DELETED_FILE_RE = re.compile(r"^deleted file mode")
_RENAME_FROM_RE = re.compile(r"^rename from (.+)$")
_RENAME_TO_RE = re.compile(r"^rename to (.+)$")
_SIMILARITY_RE = re.compile(r"^similarity index")


# Worktree path marker for deriving repo root
_WORKTREE_MARKER = "/.codeplane-worktrees/"




class DiffService:
    """Generates and parses unified diffs from git worktrees."""

    def __init__(self, git_service: GitService, event_bus: EventBus) -> None:
        self._git = git_service
        self._event_bus = event_bus
        # Monotonic timestamps of last diff calculation per job
        self._last_diff_at: dict[str, float] = {}
        # Per-job locks to prevent concurrent diff calculations
        self._locks: dict[str, asyncio.Lock] = {}

    async def on_worktree_file_modified(
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

    @staticmethod
    def truncate_large_files(files: list[DiffFileModel]) -> list[DiffFileModel]:
        """Replace hunks with empty list for files exceeding the size threshold.

        Files matching generated-file patterns are always truncated.
        Sets truncated=True and raw_size to the original hunk content size.
        """
        for f in files:
            hunk_size = sum(len(line.content) for h in f.hunks for line in h.lines)
            is_generated = bool(_GENERATED_FILE_PATTERNS.search(f.path))
            if is_generated or hunk_size > DIFF_TRUNCATION_THRESHOLD_BYTES:
                f.truncated = True
                f.raw_size = hunk_size
                f.hunks = []
        return files

    async def calculate_diff_single_file(
        self,
        worktree_path: str,
        base_ref: str,
        file_path: str,
    ) -> DiffFileModel | None:
        """Calculate the diff for a single file and return it with full hunks."""
        files = await self.calculate_diff(worktree_path, base_ref)
        for f in files:
            if f.path == file_path:
                return f
        return None

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

        When a merge is currently in progress (MERGE_HEAD exists), the
        working tree contains the merged-in content from the other branch
        before the merge commit has been created.  In that window a
        working-tree diff would include all of the merged branch's unrelated
        changes.  We detect this state and diff against HEAD (committed
        state only) so only the job's own committed changes are shown.
        """
        try:
            # When a merge is in-progress the working tree holds the merged
            # content from the other branch.  Avoid polluting the diff with
            # those unrelated changes by comparing committed state only.
            merge_in_progress = await self._git.is_merge_in_progress(cwd=worktree_path)
            if not merge_in_progress:
                # Mark untracked files so they appear in the diff output.
                await self._git.add_intent_to_add(cwd=worktree_path)
            # Resolve merge-base so we only show branch-own changes,
            # not divergence on the base branch.
            try:
                effective_base = await self._git.merge_base(base_ref, "HEAD", cwd=worktree_path)
            except GitError:
                log.debug("merge_base_fallback", worktree=worktree_path, base_ref=base_ref, exc_info=True)
                effective_base = base_ref  # fallback to two-dot if merge-base fails
            if merge_in_progress:
                log.debug("diff_merge_in_progress", worktree=worktree_path, base_ref=base_ref)
                raw = await self._git.diff_range(effective_base, "HEAD", cwd=worktree_path)
            else:
                raw = await self._git.diff(
                    effective_base,
                    cwd=worktree_path,
                )
        except GitError as exc:
            if "does not exist" in str(exc) or "not a git repository" in str(exc).lower():
                log.info("diff_skipped_missing_worktree", worktree=worktree_path, base_ref=base_ref)
                return []
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
        """Calculate diff, publish event, update throttle."""
        files = await self.calculate_diff(worktree_path, base_ref)
        self._last_diff_at[job_id] = time.monotonic()


        # Use snake_case keys for internal domain event payload;
        # SSE manager re-serializes to camelCase for the wire.
        payload = DiffUpdatePayload(job_id=job_id, changed_files=files)
        await self._event_bus.publish(
            new_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.diff_updated,
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
