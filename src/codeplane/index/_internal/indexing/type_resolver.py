"""Type-traced reference resolution (Pass 3).

Resolves member accesses by tracing through type annotations.
For example: ctx.mutation_ops.atomic_edit_files() -> MutationOps.atomic_edit_files def_uid.

This module runs AFTER Pass 2 (import resolution) and uses:
- TypeAnnotationFact: What type a variable/parameter has
- TypeMemberFact: What members a class/struct has
- MemberAccessFact: What member chains appear in code

The output is updated MemberAccessFact records with:
- resolved_type_path: e.g., "AppContext.MutationOps.atomic_edit_files"
- final_target_def_uid: The def_uid of the final member
- resolution_method: "type_traced"
- resolution_confidence: 1.0 (type-traced is authoritative)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlmodel import col, select

from codeplane.index.models import (
    MemberAccessFact,
    RefFact,
    RefTier,
    TypeAnnotationFact,
    TypeMemberFact,
)

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database


@dataclass
class TypeTracedStats:
    """Statistics from type-traced resolution."""

    accesses_processed: int = 0
    accesses_resolved: int = 0
    accesses_partial: int = 0  # Resolved partway through chain
    accesses_unresolved: int = 0
    refs_upgraded: int = 0  # RefFacts upgraded to PROVEN


class TypeTracedResolver:
    """Resolves member accesses by following type annotation chains.

    Resolution Algorithm:
    1. For each MemberAccessFact without final_target_def_uid:
       a. Look up receiver type from TypeAnnotationFact in scope
       b. Walk each member in the chain:
          - Look up TypeMemberFact for (parent_type, member_name)
          - Get the member's type for next iteration
       c. For the final member, get its def_uid
    2. Update the MemberAccessFact with resolution info
    3. Optionally upgrade corresponding RefFact to PROVEN

    Usage::

        resolver = TypeTracedResolver(db)
        stats = resolver.resolve_all()
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        # Cache: (name, scope_id) -> base_type
        self._type_map: dict[tuple[str, int | None], str] = {}
        # Cache: (parent_type, member_name) -> TypeMemberFact
        self._member_map: dict[tuple[str, str], TypeMemberFact] = {}

    def resolve_all(self, *, limit: int = 10000) -> TypeTracedStats:
        """Resolve all unresolved member accesses.

        Args:
            limit: Maximum accesses to process in one batch

        Returns:
            TypeTracedStats with resolution counts
        """
        stats = TypeTracedStats()

        with self._db.session() as session:
            # Find unresolved accesses (no final_target_def_uid)
            stmt = (
                select(MemberAccessFact)
                .where(MemberAccessFact.final_target_def_uid == None)  # noqa: E711
                .limit(limit)
            )
            unresolved = list(session.exec(stmt).all())
            stats.accesses_processed = len(unresolved)

            if not unresolved:
                return stats

            # Build caches
            self._build_type_cache(session)
            self._build_member_cache(session)

            # Resolve each access
            for access in unresolved:
                result = self._resolve_access(session, access)
                if result == "resolved":
                    stats.accesses_resolved += 1
                elif result == "partial":
                    stats.accesses_partial += 1
                else:
                    stats.accesses_unresolved += 1

            session.commit()

        return stats

    def resolve_for_files(self, file_ids: list[int]) -> TypeTracedStats:
        """Resolve accesses only for specific files.

        Use for incremental updates after re-indexing.
        """
        stats = TypeTracedStats()

        with self._db.session() as session:
            stmt = select(MemberAccessFact).where(
                col(MemberAccessFact.file_id).in_(file_ids),
                MemberAccessFact.final_target_def_uid == None,  # noqa: E711
            )
            unresolved = list(session.exec(stmt).all())
            stats.accesses_processed = len(unresolved)

            if not unresolved:
                return stats

            self._build_type_cache(session)
            self._build_member_cache(session)

            for access in unresolved:
                result = self._resolve_access(session, access)
                if result == "resolved":
                    stats.accesses_resolved += 1
                elif result == "partial":
                    stats.accesses_partial += 1
                else:
                    stats.accesses_unresolved += 1

            session.commit()

        return stats

    def _resolve_access(
        self, session: object, access: MemberAccessFact
    ) -> str:  # "resolved", "partial", "unresolved"
        """Attempt to resolve a single member access chain."""
        # Get receiver type
        receiver_type = access.receiver_declared_type
        if not receiver_type:
            # Try to look up from our cache
            receiver_type = self._type_map.get((access.receiver_name, access.scope_id))
            if not receiver_type:
                # Also try without scope (file-level)
                receiver_type = self._type_map.get((access.receiver_name, None))

        if not receiver_type:
            return "unresolved"

        # Walk the chain
        current_type = receiver_type
        chain_parts = access.member_chain.split(".")
        type_path = [receiver_type]
        resolved_depth = 0

        for i, member_name in enumerate(chain_parts):
            member_key = (current_type, member_name)
            member = self._member_map.get(member_key)

            if not member:
                # Can't continue - but we may have resolved part of the chain
                break

            resolved_depth = i + 1
            type_path.append(member_name)

            # Is this the final member?
            if i == len(chain_parts) - 1:
                # Success! Update the access
                access.resolved_type_path = ".".join(type_path)
                access.final_target_def_uid = member.member_def_uid
                access.resolution_method = "type_traced"
                access.resolution_confidence = 1.0

                # Upgrade the RefFact if we can find it
                self._upgrade_ref(
                    session,
                    access.file_id,
                    access.start_line,
                    member_name,
                    member.member_def_uid,
                )

                return "resolved"

            # Get type for next iteration
            if member.base_type:
                current_type = member.base_type
            elif member.member_kind in ("method", "static_method", "class_method"):
                # Methods don't have a "type" to continue through
                break
            else:
                # Field without type annotation - can't continue
                break

        # Partial resolution - record what we did resolve
        if resolved_depth > 0:
            access.resolved_type_path = ".".join(type_path[: resolved_depth + 1])
            access.resolution_method = "type_traced"
            access.resolution_confidence = resolved_depth / len(chain_parts)
            return "partial"

        return "unresolved"

    def _upgrade_ref(
        self,
        session: object,
        file_id: int,
        line: int,
        token: str,
        target_def_uid: str | None,
    ) -> bool:
        """Upgrade a RefFact to PROVEN based on type-traced resolution."""
        if not target_def_uid:
            return False

        stmt = select(RefFact).where(
            RefFact.file_id == file_id,
            RefFact.start_line == line,
            RefFact.token_text == token,
        )
        ref = session.exec(stmt).first()  # type: ignore[attr-defined]

        if ref:
            ref.target_def_uid = target_def_uid
            ref.ref_tier = RefTier.PROVEN.value
            return True

        return False

    def _build_type_cache(self, session: object) -> None:
        """Build (name, scope_id) -> base_type mapping."""
        self._type_map = {}

        stmt = select(TypeAnnotationFact)
        annotations = session.exec(stmt).all()  # type: ignore[attr-defined]

        for ann in annotations:
            # Add with scope
            self._type_map[(ann.target_name, ann.scope_id)] = ann.base_type
            # Also add without scope for fallback
            if ann.scope_id is not None:
                existing = self._type_map.get((ann.target_name, None))
                if not existing:
                    self._type_map[(ann.target_name, None)] = ann.base_type

    def _build_member_cache(self, session: object) -> None:
        """Build (parent_type, member_name) -> TypeMemberFact mapping."""
        self._member_map = {}

        stmt = select(TypeMemberFact)
        members = session.exec(stmt).all()  # type: ignore[attr-defined]

        for member in members:
            key = (member.parent_type_name, member.member_name)
            self._member_map[key] = member


def resolve_type_traced(db: Database, file_ids: list[int] | None = None) -> TypeTracedStats:
    """Convenience function to run type-traced resolution.

    Args:
        db: Database instance
        file_ids: Optional list of file IDs to resolve (None = all)

    Returns:
        TypeTracedStats
    """
    resolver = TypeTracedResolver(db)
    if file_ids:
        return resolver.resolve_for_files(file_ids)
    return resolver.resolve_all()
