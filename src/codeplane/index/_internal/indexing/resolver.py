"""Cross-file reference resolution for the structural index.

This module implements "Pass 2" of indexing - resolving cross-file references
by following ImportFact chains to find the actual target definitions.

The structural indexer (Pass 1) creates RefFacts with:
- PROVEN tier + target_def_uid for same-file references
- STRONG tier + target_def_uid=None for import-based references

This module resolves the STRONG refs by:
1. Finding the ImportFact that introduced the name
2. Looking up the source module's DefFact
3. Updating the RefFact's target_def_uid

Per SPEC.md §7.9, this is a best-effort heuristic resolution.
Certainty is marked appropriately when resolution is ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlmodel import col, select

from codeplane.index.models import (
    Certainty,
    DefFact,
    File,
    ImportFact,
    LocalBindFact,
    RefFact,
    RefTier,
)

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database


@dataclass
class ResolutionStats:
    """Statistics from reference resolution."""

    refs_processed: int = 0
    refs_resolved: int = 0
    refs_unresolved: int = 0
    refs_ambiguous: int = 0


class ReferenceResolver:
    """Resolves cross-file references by following import chains.

    This implements the "STRONG" tier resolution per SPEC.md §7.3.2:
    - STRONG refs have an ImportFact trace but need def_uid lookup
    - Resolution follows: RefFact -> LocalBindFact -> ImportFact -> DefFact

    Usage after structural indexing::

        resolver = ReferenceResolver(db)
        stats = resolver.resolve_all()
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        # Cache module path -> file_id mapping
        self._module_to_file: dict[str, int] = {}
        # Cache file_id -> exported symbols
        self._file_exports: dict[int, dict[str, str]] = {}  # name -> def_uid

    def resolve_all(self, *, limit: int = 10000) -> ResolutionStats:
        """Resolve all unresolved STRONG-tier references.

        Args:
            limit: Maximum refs to process in one batch

        Returns:
            ResolutionStats with counts
        """
        stats = ResolutionStats()

        with self._db.session() as session:
            # Find STRONG refs with no target_def_uid
            stmt = (
                select(RefFact)
                .where(
                    RefFact.ref_tier == RefTier.STRONG.value,
                    RefFact.target_def_uid == None,  # noqa: E711
                )
                .limit(limit)
            )
            unresolved_refs = list(session.exec(stmt).all())
            stats.refs_processed = len(unresolved_refs)

            # Build caches
            self._build_module_cache(session)
            self._build_export_cache(session)

            # Resolve each ref
            for ref in unresolved_refs:
                resolved = self._resolve_ref(session, ref)
                if resolved:
                    stats.refs_resolved += 1
                else:
                    stats.refs_unresolved += 1

            session.commit()

        return stats

    def resolve_for_files(self, file_ids: list[int]) -> ResolutionStats:
        """Resolve references only for specific files.

        Use this for incremental updates after re-indexing specific files.
        """
        stats = ResolutionStats()

        with self._db.session() as session:
            # Find STRONG refs in these files with no target_def_uid
            stmt = (
                select(RefFact)
                .where(
                    col(RefFact.file_id).in_(file_ids),
                    RefFact.ref_tier == RefTier.STRONG.value,
                    RefFact.target_def_uid == None,  # noqa: E711
                )
            )
            unresolved_refs = list(session.exec(stmt).all())
            stats.refs_processed = len(unresolved_refs)

            # Build caches (only if we have refs to resolve)
            if unresolved_refs:
                self._build_module_cache(session)
                self._build_export_cache(session)

                for ref in unresolved_refs:
                    resolved = self._resolve_ref(session, ref)
                    if resolved:
                        stats.refs_resolved += 1
                    else:
                        stats.refs_unresolved += 1

                session.commit()

        return stats

    def _resolve_ref(self, session: object, ref: RefFact) -> bool:
        """Attempt to resolve a single reference.

        Returns True if resolution succeeded.
        """
        # Find the LocalBindFact that binds this name
        bind_stmt = (
            select(LocalBindFact)
            .where(
                LocalBindFact.file_id == ref.file_id,
                LocalBindFact.name == ref.token_text,
            )
        )
        bind = session.exec(bind_stmt).first()  # type: ignore[union-attr]

        if bind is None:
            return False

        # If it's a DEF binding (same-file), should already be PROVEN
        if bind.target_kind == "DEF":
            ref.target_def_uid = bind.target_uid
            ref.certainty = Certainty.CERTAIN.value
            return True

        # If it's an IMPORT binding, follow the import chain
        if bind.target_kind == "IMPORT":
            import_uid = bind.target_uid
            return self._resolve_import_ref(session, ref, import_uid)

        return False

    def _resolve_import_ref(self, session: object, ref: RefFact, import_uid: str) -> bool:
        """Resolve a reference via import chain."""
        # Find the ImportFact
        import_stmt = select(ImportFact).where(ImportFact.import_uid == import_uid)
        imp = session.exec(import_stmt).first()  # type: ignore[union-attr]

        if imp is None:
            return False

        # Get the source module path
        source_literal = imp.source_literal
        if not source_literal:
            return False

        # Look up the target file
        target_file_id = self._find_module_file(source_literal)
        if target_file_id is None:
            return False

        # Look up the exported symbol
        imported_name = imp.imported_name
        exports = self._file_exports.get(target_file_id, {})

        if imported_name in exports:
            ref.target_def_uid = exports[imported_name]
            ref.certainty = Certainty.CERTAIN.value
            return True

        # Try wildcard - if importing module itself, look for __all__
        if imported_name == "*" or imported_name == source_literal.split(".")[-1]:
            # Module-level import, can't resolve to specific def
            return False

        return False

    def _build_module_cache(self, session: object) -> None:
        """Build mapping from module path to file_id."""
        self._module_to_file = {}

        stmt = select(File.id, File.path)
        files = session.exec(stmt).all()  # type: ignore[union-attr]

        for file_id, path in files:
            if file_id is None:
                continue
            # Convert path to module path (e.g., src/foo/bar.py -> src.foo.bar)
            module_path = self._path_to_module(path)
            if module_path:
                self._module_to_file[module_path] = file_id

    def _build_export_cache(self, session: object) -> None:
        """Build mapping from file_id to exported symbols."""
        self._file_exports = {}

        # Get all top-level definitions (no parent = exported)
        stmt = select(DefFact).where(
            col(DefFact.kind).in_(["function", "class", "variable"]),
        )
        defs = session.exec(stmt).all()  # type: ignore[union-attr]

        for d in defs:
            if d.file_id not in self._file_exports:
                self._file_exports[d.file_id] = {}

            # Simple heuristic: public names don't start with _
            if not d.name.startswith("_"):
                self._file_exports[d.file_id][d.name] = d.def_uid

    def _find_module_file(self, source_literal: str) -> int | None:
        """Find file_id for a module import path."""
        # Direct match
        if source_literal in self._module_to_file:
            return self._module_to_file[source_literal]

        # Try common patterns
        # foo.bar -> foo/bar.py or foo/bar/__init__.py
        candidates = [
            source_literal,
            source_literal.replace(".", "/"),
            f"{source_literal.replace('.', '/')}/__init__",
        ]

        for candidate in candidates:
            if candidate in self._module_to_file:
                return self._module_to_file[candidate]

        return None

    def _path_to_module(self, path: str) -> str | None:
        """Convert file path to Python module path."""
        if not path.endswith(".py"):
            return None

        # Remove .py extension
        module = path[:-3]

        # Handle __init__.py
        if module.endswith("/__init__"):
            module = module[:-9]

        # Convert / to .
        module = module.replace("/", ".").replace("\\", ".")

        # Remove leading . if any
        module = module.lstrip(".")

        return module


def resolve_references(db: Database, file_ids: list[int] | None = None) -> ResolutionStats:
    """Convenience function to resolve cross-file references.

    Args:
        db: Database instance
        file_ids: Optional list of file IDs to resolve (None = all)

    Returns:
        ResolutionStats
    """
    resolver = ReferenceResolver(db)
    if file_ids:
        return resolver.resolve_for_files(file_ids)
    return resolver.resolve_all()
