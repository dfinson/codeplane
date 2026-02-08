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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlmodel import col, select

from codeplane.index.models import (
    BindTargetKind,
    Certainty,
    DefFact,
    File,
    ImportFact,
    LocalBindFact,
    RefFact,
    RefTier,
    Role,
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

    def resolve_all(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ResolutionStats:
        """Resolve all unresolved STRONG-tier references.

        Args:
            on_progress: Optional callback(processed, total) for progress updates

        Returns:
            ResolutionStats with counts
        """
        stats = ResolutionStats()

        with self._db.session() as session:
            # Find ALL STRONG refs with no target_def_uid
            stmt = select(RefFact).where(
                RefFact.ref_tier == RefTier.STRONG.value,
                RefFact.target_def_uid == None,  # noqa: E711
            )
            unresolved_refs = list(session.exec(stmt).all())
            stats.refs_processed = len(unresolved_refs)
            total = len(unresolved_refs)

            # Build caches
            self._build_module_cache(session)
            self._build_export_cache(session)

            # Resolve each ref
            for i, ref in enumerate(unresolved_refs):
                resolved = self._resolve_ref(session, ref)
                if resolved:
                    stats.refs_resolved += 1
                else:
                    stats.refs_unresolved += 1

                # Report progress every 50 refs
                if on_progress and (i + 1) % 50 == 0:
                    on_progress(i + 1, total)

            # Final progress update
            if on_progress and total > 0:
                on_progress(total, total)

            session.commit()

        return stats

    def resolve_for_files(
        self,
        file_ids: list[int],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ResolutionStats:
        """Resolve references only for specific files.

        Use this for incremental updates after re-indexing specific files.

        Args:
            file_ids: List of file IDs to resolve
            on_progress: Optional callback(processed, total) for progress updates
        """
        stats = ResolutionStats()

        with self._db.session() as session:
            # Find STRONG refs in these files with no target_def_uid
            stmt = select(RefFact).where(
                col(RefFact.file_id).in_(file_ids),
                RefFact.ref_tier == RefTier.STRONG.value,
                RefFact.target_def_uid == None,  # noqa: E711
            )
            unresolved_refs = list(session.exec(stmt).all())
            stats.refs_processed = len(unresolved_refs)
            total = len(unresolved_refs)

            # Build caches (only if we have refs to resolve)
            if unresolved_refs:
                self._build_module_cache(session)
                self._build_export_cache(session)

                for i, ref in enumerate(unresolved_refs):
                    resolved = self._resolve_ref(session, ref)
                    if resolved:
                        stats.refs_resolved += 1
                    else:
                        stats.refs_unresolved += 1

                    # Report progress every 50 refs
                    if on_progress and (i + 1) % 50 == 0:
                        on_progress(i + 1, total)

                # Final progress update
                if on_progress and total > 0:
                    on_progress(total, total)

                session.commit()

        return stats

    def _resolve_ref(self, session: object, ref: RefFact) -> bool:
        """Attempt to resolve a single reference.

        Returns True if resolution succeeded.
        """
        # Find the LocalBindFact that binds this name
        bind_stmt = select(LocalBindFact).where(
            LocalBindFact.file_id == ref.file_id,
            LocalBindFact.name == ref.token_text,
        )
        bind = session.exec(bind_stmt).first()  # type: ignore[attr-defined]

        if bind is None:
            return False

        # If it's a DEF binding (same-file), should already be PROVEN
        if bind.target_kind == BindTargetKind.DEF.value:
            ref.target_def_uid = bind.target_uid
            ref.certainty = Certainty.CERTAIN.value
            return True

        # If it's an IMPORT binding, follow the import chain
        if bind.target_kind == BindTargetKind.IMPORT.value:
            import_uid = bind.target_uid
            return self._resolve_import_ref(session, ref, import_uid)

        return False

    def _resolve_import_ref(self, session: object, ref: RefFact, import_uid: str) -> bool:
        """Resolve a reference via import chain."""
        # Find the ImportFact
        import_stmt = select(ImportFact).where(ImportFact.import_uid == import_uid)
        imp = session.exec(import_stmt).first()  # type: ignore[attr-defined]

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
        files = session.exec(stmt).all()  # type: ignore[attr-defined]

        for file_id, path in files:
            if file_id is None:
                continue
            # Convert path to module path (e.g., src/foo/bar.py -> src.foo.bar)
            module_path = self._path_to_module(path)
            if module_path:
                self._module_to_file[module_path] = file_id

    def _build_export_cache(self, session: object) -> None:
        """Build mapping from file_id to exported symbols.

        Includes:
        1. Direct definitions (DefFact) in the file
        2. Re-exports (import + expose at module level via LocalBindFact)
        """
        self._file_exports = {}

        # Step 1: Get all top-level definitions
        stmt = select(DefFact).where(
            col(DefFact.kind).in_(["function", "class", "variable"]),
        )
        defs = session.exec(stmt).all()  # type: ignore[attr-defined]

        for d in defs:
            if d.file_id not in self._file_exports:
                self._file_exports[d.file_id] = {}

            # Simple heuristic: public names don't start with _
            if not d.name.startswith("_"):
                self._file_exports[d.file_id][d.name] = d.def_uid

        # Step 2: Add re-exports (imports that are exposed at module level)
        # These are LocalBindFacts with target_kind='import' - common in __init__.py
        reexport_stmt = (
            select(LocalBindFact, ImportFact)
            .join(
                ImportFact,
                LocalBindFact.target_uid == ImportFact.import_uid,  # type: ignore[arg-type]
            )
            .where(
                LocalBindFact.target_kind == BindTargetKind.IMPORT.value,
            )
        )
        reexports = session.exec(reexport_stmt).all()  # type: ignore[attr-defined]

        for bind, imp in reexports:
            if bind.name.startswith("_"):
                continue

            # Find the actual definition in the source module
            source_file_id = (
                self._find_module_file(imp.source_literal) if imp.source_literal else None
            )
            if source_file_id is None:
                continue

            # Look up the def_uid from the source module's exports
            source_exports = self._file_exports.get(source_file_id, {})
            if imp.imported_name in source_exports:
                def_uid = source_exports[imp.imported_name]
                # Add to this file's exports
                if bind.file_id not in self._file_exports:
                    self._file_exports[bind.file_id] = {}
                self._file_exports[bind.file_id][bind.name] = def_uid

    def _find_module_file(self, source_literal: str) -> int | None:
        """Find file_id for a module import path."""
        # Direct match
        if source_literal in self._module_to_file:
            return self._module_to_file[source_literal]

        # Try common patterns:
        # 1. foo.bar -> foo/bar.py or foo/bar/__init__.py
        # 2. src.foo.bar -> also try for codebase with src/ prefix
        candidates = [
            source_literal,
            source_literal.replace(".", "/"),
            f"{source_literal.replace('.', '/')}/__init__",
            # Handle src/ prefix - imports like 'codeplane.foo' map to 'src/codeplane/foo.py'
            f"src.{source_literal}",
            f"src/{source_literal.replace('.', '/')}",
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


def resolve_references(
    db: Database,
    file_ids: list[int] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ResolutionStats:
    """Convenience function to resolve cross-file references.

    Args:
        db: Database instance
        file_ids: Optional list of file IDs to resolve (None = all)
        on_progress: Optional callback(processed, total) for progress updates

    Returns:
        ResolutionStats
    """
    resolver = ReferenceResolver(db)
    if file_ids:
        return resolver.resolve_for_files(file_ids, on_progress)
    return resolver.resolve_all(on_progress)


# ============================================================================
# Pass 1.5: DB-backed cross-file resolution
# ============================================================================
#
# These functions run AFTER all structural facts are persisted to the DB,
# eliminating the batch-boundary problem where in-memory resolution only
# saw a fraction of namespace-type mappings per 25-file batch.
#
# resolve_namespace_refs: C# namespace-using resolution
# resolve_star_import_refs: Python star-import resolution
# ============================================================================


@dataclass
class CrossFileResolutionStats:
    """Statistics from cross-file DB-backed resolution."""

    refs_upgraded: int = 0
    refs_scanned: int = 0


def resolve_namespace_refs(
    db: Database,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using C# namespace-using evidence (DB-backed).

    For each UNKNOWN REFERENCE ref in a file with ``using Namespace;``
    directives, checks if the ref's token_text matches a DefFact.name
    where that def's namespace equals one of the file's using'd namespaces.
    Matching refs are upgraded to STRONG.

    This replaces the old in-memory ``_resolve_cross_file_refs()`` which was
    limited to seeing only 25 files per batch.

    Args:
        db: Database instance
        file_ids: Optional list of file IDs to scope resolution. None = all.

    Returns:
        CrossFileResolutionStats
    """
    stats = CrossFileResolutionStats()

    with db.session() as session:
        from sqlalchemy import text

        # Build the SQL query with optional file_id scoping
        # Logic:
        #   1. Find UNKNOWN REFERENCE refs
        #   2. whose file has a csharp_using ImportFact (no alias)
        #   3. where a DefFact exists with matching name and namespace
        #   4. Upgrade those refs to STRONG
        if file_ids:
            file_id_list = ",".join(str(fid) for fid in file_ids)
            file_filter = f"AND rf.file_id IN ({file_id_list})"
        else:
            file_filter = ""

        # Count refs that will be upgraded (for stats)
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN import_facts imf ON rf.file_id = imf.file_id
                AND imf.import_kind = 'csharp_using'
                AND imf.alias IS NULL
            JOIN def_facts df ON df.name = rf.token_text
                AND df.namespace = imf.imported_name
                AND df.kind IN ('class', 'struct', 'interface', 'enum')
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
        """)
        result = session.execute(
            count_sql.bindparams(
                unknown_tier=RefTier.UNKNOWN.value,
                ref_role=Role.REFERENCE.value,
            )
        )
        stats.refs_scanned = result.scalar_one()

        if stats.refs_scanned == 0:
            return stats

        # Perform the upgrade — also link target_def_uid so the rename
        # code path can find these refs via list_refs_by_def_uid().
        # Use a correlated subquery to resolve def_uid for each ref.
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM import_facts imf
                    JOIN def_facts df ON df.name = ref_facts.token_text
                        AND df.namespace = imf.imported_name
                        AND df.kind IN ('class', 'struct', 'interface', 'enum')
                    WHERE imf.file_id = ref_facts.file_id
                        AND imf.import_kind = 'csharp_using'
                        AND imf.alias IS NULL
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN import_facts imf ON rf.file_id = imf.file_id
                    AND imf.import_kind = 'csharp_using'
                    AND imf.alias IS NULL
                JOIN def_facts df ON df.name = rf.token_text
                    AND df.namespace = imf.imported_name
                    AND df.kind IN ('class', 'struct', 'interface', 'enum')
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
            )
        """)
        session.execute(
            update_sql.bindparams(
                strong_tier=RefTier.STRONG.value,
                certain=Certainty.CERTAIN.value,
                unknown_tier=RefTier.UNKNOWN.value,
                ref_role=Role.REFERENCE.value,
            )
        )
        stats.refs_upgraded = stats.refs_scanned
        session.commit()

    return stats


def resolve_star_import_refs(
    db: Database,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using Python star-import evidence (DB-backed).

    For each file with ``from X import *``, resolves the source module to a
    project file, builds the set of module-level exports, and upgrades
    matching UNKNOWN refs to STRONG.

    This replaces the old in-memory ``_resolve_python_star_refs()`` which was
    limited to seeing only 25 files per batch.

    Args:
        db: Database instance
        file_ids: Optional list of file IDs to scope resolution. None = all.

    Returns:
        CrossFileResolutionStats
    """
    stats = CrossFileResolutionStats()

    with db.session() as session:
        # Step 1: Find all star imports
        star_stmt = select(ImportFact).where(
            ImportFact.imported_name == "*",
            ImportFact.import_kind == "python_from",
        )
        if file_ids:
            star_stmt = star_stmt.where(col(ImportFact.file_id).in_(file_ids))
        star_imports = list(session.exec(star_stmt).all())

        if not star_imports:
            return stats

        # Step 2: Build module path -> file_id mapping
        all_files: list[tuple[int | None, str]] = list(
            session.exec(select(File.id, File.path)).all()
        )
        module_to_file_id: dict[str, int] = {}
        for fid, fpath in all_files:
            if fid is None or fpath is None:
                continue
            module_path = _path_to_python_module(fpath)
            if module_path:
                module_to_file_id[module_path] = fid

        # Step 3: For each star import, find source module and resolve refs
        for star_imp in star_imports:
            source_literal = star_imp.source_literal
            if not source_literal:
                continue

            # Resolve source module to file_id
            source_file_id = _find_python_module_file(
                source_literal, star_imp.file_id, module_to_file_id, all_files
            )
            if source_file_id is None:
                continue

            # Get module-level defs from source file (name + def_uid for linking)
            source_def_rows = session.exec(
                select(DefFact.name, DefFact.def_uid).where(
                    DefFact.file_id == source_file_id,
                    DefFact.lexical_path == DefFact.name,  # Module-level: lexical_path == name
                )
            ).all()
            export_map: dict[str, str] = {
                name: uid for name, uid in source_def_rows if not name.startswith("_")
            }

            if not export_map:
                continue

            # Upgrade UNKNOWN refs in the importing file that match exports
            unknown_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == star_imp.file_id,
                    RefFact.ref_tier == RefTier.UNKNOWN.value,
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()

            for ref in unknown_refs:
                stats.refs_scanned += 1
                if ref.token_text in export_map:
                    ref.ref_tier = RefTier.STRONG.value
                    ref.certainty = Certainty.CERTAIN.value
                    ref.target_def_uid = export_map[ref.token_text]
                    stats.refs_upgraded += 1

        session.commit()

    return stats


def resolve_same_namespace_refs(
    db: Database,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using same/parent namespace visibility (DB-backed).

    In C# (and Java/Kotlin), types in the same namespace or a parent namespace
    are visible without an explicit ``using`` directive. For example, a file in
    ``namespace Newtonsoft.Json.Converters`` can reference ``JsonSerializer``
    from ``Newtonsoft.Json`` without ``using Newtonsoft.Json;``.

    Resolution logic:
    1. Find UNKNOWN REFERENCE refs
    2. For each ref, find which namespace(s) the ref's file declares
       (via DefFacts with a non-null namespace in the same file)
    3. Check if a DefFact exists with matching name where:
       - The def's namespace equals the ref's file namespace (same namespace), OR
       - The def's namespace is a parent of the ref's file namespace
         (e.g., def in "A.B" visible from file in "A.B.C")
    4. Upgrade matching refs to STRONG with target_def_uid linked

    Args:
        db: Database instance
        file_ids: Optional list of file IDs to scope resolution. None = all.

    Returns:
        CrossFileResolutionStats
    """
    stats = CrossFileResolutionStats()

    with db.session() as session:
        from sqlalchemy import text

        if file_ids:
            file_id_list = ",".join(str(fid) for fid in file_ids)
            file_filter = f"AND rf.file_id IN ({file_id_list})"
        else:
            file_filter = ""

        # Count refs that will be upgraded.
        # Join: ref's file has a DefFact with a namespace, and a target DefFact
        # exists with matching name whose namespace is the same as or a parent
        # of the file's namespace.
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN def_facts file_def ON file_def.file_id = rf.file_id
                AND file_def.namespace IS NOT NULL
            JOIN def_facts target_def ON target_def.name = rf.token_text
                AND target_def.kind IN ('class', 'struct', 'interface', 'enum')
                AND (
                    target_def.namespace = file_def.namespace
                    OR file_def.namespace LIKE target_def.namespace || '.%'
                )
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
        """)
        result = session.execute(
            count_sql.bindparams(
                unknown_tier=RefTier.UNKNOWN.value,
                ref_role=Role.REFERENCE.value,
            )
        )
        stats.refs_scanned = result.scalar_one()

        if stats.refs_scanned == 0:
            return stats

        # Perform the upgrade with target_def_uid linking.
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT target_def.def_uid
                    FROM def_facts file_def
                    JOIN def_facts target_def ON target_def.name = ref_facts.token_text
                        AND target_def.kind IN ('class', 'struct', 'interface', 'enum')
                        AND (
                            target_def.namespace = file_def.namespace
                            OR file_def.namespace LIKE target_def.namespace || '.%'
                        )
                    WHERE file_def.file_id = ref_facts.file_id
                        AND file_def.namespace IS NOT NULL
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN def_facts file_def ON file_def.file_id = rf.file_id
                    AND file_def.namespace IS NOT NULL
                JOIN def_facts target_def ON target_def.name = rf.token_text
                    AND target_def.kind IN ('class', 'struct', 'interface', 'enum')
                    AND (
                        target_def.namespace = file_def.namespace
                        OR file_def.namespace LIKE target_def.namespace || '.%'
                    )
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
            )
        """)
        session.execute(
            update_sql.bindparams(
                strong_tier=RefTier.STRONG.value,
                certain=Certainty.CERTAIN.value,
                unknown_tier=RefTier.UNKNOWN.value,
                ref_role=Role.REFERENCE.value,
            )
        )
        stats.refs_upgraded = stats.refs_scanned
        session.commit()

    return stats


def _path_to_python_module(path: str) -> str | None:
    """Convert file path to Python module path."""
    if not path.endswith(".py"):
        return None
    module = path[:-3]
    if module.endswith("/__init__"):
        module = module[:-9]
    module = module.replace("/", ".").replace("\\", ".").lstrip(".")
    return module


def _find_python_module_file(
    source_literal: str,
    _importing_file_id: int,
    module_to_file_id: dict[str, int],
    all_files: Sequence[tuple[int | None, str]],
) -> int | None:
    """Resolve Python import source literal to a file_id."""
    # Direct match
    if source_literal in module_to_file_id:
        return module_to_file_id[source_literal]

    # Try suffix-based matching (handles src/ prefixes etc.)
    parts = source_literal.replace(".", "/")
    for fid, fpath in all_files:
        if fid is None or fpath is None:
            continue
        if fpath.endswith(f"{parts}.py") or fpath.endswith(f"{parts}/__init__.py"):
            return fid

    return None
