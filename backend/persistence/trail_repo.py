"""Trail node persistence — CRUD for agent audit trail records."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import CursorResult, func, select, update

from backend.models.db import TrailNodeRow
from backend.persistence.database import serialized_write

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class TrailNodeRepository:
    """Persistence for trail nodes.

    Uses session_factory (not BaseRepository) for independent session-per-
    operation semantics — each write commits immediately for fire-and-forget
    audit trail persistence.

    All writes go through the global write lock (``serialized_write``) to
    avoid SQLite lock contention with other concurrent writers.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, node: TrailNodeRow) -> None:
        async with serialized_write(self._session_factory) as session:
            session.add(node)

    async def create_many(self, nodes: list[TrailNodeRow]) -> None:
        async with serialized_write(self._session_factory) as session:
            session.add_all(nodes)

    async def get(self, node_id: str) -> TrailNodeRow | None:
        async with self._session_factory() as session:
            result = await session.execute(select(TrailNodeRow).where(TrailNodeRow.id == node_id))
            return result.scalar_one_or_none()

    async def get_by_job(
        self,
        job_id: str,
        *,
        kinds: list[str] | None = None,
        after_seq: int | None = None,
        limit: int | None = None,
    ) -> list[TrailNodeRow]:
        """Fetch trail nodes for a job in display order (anchor_seq, seq)."""
        async with self._session_factory() as session:
            stmt = select(TrailNodeRow).where(TrailNodeRow.job_id == job_id)
            if kinds:
                stmt = stmt.where(TrailNodeRow.kind.in_(kinds))
            if after_seq is not None:
                stmt = stmt.where(TrailNodeRow.seq > after_seq)
            stmt = stmt.order_by(TrailNodeRow.anchor_seq, TrailNodeRow.seq)
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_pending_enrichment(
        self,
        job_id: str | None = None,
        *,
        limit: int,
    ) -> list[TrailNodeRow]:
        """Fetch nodes needing enrichment, oldest first."""
        async with self._session_factory() as session:
            stmt = select(TrailNodeRow).where(TrailNodeRow.enrichment == "pending")
            if job_id:
                stmt = stmt.where(TrailNodeRow.job_id == job_id)
            stmt = stmt.order_by(TrailNodeRow.seq).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_enrichment(
        self,
        node_id: str,
        *,
        kind: str | None = None,
        intent: str | None = None,
        rationale: str | None = None,
        outcome: str | None = None,
        tags: list[str] | None = None,
        supersedes: str | None = None,
        files: list[str] | None = None,
        purpose: str | None = None,
        enrichment: str = "complete",
    ) -> None:
        """Update a node with enrichment results."""
        async with serialized_write(self._session_factory) as session:
            values: dict[str, object] = {"enrichment": enrichment}
            if kind is not None:
                values["kind"] = kind
            if intent is not None:
                values["intent"] = intent
            if rationale is not None:
                values["rationale"] = rationale
            if outcome is not None:
                values["outcome"] = outcome
            if tags is not None:
                values["tags"] = json.dumps(tags, ensure_ascii=False)
            if supersedes is not None:
                values["supersedes"] = supersedes
            if files is not None:
                values["files"] = json.dumps(files, ensure_ascii=False)
            if purpose is not None:
                valid_purposes = {"building", "recovering", "orienting", "verifying", "housekeeping"}
                if purpose in valid_purposes:
                    values["purpose"] = purpose
                    values["purpose_source"] = "enrich"
            stmt = update(TrailNodeRow).where(TrailNodeRow.id == node_id).values(**values)
            await session.execute(stmt)

    async def max_seq(self, job_id: str) -> int:
        """Return the highest seq for a job, or 0 if no nodes exist."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.coalesce(func.max(TrailNodeRow.seq), 0)).where(TrailNodeRow.job_id == job_id)
            )
            return result.scalar_one()

    async def get_recent_decisions(
        self,
        job_id: str,
        *,
        limit: int,
    ) -> list[TrailNodeRow]:
        """Fetch the most recent decide nodes for supersedes linking."""
        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.job_id == job_id)
                .where(TrailNodeRow.kind == "decide")
                .order_by(TrailNodeRow.seq.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def count_by_job(self, job_id: str) -> tuple[int, int]:
        """Return (total_nodes, enriched_nodes) counts for a job."""
        async with self._session_factory() as session:
            total = await session.execute(
                select(func.count()).select_from(TrailNodeRow).where(TrailNodeRow.job_id == job_id)
            )
            enriched = await session.execute(
                select(func.count())
                .select_from(TrailNodeRow)
                .where(
                    TrailNodeRow.job_id == job_id,
                    TrailNodeRow.enrichment == "complete",
                )
            )
            return total.scalar_one(), enriched.scalar_one()

    async def get_untitled_work_nodes(self, *, limit: int) -> list[TrailNodeRow]:
        """Fetch work nodes that have a turn_id but no title (need title recovery)."""
        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.turn_id.isnot(None))
                .where(TrailNodeRow.title.is_(None))
                .where(TrailNodeRow.kind.in_(["shell", "modify", "explore"]))
                .order_by(TrailNodeRow.seq)
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Projection methods for downstream consumer migration
    # ------------------------------------------------------------------

    async def get_transcript_nodes(
        self,
        job_id: str,
        *,
        limit: int | None = None,
    ) -> list[TrailNodeRow]:
        """Fetch trail nodes carrying transcript content for a job.

        Returns nodes that have conversational content:
        - Step nodes (modify/shell/explore) with agent_message set
        - Request nodes with intent set (operator approval interactions)

        Note: General operator chat messages (transcript_updated with
        role=operator) are not yet captured as trail nodes. Only operator
        approval requests appear here. Full operator transcript migration
        requires TrailNodeBuilder to subscribe to transcript_updated events.
        """
        from sqlalchemy import and_, or_

        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.job_id == job_id)
                .where(
                    or_(
                        TrailNodeRow.agent_message.isnot(None),
                        # Request nodes carry operator context in intent
                        and_(
                            TrailNodeRow.kind == "request",
                            TrailNodeRow.intent.isnot(None),
                        ),
                    )
                )
                .order_by(TrailNodeRow.anchor_seq, TrailNodeRow.seq)
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_file_changes_by_step(
        self,
        job_id: str,
    ) -> list[TrailNodeRow]:
        """Fetch step nodes that carry file manifests, ordered chronologically."""
        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.job_id == job_id)
                .where(TrailNodeRow.kind.in_(["modify", "shell", "explore"]))
                .where(TrailNodeRow.files.isnot(None))
                .order_by(TrailNodeRow.anchor_seq, TrailNodeRow.seq)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_latest_step_boundary(
        self,
        job_id: str,
    ) -> TrailNodeRow | None:
        """Fetch the most recent step node with file information for a job."""
        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.job_id == job_id)
                .where(TrailNodeRow.kind.in_(["modify", "shell", "explore"]))
                .where(TrailNodeRow.files.isnot(None))
                .order_by(TrailNodeRow.seq.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_all_changed_files(self, job_id: str) -> list[str]:
        """Return sorted unique file paths changed across all steps in a job."""
        step_nodes = await self.get_file_changes_by_step(job_id)
        paths: set[str] = set()
        for node in step_nodes:
            if node.files:
                for path in json.loads(node.files):
                    if isinstance(path, str):
                        paths.add(path)
                    elif isinstance(path, dict):
                        p = path.get("path", "")
                        if p:
                            paths.add(p)
        return sorted(paths)

    async def get_diff_line_counts(self, job_id: str) -> tuple[int, int]:
        """Return (additions, deletions) summed across all trail nodes for a job."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    func.coalesce(func.sum(TrailNodeRow.diff_additions), 0),
                    func.coalesce(func.sum(TrailNodeRow.diff_deletions), 0),
                ).where(TrailNodeRow.job_id == job_id)
            )
            row = result.one()
            return int(row[0]), int(row[1])

    async def get_write_nodes_for_step(
        self,
        job_id: str,
        turn_id: str,
    ) -> list[TrailNodeRow]:
        """Fetch write sub-nodes for a specific step (by turn_id)."""
        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.job_id == job_id)
                .where(TrailNodeRow.turn_id == turn_id)
                .where(TrailNodeRow.kind == "write")
                .order_by(TrailNodeRow.seq)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_write_nodes_for_job(
        self,
        job_id: str,
    ) -> list[TrailNodeRow]:
        """Fetch all write sub-nodes for a job, ordered chronologically."""
        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.job_id == job_id)
                .where(TrailNodeRow.kind == "write")
                .order_by(TrailNodeRow.anchor_seq, TrailNodeRow.seq)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_unsummarized_write_nodes(
        self,
        *,
        limit: int,
    ) -> list[TrailNodeRow]:
        """Fetch write sub-nodes with no write_summary yet (§13.2 motivation pass 1)."""
        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.kind == "write")
                .where(TrailNodeRow.write_summary.is_(None))
                .order_by(TrailNodeRow.timestamp)
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_unenriched_edit_write_nodes(
        self,
        *,
        limit: int,
    ) -> list[TrailNodeRow]:
        """Fetch write sub-nodes that have write_summary but no edit_motivations (§13.2 pass 2)."""
        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.kind == "write")
                .where(TrailNodeRow.write_summary.isnot(None))
                .where(TrailNodeRow.edit_motivations.is_(None))
                .order_by(TrailNodeRow.timestamp)
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def set_write_summary(self, node_id: str, summary: str) -> None:
        """Set the write_summary on a write sub-node."""
        from backend.persistence.database import serialized_write

        async with serialized_write(self._session_factory) as session:
            stmt = update(TrailNodeRow).where(TrailNodeRow.id == node_id).values(write_summary=summary)
            await session.execute(stmt)

    async def set_edit_motivations(self, node_id: str, motivations_json: str) -> None:
        """Set the edit_motivations JSON on a write sub-node."""
        from backend.persistence.database import serialized_write

        async with serialized_write(self._session_factory) as session:
            stmt = update(TrailNodeRow).where(TrailNodeRow.id == node_id).values(edit_motivations=motivations_json)
            await session.execute(stmt)




    async def update_tool_metadata(
        self,
        job_id: str,
        turn_id: str,
        tool_name: str,
        *,
        tool_display: str | None = None,
        tool_intent: str | None = None,
        tool_success: bool | None = None,
    ) -> bool:
        """Update tool_display/tool_intent/tool_success on a write sub-node.

        Matches by job_id + turn_id + tool_name. Returns True if a row was
        updated, False if no matching node was found.
        """
        values: dict[str, object] = {}
        if tool_display is not None:
            values["tool_display"] = tool_display
        if tool_intent is not None:
            values["tool_intent"] = tool_intent
        if tool_success is not None:
            values["tool_success"] = tool_success
        if not values:
            return False

        from backend.persistence.database import serialized_write

        async with serialized_write(self._session_factory) as session:
            stmt = (
                update(TrailNodeRow)
                .where(TrailNodeRow.job_id == job_id)
                .where(TrailNodeRow.turn_id == turn_id)
                .where(TrailNodeRow.tool_name == tool_name)
                .where(TrailNodeRow.kind == "write")
                .where(TrailNodeRow.tool_display.is_(None))
                .values(**values)
            )
            result = cast("CursorResult[Any]", await session.execute(stmt))
            return (result.rowcount or 0) > 0

    async def get_snapshot_turns(self, job_id: str) -> list[TrailNodeRow]:
        """Fetch trail nodes needed for session snapshot reconstruction.

        Returns nodes carrying transcript-relevant data in chronological
        order:
        - Step nodes (modify/shell/explore) with agent_message (assistant turns)
        - Request nodes (operator turns)
        - Write sub-nodes with tool_display populated (tool_call turns)

        Ordered by (anchor_seq, seq) to maintain correct timeline.
        """
        from sqlalchemy import and_, or_

        async with self._session_factory() as session:
            stmt = (
                select(TrailNodeRow)
                .where(TrailNodeRow.job_id == job_id)
                .where(
                    or_(
                        # Assistant turns: step nodes with agent_message
                        and_(
                            TrailNodeRow.kind.in_(["modify", "shell", "explore"]),
                            TrailNodeRow.agent_message.isnot(None),
                        ),
                        # Operator turns: request nodes
                        TrailNodeRow.kind == "request",
                        # Tool call turns: write sub-nodes with tool metadata
                        and_(
                            TrailNodeRow.kind == "write",
                            TrailNodeRow.tool_display.isnot(None),
                        ),
                    )
                )
                .order_by(TrailNodeRow.anchor_seq, TrailNodeRow.seq)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
