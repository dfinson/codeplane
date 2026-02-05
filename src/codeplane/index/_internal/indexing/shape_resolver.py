"""Shape-based type inference (Pass 5).

Infers types for receivers based on their observed access patterns.
This is the fallback for dynamic languages without type annotations.

Algorithm:
1. For each ReceiverShapeFact (computed from MemberAccessFacts)
2. Build a "shape" = set of accessed members
3. Find TypeMemberFacts whose parent has matching members
4. Rank matches by overlap confidence
5. If high confidence, upgrade associated accesses

This runs AFTER Pass 3 (type-traced) as a fallback for unresolved accesses.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlmodel import col, select

from codeplane.index.models import (
    MemberAccessFact,
    ReceiverShapeFact,
    RefFact,
    RefTier,
    TypeMemberFact,
)

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database


@dataclass
class ShapeInferenceStats:
    """Statistics from shape-based inference."""

    shapes_processed: int = 0
    shapes_matched: int = 0
    shapes_ambiguous: int = 0  # Multiple high-confidence matches
    shapes_unmatched: int = 0
    accesses_upgraded: int = 0


@dataclass
class TypeMatch:
    """A candidate type match for a shape."""

    type_name: str
    confidence: float  # 0.0 - 1.0
    matched_members: list[str]
    unmatched_members: list[str]


class ShapeInferenceResolver:
    """Infers types from observed member access patterns.

    For receivers without type annotations, we can often infer the type
    by looking at what members are accessed:

    - If `obj` accesses `.mutation_ops`, `.refactor_ops`, `.git_ops`
    - And only `AppContext` has all three of those fields
    - Then `obj` is probably `AppContext` with high confidence

    Usage::

        resolver = ShapeInferenceResolver(db)
        stats = resolver.resolve_all()
    """

    # Minimum confidence threshold for upgrading refs
    CONFIDENCE_THRESHOLD = 0.7

    def __init__(self, db: Database) -> None:
        self._db = db
        # Cache: type_name -> set of member names
        self._type_shapes: dict[str, set[str]] = {}
        # Cache: type_name -> {member_name: def_uid}
        self._type_member_uids: dict[str, dict[str, str | None]] = {}

    def resolve_all(self, *, limit: int = 10000) -> ShapeInferenceStats:
        """Run shape inference on all unmatched shapes.

        Args:
            limit: Maximum shapes to process

        Returns:
            ShapeInferenceStats
        """
        stats = ShapeInferenceStats()

        with self._db.session() as session:
            # Find shapes without a confident match
            stmt = (
                select(ReceiverShapeFact)
                .where(
                    (ReceiverShapeFact.best_match_type == None)  # noqa: E711
                    | (
                        (ReceiverShapeFact.match_confidence != None)  # noqa: E711
                        & (ReceiverShapeFact.match_confidence < self.CONFIDENCE_THRESHOLD)  # type: ignore[operator]
                    )
                )
                .limit(limit)
            )
            shapes = list(session.exec(stmt).all())
            stats.shapes_processed = len(shapes)

            if not shapes:
                return stats

            # Build type shape cache
            self._build_type_shape_cache(session)

            # Match each shape
            for shape in shapes:
                result = self._match_shape(session, shape)
                if result == "matched":
                    stats.shapes_matched += 1
                elif result == "ambiguous":
                    stats.shapes_ambiguous += 1
                else:
                    stats.shapes_unmatched += 1

            session.commit()

        return stats

    def resolve_for_files(self, file_ids: list[int]) -> ShapeInferenceStats:
        """Run shape inference for specific files."""
        stats = ShapeInferenceStats()

        with self._db.session() as session:
            stmt = select(ReceiverShapeFact).where(
                col(ReceiverShapeFact.file_id).in_(file_ids),
                (ReceiverShapeFact.best_match_type == None)  # noqa: E711
                | (
                    (ReceiverShapeFact.match_confidence != None)  # noqa: E711
                    & (ReceiverShapeFact.match_confidence < self.CONFIDENCE_THRESHOLD)  # type: ignore[operator]
                ),
            )
            shapes = list(session.exec(stmt).all())
            stats.shapes_processed = len(shapes)

            if not shapes:
                return stats

            self._build_type_shape_cache(session)

            for shape in shapes:
                result = self._match_shape(session, shape)
                if result == "matched":
                    stats.shapes_matched += 1
                elif result == "ambiguous":
                    stats.shapes_ambiguous += 1
                else:
                    stats.shapes_unmatched += 1

            session.commit()

        return stats

    def _match_shape(
        self, session: object, shape: ReceiverShapeFact
    ) -> str:  # "matched", "ambiguous", "unmatched"
        """Match a shape against known types."""
        # Parse observed members
        observed = shape.get_observed_members()
        observed_set = set(observed.get("fields", [])) | set(observed.get("methods", []))

        if not observed_set:
            return "unmatched"

        # Find matching types
        matches: list[TypeMatch] = []

        for type_name, type_members in self._type_shapes.items():
            if not type_members:
                continue

            # Calculate overlap
            matched = observed_set & type_members
            unmatched = observed_set - type_members

            if not matched:
                continue

            # Confidence = what fraction of observed members exist in this type
            # Plus bonus for methods (stronger signal than fields)
            method_matches = set(observed.get("methods", [])) & type_members
            set(observed.get("fields", [])) & type_members

            base_confidence = len(matched) / len(observed_set)
            # Bonus: methods are stronger evidence
            method_bonus = 0.1 * len(method_matches) if method_matches else 0
            confidence = min(1.0, base_confidence + method_bonus)

            matches.append(
                TypeMatch(
                    type_name=type_name,
                    confidence=confidence,
                    matched_members=list(matched),
                    unmatched_members=list(unmatched),
                )
            )

        if not matches:
            return "unmatched"

        # Sort by confidence
        matches.sort(key=lambda m: -m.confidence)
        best = matches[0]

        # Check for ambiguity (multiple high-confidence matches)
        high_confidence_matches = [m for m in matches if m.confidence >= self.CONFIDENCE_THRESHOLD]
        if len(high_confidence_matches) > 1:
            # Multiple candidates - record all but mark ambiguous
            shape.matched_types_json = json.dumps(
                [
                    {"type": m.type_name, "confidence": m.confidence}
                    for m in matches[:5]  # Top 5
                ]
            )
            shape.best_match_type = best.type_name
            shape.match_confidence = best.confidence
            return "ambiguous"

        # Single good match
        if best.confidence >= self.CONFIDENCE_THRESHOLD:
            shape.matched_types_json = json.dumps(
                [{"type": best.type_name, "confidence": best.confidence}]
            )
            shape.best_match_type = best.type_name
            shape.match_confidence = best.confidence

            # Upgrade accesses for this receiver
            self._upgrade_accesses(
                session,
                shape.file_id,
                shape.receiver_name,
                shape.scope_id,
                best.type_name,
                best.confidence,
            )

            return "matched"

        # Low confidence - record but don't upgrade
        shape.matched_types_json = json.dumps(
            [{"type": m.type_name, "confidence": m.confidence} for m in matches[:3]]
        )
        if matches:
            shape.best_match_type = best.type_name
            shape.match_confidence = best.confidence

        return "unmatched"

    def _upgrade_accesses(
        self,
        session: object,
        file_id: int,
        receiver_name: str,
        scope_id: int | None,
        inferred_type: str,
        confidence: float,
    ) -> int:
        """Upgrade MemberAccessFacts for a receiver with inferred type."""
        upgraded = 0

        # Find accesses for this receiver
        stmt = select(MemberAccessFact).where(
            MemberAccessFact.file_id == file_id,
            MemberAccessFact.receiver_name == receiver_name,
            MemberAccessFact.final_target_def_uid == None,  # noqa: E711
        )
        if scope_id is not None:
            stmt = stmt.where(MemberAccessFact.scope_id == scope_id)

        accesses = session.exec(stmt).all()  # type: ignore[attr-defined]

        for access in accesses:
            # Try to resolve the chain with inferred type
            final_member = access.final_member
            member_uid = self._type_member_uids.get(inferred_type, {}).get(final_member)

            if member_uid:
                access.receiver_declared_type = inferred_type
                access.final_target_def_uid = member_uid
                access.resolution_method = "shape_matched"
                access.resolution_confidence = confidence

                # Also upgrade the RefFact
                self._upgrade_ref(
                    session,
                    file_id,
                    access.start_line,
                    final_member,
                    member_uid,
                    confidence,
                )

                upgraded += 1

        return upgraded

    def _upgrade_ref(
        self,
        session: object,
        file_id: int,
        line: int,
        token: str,
        target_def_uid: str,
        _confidence: float,
    ) -> None:
        """Upgrade a RefFact based on shape inference."""
        stmt = select(RefFact).where(
            RefFact.file_id == file_id,
            RefFact.start_line == line,
            RefFact.token_text == token,
        )
        ref = session.exec(stmt).first()  # type: ignore[attr-defined]

        if ref and not ref.target_def_uid:
            ref.target_def_uid = target_def_uid
            # ANCHORED for shape-matched (not as confident as PROVEN)
            ref.ref_tier = RefTier.ANCHORED.value

    def _build_type_shape_cache(self, session: object) -> None:
        """Build type -> member set mapping."""
        self._type_shapes = defaultdict(set)
        self._type_member_uids = defaultdict(dict)

        stmt = select(TypeMemberFact)
        members = session.exec(stmt).all()  # type: ignore[attr-defined]

        for member in members:
            type_name = member.parent_type_name
            self._type_shapes[type_name].add(member.member_name)
            self._type_member_uids[type_name][member.member_name] = member.member_def_uid


def resolve_shape_inference(db: Database, file_ids: list[int] | None = None) -> ShapeInferenceStats:
    """Convenience function to run shape inference.

    Args:
        db: Database instance
        file_ids: Optional list of file IDs to process (None = all)

    Returns:
        ShapeInferenceStats
    """
    resolver = ShapeInferenceResolver(db)
    if file_ids:
        return resolver.resolve_for_files(file_ids)
    return resolver.resolve_all()
