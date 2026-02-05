"""Bounded fact query operations for Tier 1 index.

This module provides BOUNDED queries over fact tables. All queries require limits.
No semantic resolution, no call graph, no transitive closure.

See SPEC.md §7.8 for the bounded query API contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from codeplane.index.models import (
    AnchorGroup,
    DefFact,
    ExportEntry,
    ExportSurface,
    File,
    ImportFact,
    LocalBindFact,
    RefFact,
    RefTier,
    ScopeFact,
)

if TYPE_CHECKING:
    from sqlmodel import Session


class FactQueries:
    """Bounded fact queries for the Tier 1 index.

    All queries require explicit limits. No unbounded returns.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # -------------------------------------------------------------------------
    # Definition lookups
    # -------------------------------------------------------------------------

    def get_def(self, def_uid: str) -> DefFact | None:
        """Get a definition by its stable UID."""
        return self._session.get(DefFact, def_uid)

    def list_defs_by_name(self, unit_id: int, name: str, *, limit: int = 100) -> list[DefFact]:
        """List definitions by simple name within a build unit."""
        stmt = select(DefFact).where(DefFact.unit_id == unit_id, DefFact.name == name).limit(limit)
        return list(self._session.exec(stmt).all())

    def list_defs_in_file(self, file_id: int, *, limit: int = 1000) -> list[DefFact]:
        """List all definitions in a file."""
        stmt = select(DefFact).where(DefFact.file_id == file_id).limit(limit)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Reference lookups
    # -------------------------------------------------------------------------

    def list_refs_by_def_uid(
        self, def_uid: str, *, tier: RefTier | None = None, limit: int = 100
    ) -> list[RefFact]:
        """List references to a definition."""
        stmt = select(RefFact).where(RefFact.target_def_uid == def_uid)
        if tier is not None:
            stmt = stmt.where(RefFact.ref_tier == tier.value)
        stmt = stmt.limit(limit)
        return list(self._session.exec(stmt).all())

    def list_proven_refs(self, def_uid: str, *, limit: int = 100) -> list[RefFact]:
        """List PROVEN references to a definition (convenience)."""
        return self.list_refs_by_def_uid(def_uid, tier=RefTier.PROVEN, limit=limit)

    def list_refs_in_file(self, file_id: int, *, limit: int = 1000) -> list[RefFact]:
        """List all references in a file."""
        stmt = select(RefFact).where(RefFact.file_id == file_id).limit(limit)
        return list(self._session.exec(stmt).all())

    def list_refs_by_token(
        self, unit_id: int, token_text: str, *, limit: int = 100
    ) -> list[RefFact]:
        """List references by token text within a build unit."""
        stmt = (
            select(RefFact)
            .where(RefFact.unit_id == unit_id, RefFact.token_text == token_text)
            .limit(limit)
        )
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Scope lookups
    # -------------------------------------------------------------------------

    def get_scope(self, scope_id: int) -> ScopeFact | None:
        """Get a scope by ID."""
        return self._session.get(ScopeFact, scope_id)

    def list_scopes_in_file(self, file_id: int) -> list[ScopeFact]:
        """List all scopes in a file (typically bounded by file size)."""
        stmt = select(ScopeFact).where(ScopeFact.file_id == file_id)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Binding lookups
    # -------------------------------------------------------------------------

    def get_local_bind(self, scope_id: int, name: str) -> LocalBindFact | None:
        """Get a local binding by scope and name."""
        stmt = select(LocalBindFact).where(
            LocalBindFact.scope_id == scope_id, LocalBindFact.name == name
        )
        return self._session.exec(stmt).first()

    def list_binds_in_scope(self, scope_id: int, *, limit: int = 100) -> list[LocalBindFact]:
        """List all bindings in a scope."""
        stmt = select(LocalBindFact).where(LocalBindFact.scope_id == scope_id).limit(limit)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Import lookups
    # -------------------------------------------------------------------------

    def list_imports(self, file_id: int, *, limit: int = 100) -> list[ImportFact]:
        """List all imports in a file."""
        stmt = select(ImportFact).where(ImportFact.file_id == file_id).limit(limit)
        return list(self._session.exec(stmt).all())

    def get_import(self, import_uid: str) -> ImportFact | None:
        """Get an import by its UID."""
        return self._session.get(ImportFact, import_uid)

    # -------------------------------------------------------------------------
    # Export lookups
    # -------------------------------------------------------------------------

    def get_export_surface(self, unit_id: int) -> ExportSurface | None:
        """Get the export surface for a build unit."""
        stmt = select(ExportSurface).where(ExportSurface.unit_id == unit_id)
        return self._session.exec(stmt).first()

    def list_export_entries(self, surface_id: int, *, limit: int = 1000) -> list[ExportEntry]:
        """List export entries for a surface."""
        stmt = select(ExportEntry).where(ExportEntry.surface_id == surface_id).limit(limit)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # Anchor group lookups
    # -------------------------------------------------------------------------

    def get_anchor_group(
        self, unit_id: int, member_token: str, receiver_shape: str | None
    ) -> AnchorGroup | None:
        """Get an anchor group by token and receiver shape."""
        stmt = select(AnchorGroup).where(
            AnchorGroup.unit_id == unit_id,
            AnchorGroup.member_token == member_token,
        )
        if receiver_shape is not None:
            stmt = stmt.where(AnchorGroup.receiver_shape == receiver_shape)
        else:
            stmt = stmt.where(AnchorGroup.receiver_shape.is_(None))  # type: ignore[union-attr]
        return self._session.exec(stmt).first()

    def list_anchor_groups(self, unit_id: int, *, limit: int = 100) -> list[AnchorGroup]:
        """List anchor groups in a build unit."""
        stmt = select(AnchorGroup).where(AnchorGroup.unit_id == unit_id).limit(limit)
        return list(self._session.exec(stmt).all())

    # -------------------------------------------------------------------------
    # File lookups
    # -------------------------------------------------------------------------

    def get_file(self, file_id: int) -> File | None:
        """Get a file by ID."""
        return self._session.get(File, file_id)

    def get_file_by_path(self, path: str) -> File | None:
        """Get a file by path."""
        stmt = select(File).where(File.path == path)
        return self._session.exec(stmt).first()

    def list_files(self, *, limit: int = 10000) -> list[File]:
        """List all indexed files."""
        stmt = select(File).limit(limit)
        return list(self._session.exec(stmt).all())


# Re-export for backwards compatibility during migration
# These will be removed once all consumers are updated
SymbolGraph = FactQueries
