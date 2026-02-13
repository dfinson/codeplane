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

from sqlalchemy import text
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
# Resolution passes are registered in _RESOLUTION_PASSES and invoked via
# run_pass_1_5() to eliminate scattered call sites in ops.py.
#
# Languages with namespace/glob imports have dedicated resolution passes:
#   - resolve_namespace_refs: C# namespace-using resolution
#   - resolve_same_namespace_refs: C#/Java same-namespace visibility
#   - resolve_star_import_refs: Python star-import resolution
#   - resolve_go_dot_import_refs: Go dot-import resolution
#   - resolve_rust_glob_import_refs: Rust glob-import resolution
#   - resolve_java_star_import_refs: Java star-import resolution
# ============================================================================


# Type alias for resolution pass signature
ResolutionPassFn = Callable[["Database", int | None, list[int] | None], "CrossFileResolutionStats"]


@dataclass
class CrossFileResolutionStats:
    """Statistics from cross-file DB-backed resolution."""

    refs_upgraded: int = 0
    refs_matched: int = 0


# Resolvable C# type declaration kinds — used in SQL IN-lists.
# SYNC: Must match the tree-sitter node types in ``_TYPE_DECLS`` within
# ``treesitter.py::extract_csharp_namespace_types``, modulo the
# ``_declaration`` suffix that generic symbol extraction strips.
_TYPE_KINDS: tuple[str, ...] = ("class", "struct", "interface", "enum", "record", "record_struct")
_TYPE_KIND_FILTER = f"({', '.join(repr(k) for k in _TYPE_KINDS)})"


def _build_file_filter(
    file_ids: list[int] | None,
    alias: str = "rf",
) -> tuple[str, dict[str, int]]:
    """Build a parameterized file_id filter clause and bind values.

    Returns:
        (sql_fragment, bind_dict) where sql_fragment is either empty or
        ``AND <alias>.file_id IN (:fid_0, :fid_1, ...)`` and bind_dict
        maps the placeholder names to integer values.
    """
    if not file_ids:
        return ("", {})
    placeholders = [f":fid_{i}" for i in range(len(file_ids))]
    sql = f"AND {alias}.file_id IN ({', '.join(placeholders)})"
    binds = {f"fid_{i}": fid for i, fid in enumerate(file_ids)}
    return (sql, binds)


def _build_unit_filter(
    unit_id: int | None,
    alias: str = "rf",
) -> tuple[str, dict[str, int]]:
    """Build a parameterized unit_id filter clause and bind values.

    Returns:
        (sql_fragment, bind_dict) where sql_fragment is either empty or
        ``AND <alias>.unit_id = :unit_id`` and bind_dict maps the
        placeholder name to the integer value.
    """
    if unit_id is None:
        return ("", {})
    sql = f"AND {alias}.unit_id = :unit_id"
    binds = {"unit_id": unit_id}
    return (sql, binds)


def resolve_namespace_refs(
    db: Database,
    unit_id: int | None,
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
        unit_id: Context/unit ID to scope resolution to. Pass the context ID
            to prevent cross-context contamination in multi-unit repos, or
            None to resolve across all contexts.

    Returns:
        CrossFileResolutionStats
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")
    def_unit_filter, def_unit_binds = _build_unit_filter(unit_id, "df")

    with db.session() as session:
        # Count refs that will be upgraded (for stats)
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN import_facts imf ON rf.file_id = imf.file_id
                AND imf.import_kind = 'csharp_using'
                AND imf.alias IS NULL
            JOIN def_facts df ON df.name = rf.token_text
                AND df.namespace = imf.imported_name
                AND df.kind IN {_TYPE_KIND_FILTER}
                {def_unit_filter}
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
                {ref_unit_filter}
        """)
        result = session.execute(
            count_sql,
            {
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
                **def_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()

        if stats.refs_matched == 0:
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
                        AND df.kind IN {_TYPE_KIND_FILTER}
                        {def_unit_filter}
                    WHERE imf.file_id = ref_facts.file_id
                        AND imf.import_kind = 'csharp_using'
                        AND imf.alias IS NULL
                    ORDER BY df.def_uid ASC
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
                    AND df.kind IN {_TYPE_KIND_FILTER}
                    {def_unit_filter}
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
                    {ref_unit_filter}
            )
        """)
        update_result = session.execute(
            update_sql,
            {
                "strong_tier": RefTier.STRONG.value,
                "certain": Certainty.CERTAIN.value,
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
                **def_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]
        session.commit()

    return stats


def resolve_star_import_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using Python star-import evidence (DB-backed).

    For each file with ``from X import *``, resolves the source module to a
    project file, builds the set of module-level exports, and upgrades
    matching UNKNOWN refs to STRONG via a single SQL UPDATE.

    Resolution strategy:
    1. Find star imports (ORM query).
    2. Resolve each import's source literal to a project file_id using
       Python-side heuristic matching (``_find_python_module_file``).
    3. Insert the resulting (importing_file_id, source_file_id) mappings
       into a SQLite temp table.
    4. Execute a single UPDATE that joins ``ref_facts`` through
       ``import_facts``, the temp mapping, and ``def_facts`` to upgrade
       all eligible refs in one round-trip.

    Args:
        db: Database instance
        unit_id: Context/unit ID to scope resolution to. Pass the context ID
            to prevent cross-context contamination in multi-unit repos, or
            None to resolve across all contexts.
        file_ids: Optional list of file IDs to scope resolution. None = all.

    Returns:
        CrossFileResolutionStats
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")

    with db.session() as session:
        # Step 1: Find all star imports
        star_stmt = select(ImportFact).where(
            ImportFact.imported_name == "*",
            ImportFact.import_kind == "python_from",
        )
        if file_ids:
            star_stmt = star_stmt.where(col(ImportFact.file_id).in_(file_ids))
        if unit_id is not None:
            star_stmt = star_stmt.where(ImportFact.unit_id == unit_id)
        star_imports = list(session.exec(star_stmt).all())

        if not star_imports:
            return stats

        # Step 2: Build module path -> file_id mapping (Python heuristic)
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

        # Step 3: Resolve each star import's source to a file_id,
        # build the temp mapping table.
        session.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS _star_module_map "
                "(importing_file_id INTEGER, source_file_id INTEGER)"
            )
        )
        session.execute(text("DELETE FROM _star_module_map"))

        mappings: list[dict[str, int]] = []
        for star_imp in star_imports:
            source_literal = star_imp.source_literal
            if not source_literal:
                continue
            source_file_id = _find_python_module_file(
                source_literal, star_imp.file_id, module_to_file_id, all_files
            )
            if source_file_id is not None:
                mappings.append(
                    {
                        "importing_file_id": star_imp.file_id,
                        "source_file_id": source_file_id,
                    }
                )

        if not mappings:
            session.execute(text("DROP TABLE IF EXISTS _star_module_map"))
            return stats

        for m in mappings:
            session.execute(
                text("INSERT INTO _star_module_map VALUES (:importing_file_id, :source_file_id)"),
                m,
            )

        # Step 4: Count eligible refs (single query)
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN _star_module_map tsm ON tsm.importing_file_id = rf.file_id
            JOIN def_facts df ON df.file_id = tsm.source_file_id
                AND df.name = rf.token_text
                AND df.lexical_path = df.name
                AND df.name NOT LIKE '\\_%' ESCAPE '\\'
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
                {ref_unit_filter}
        """)
        result = session.execute(
            count_sql,
            {
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()

        if stats.refs_matched == 0:
            session.execute(text("DROP TABLE IF EXISTS _star_module_map"))
            return stats

        # Step 5: Single UPDATE with JOIN through temp table
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM _star_module_map tsm
                    JOIN def_facts df ON df.file_id = tsm.source_file_id
                        AND df.name = ref_facts.token_text
                        AND df.lexical_path = df.name
                        AND df.name NOT LIKE '\\_%' ESCAPE '\\'
                    WHERE tsm.importing_file_id = ref_facts.file_id
                    ORDER BY df.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN _star_module_map tsm ON tsm.importing_file_id = rf.file_id
                JOIN def_facts df ON df.file_id = tsm.source_file_id
                    AND df.name = rf.token_text
                    AND df.lexical_path = df.name
                    AND df.name NOT LIKE '\\_%' ESCAPE '\\'
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
                    {ref_unit_filter}
            )
        """)
        update_result = session.execute(
            update_sql,
            {
                "strong_tier": RefTier.STRONG.value,
                "certain": Certainty.CERTAIN.value,
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]

        session.execute(text("DROP TABLE IF EXISTS _star_module_map"))
        session.commit()

    return stats


def resolve_same_namespace_refs(
    db: Database,
    unit_id: int | None,
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
        unit_id: Context/unit ID to scope resolution to. Pass the context ID
            to prevent cross-context contamination in multi-unit repos, or
            None to resolve across all contexts.
        file_ids: Optional list of file IDs to scope resolution. None = all.

    Returns:
        CrossFileResolutionStats
    """
    stats = CrossFileResolutionStats()

    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")
    target_unit_filter, target_unit_binds = _build_unit_filter(unit_id, "target_def")

    with db.session() as session:
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
                AND target_def.kind IN {_TYPE_KIND_FILTER}
                AND (
                    target_def.namespace = file_def.namespace
                    OR file_def.namespace LIKE target_def.namespace || '.%'
                )
                {target_unit_filter}
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
                {ref_unit_filter}
        """)
        result = session.execute(
            count_sql,
            {
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
                **target_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()

        if stats.refs_matched == 0:
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
                        AND target_def.kind IN {_TYPE_KIND_FILTER}
                        AND (
                            target_def.namespace = file_def.namespace
                            OR file_def.namespace LIKE target_def.namespace || '.%'
                        )
                        {target_unit_filter}
                    WHERE file_def.file_id = ref_facts.file_id
                        AND file_def.namespace IS NOT NULL
                    ORDER BY target_def.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN def_facts file_def ON file_def.file_id = rf.file_id
                    AND file_def.namespace IS NOT NULL
                JOIN def_facts target_def ON target_def.name = rf.token_text
                    AND target_def.kind IN {_TYPE_KIND_FILTER}
                    AND (
                        target_def.namespace = file_def.namespace
                        OR file_def.namespace LIKE target_def.namespace || '.%'
                    )
                    {target_unit_filter}
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
                    {ref_unit_filter}
            )
        """)
        update_result = session.execute(
            update_sql,
            {
                "strong_tier": RefTier.STRONG.value,
                "certain": Certainty.CERTAIN.value,
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
                **target_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]
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
    # Require a path separator (or start-of-string) before the match to avoid
    # false positives like "afoo.py" matching a search for "foo".
    parts = source_literal.replace(".", "/")
    suffix_py = f"/{parts}.py"
    suffix_init = f"/{parts}/__init__.py"
    for fid, fpath in all_files:
        if fid is None or fpath is None:
            continue
        prefixed = f"/{fpath}"
        if prefixed.endswith(suffix_py) or prefixed.endswith(suffix_init):
            return fid

    return None


def resolve_go_dot_import_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using Go dot-import evidence (DB-backed).

    For each file with ``import . "pkg"``, resolves the source package
    to a project file, and upgrades matching UNKNOWN refs (where the ref
    matches an exported symbol from the imported package) to STRONG.

    Go dot imports are analogous to Python's ``from X import *``: all
    exported identifiers (capitalized names) from the package become
    directly accessible without qualification.

    Args:
        db: Database instance
        unit_id: Context/unit ID to scope resolution to.
        file_ids: Optional list of file IDs to scope resolution. None = all.

    Returns:
        CrossFileResolutionStats
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")

    with db.session() as session:
        # Step 1: Find all dot imports (imported_name == "*" for go_import)
        dot_stmt = select(ImportFact).where(
            ImportFact.imported_name == "*",
            ImportFact.import_kind == "go_import",
        )
        if file_ids:
            dot_stmt = dot_stmt.where(col(ImportFact.file_id).in_(file_ids))
        if unit_id is not None:
            dot_stmt = dot_stmt.where(ImportFact.unit_id == unit_id)
        dot_imports = list(session.exec(dot_stmt).all())

        if not dot_imports:
            return stats

        # Step 2: Build package path -> file_id mapping for Go files
        all_files: list[tuple[int | None, str]] = list(
            session.exec(select(File.id, File.path)).all()
        )
        pkg_to_file_id: dict[str, int] = {}
        for fid, fpath in all_files:
            if fid is None or fpath is None:
                continue
            if fpath.endswith(".go") and not fpath.endswith("_test.go"):
                # Use directory as package path
                pkg_path = fpath.rsplit("/", 1)[0] if "/" in fpath else ""
                if pkg_path:
                    pkg_to_file_id[pkg_path] = fid

        # Step 3: Create temp mapping table
        session.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS _go_dot_map "
                "(importing_file_id INTEGER, source_file_id INTEGER)"
            )
        )
        session.execute(text("DELETE FROM _go_dot_map"))

        mappings: list[dict[str, int]] = []
        for dot_imp in dot_imports:
            source_literal = dot_imp.source_literal
            if not source_literal:
                continue
            # Try to find a matching Go file by package path suffix
            source_file_id = _find_go_package_file(source_literal, pkg_to_file_id)
            if source_file_id is not None:
                mappings.append(
                    {
                        "importing_file_id": dot_imp.file_id,
                        "source_file_id": source_file_id,
                    }
                )

        if not mappings:
            session.execute(text("DROP TABLE IF EXISTS _go_dot_map"))
            return stats

        for m in mappings:
            session.execute(
                text("INSERT INTO _go_dot_map VALUES (:importing_file_id, :source_file_id)"),
                m,
            )

        # Step 4: Count eligible refs - exported Go symbols are capitalized
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN _go_dot_map tsm ON tsm.importing_file_id = rf.file_id
            JOIN def_facts df ON df.file_id = tsm.source_file_id
                AND df.name = rf.token_text
                AND df.name GLOB '[A-Z]*'
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
                {ref_unit_filter}
        """)
        result = session.execute(
            count_sql,
            {
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()

        if stats.refs_matched == 0:
            session.execute(text("DROP TABLE IF EXISTS _go_dot_map"))
            return stats

        # Step 5: Upgrade refs
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM _go_dot_map tsm
                    JOIN def_facts df ON df.file_id = tsm.source_file_id
                        AND df.name = ref_facts.token_text
                        AND df.name GLOB '[A-Z]*'
                    WHERE tsm.importing_file_id = ref_facts.file_id
                    ORDER BY df.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN _go_dot_map tsm ON tsm.importing_file_id = rf.file_id
                JOIN def_facts df ON df.file_id = tsm.source_file_id
                    AND df.name = rf.token_text
                    AND df.name GLOB '[A-Z]*'
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
                    {ref_unit_filter}
            )
        """)
        update_result = session.execute(
            update_sql,
            {
                "strong_tier": RefTier.STRONG.value,
                "certain": Certainty.CERTAIN.value,
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]

        session.execute(text("DROP TABLE IF EXISTS _go_dot_map"))
        session.commit()

    return stats


def _find_go_package_file(
    import_path: str,
    pkg_to_file_id: dict[str, int],
) -> int | None:
    """Resolve Go import path to a file_id.

    Args:
        import_path: Go import path like "github.com/foo/bar"
        pkg_to_file_id: Mapping from directory paths to file IDs
    """
    # Try suffix matching - import path may not match repo structure exactly
    for pkg_path, fid in pkg_to_file_id.items():
        if pkg_path.endswith(import_path) or import_path.endswith(pkg_path.split("/")[-1]):
            return fid
    return None


def resolve_rust_glob_import_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using Rust glob-import evidence (DB-backed).

    For each file with ``use crate::module::*;``, resolves the source module
    to a project file, and upgrades matching UNKNOWN refs to STRONG.

    Rust glob imports bring all public items from a module into scope.

    Args:
        db: Database instance
        unit_id: Context/unit ID to scope resolution to.
        file_ids: Optional list of file IDs to scope resolution. None = all.

    Returns:
        CrossFileResolutionStats
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")

    with db.session() as session:
        # Step 1: Find all glob imports (imported_name == "*" for rust_use)
        glob_stmt = select(ImportFact).where(
            ImportFact.imported_name == "*",
            ImportFact.import_kind == "rust_use",
        )
        if file_ids:
            glob_stmt = glob_stmt.where(col(ImportFact.file_id).in_(file_ids))
        if unit_id is not None:
            glob_stmt = glob_stmt.where(ImportFact.unit_id == unit_id)
        glob_imports = list(session.exec(glob_stmt).all())

        if not glob_imports:
            return stats

        # Step 2: Build module path -> file_id mapping for Rust files
        all_files: list[tuple[int | None, str]] = list(
            session.exec(select(File.id, File.path)).all()
        )
        module_to_file_id: dict[str, int] = {}
        for fid, fpath in all_files:
            if fid is None or fpath is None:
                continue
            if fpath.endswith(".rs"):
                # Convert path to Rust module path (e.g., src/lib.rs -> crate)
                module_path = _path_to_rust_module(fpath)
                if module_path:
                    module_to_file_id[module_path] = fid

        # Step 3: Create temp mapping table
        session.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS _rust_glob_map "
                "(importing_file_id INTEGER, source_file_id INTEGER)"
            )
        )
        session.execute(text("DELETE FROM _rust_glob_map"))

        mappings: list[dict[str, int]] = []
        for glob_imp in glob_imports:
            source_literal = glob_imp.source_literal
            if not source_literal:
                continue
            source_file_id = _find_rust_module_file(source_literal, module_to_file_id)
            if source_file_id is not None:
                mappings.append(
                    {
                        "importing_file_id": glob_imp.file_id,
                        "source_file_id": source_file_id,
                    }
                )

        if not mappings:
            session.execute(text("DROP TABLE IF EXISTS _rust_glob_map"))
            return stats

        for m in mappings:
            session.execute(
                text("INSERT INTO _rust_glob_map VALUES (:importing_file_id, :source_file_id)"),
                m,
            )

        # Step 4: Count eligible refs - public Rust symbols
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN _rust_glob_map tsm ON tsm.importing_file_id = rf.file_id
            JOIN def_facts df ON df.file_id = tsm.source_file_id
                AND df.name = rf.token_text
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
                {ref_unit_filter}
        """)
        result = session.execute(
            count_sql,
            {
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()

        if stats.refs_matched == 0:
            session.execute(text("DROP TABLE IF EXISTS _rust_glob_map"))
            return stats

        # Step 5: Upgrade refs
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM _rust_glob_map tsm
                    JOIN def_facts df ON df.file_id = tsm.source_file_id
                        AND df.name = ref_facts.token_text
                    WHERE tsm.importing_file_id = ref_facts.file_id
                    ORDER BY df.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN _rust_glob_map tsm ON tsm.importing_file_id = rf.file_id
                JOIN def_facts df ON df.file_id = tsm.source_file_id
                    AND df.name = rf.token_text
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
                    {ref_unit_filter}
            )
        """)
        update_result = session.execute(
            update_sql,
            {
                "strong_tier": RefTier.STRONG.value,
                "certain": Certainty.CERTAIN.value,
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]

        session.execute(text("DROP TABLE IF EXISTS _rust_glob_map"))
        session.commit()

    return stats


def _path_to_rust_module(path: str) -> str | None:
    """Convert file path to Rust module path."""
    if not path.endswith(".rs"):
        return None
    # Remove .rs extension
    module = path[:-3]
    # Map lib.rs and mod.rs to parent directory, main.rs to crate
    if module.endswith("/lib") or module.endswith("/mod"):
        module = module.rsplit("/", 1)[0]
    elif module.endswith("/main"):
        return "crate"
    # Convert path separators to ::
    module = module.replace("/", "::").replace("\\", "::")
    # Handle src/ prefix
    if module.startswith("src::"):
        module = "crate::" + module[5:]
    return module


def _find_rust_module_file(
    source_literal: str,
    module_to_file_id: dict[str, int],
) -> int | None:
    """Resolve Rust use path to a file_id."""
    # Direct match
    if source_literal in module_to_file_id:
        return module_to_file_id[source_literal]

    # Try suffix matching
    for mod_path, fid in module_to_file_id.items():
        if mod_path.endswith(source_literal) or source_literal.endswith(mod_path.split("::")[-1]):
            return fid

    return None


def resolve_java_star_import_refs(
    db: Database,
    unit_id: int | None,
    file_ids: list[int] | None = None,
) -> CrossFileResolutionStats:
    """Upgrade UNKNOWN refs using Java star-import evidence (DB-backed).

    For each file with ``import com.foo.*;``, resolves types from the
    imported package and upgrades matching UNKNOWN refs to STRONG.

    Java star imports bring all public types from a package into scope.

    Args:
        db: Database instance
        unit_id: Context/unit ID to scope resolution to.
        file_ids: Optional list of file IDs to scope resolution. None = all.

    Returns:
        CrossFileResolutionStats
    """
    stats = CrossFileResolutionStats()
    file_filter, file_binds = _build_file_filter(file_ids)
    ref_unit_filter, ref_unit_binds = _build_unit_filter(unit_id, "rf")
    def_unit_filter, def_unit_binds = _build_unit_filter(unit_id, "df")

    # Java type kinds to match
    java_type_kinds = "('class', 'interface', 'enum', 'record', 'annotation')"

    with db.session() as session:
        # Count refs that will be upgraded.
        # A star import's source_literal is the package path (e.g., "com.foo").
        # Match refs where:
        # - The import is a java_import with imported_name == "*"
        # - A def exists with matching name and namespace == import's source_literal
        count_sql = text(f"""
            SELECT COUNT(DISTINCT rf.ref_id)
            FROM ref_facts rf
            JOIN import_facts imf ON rf.file_id = imf.file_id
                AND imf.import_kind = 'java_import'
                AND imf.imported_name = '*'
            JOIN def_facts df ON df.name = rf.token_text
                AND df.namespace = imf.source_literal
                AND df.kind IN {java_type_kinds}
                {def_unit_filter}
            WHERE rf.ref_tier = :unknown_tier
                AND rf.role = :ref_role
                {file_filter}
                {ref_unit_filter}
        """)
        result = session.execute(
            count_sql,
            {
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
                **def_unit_binds,
            },
        )
        stats.refs_matched = result.scalar_one()

        if stats.refs_matched == 0:
            return stats

        # Perform the upgrade
        update_sql = text(f"""
            UPDATE ref_facts
            SET ref_tier = :strong_tier,
                certainty = :certain,
                target_def_uid = (
                    SELECT df.def_uid
                    FROM import_facts imf
                    JOIN def_facts df ON df.name = ref_facts.token_text
                        AND df.namespace = imf.source_literal
                        AND df.kind IN {java_type_kinds}
                        {def_unit_filter}
                    WHERE imf.file_id = ref_facts.file_id
                        AND imf.import_kind = 'java_import'
                        AND imf.imported_name = '*'
                    ORDER BY df.def_uid ASC
                    LIMIT 1
                )
            WHERE ref_id IN (
                SELECT DISTINCT rf.ref_id
                FROM ref_facts rf
                JOIN import_facts imf ON rf.file_id = imf.file_id
                    AND imf.import_kind = 'java_import'
                    AND imf.imported_name = '*'
                JOIN def_facts df ON df.name = rf.token_text
                    AND df.namespace = imf.source_literal
                    AND df.kind IN {java_type_kinds}
                    {def_unit_filter}
                WHERE rf.ref_tier = :unknown_tier
                    AND rf.role = :ref_role
                    {file_filter}
                    {ref_unit_filter}
            )
        """)
        update_result = session.execute(
            update_sql,
            {
                "strong_tier": RefTier.STRONG.value,
                "certain": Certainty.CERTAIN.value,
                "unknown_tier": RefTier.UNKNOWN.value,
                "ref_role": Role.REFERENCE.value,
                **file_binds,
                **ref_unit_binds,
                **def_unit_binds,
            },
        )
        stats.refs_upgraded = update_result.rowcount  # type: ignore[attr-defined]
        session.commit()

    return stats


# ============================================================================
# Resolution Registry (Option C from issue #135)
# ============================================================================
#
# Centralizes all Pass 1.5 resolution passes in a single registry, eliminating
# the three separate call sites in ops.py (init, incremental, cplignore).
#
# Adding a new language's resolution pass:
#   1. Implement resolve_{lang}_{pattern}_refs() following the existing pattern
#   2. Add it to _RESOLUTION_PASSES list below
#   3. No edits needed in ops.py
#
# Contract for resolution passes:
#   - Takes (db, unit_id, file_ids) - all optional scoping
#   - Returns CrossFileResolutionStats
#   - Must be idempotent (running twice produces same result)
#   - Must be cheap when no matches exist (early return)
# ============================================================================

_RESOLUTION_PASSES: list[ResolutionPassFn] = []


def _register_resolution_passes() -> None:
    """Populate the resolution pass registry.

    Called at module load time after all resolution functions are defined.
    """
    global _RESOLUTION_PASSES
    _RESOLUTION_PASSES = [
        resolve_namespace_refs,       # C# namespace-using
        resolve_same_namespace_refs,  # C# same/parent namespace visibility
        resolve_star_import_refs,     # Python from X import *
        resolve_go_dot_import_refs,   # Go import . "pkg"
        resolve_rust_glob_import_refs,  # Rust use module::*
        resolve_java_star_import_refs,  # Java import pkg.*
    ]


def run_pass_1_5(
    db: Database,
    unit_id: int | None = None,
    file_ids: list[int] | None = None,
) -> list[CrossFileResolutionStats]:
    """Run all registered Pass 1.5 cross-file resolution passes.

    This is the single entry point for ops.py to invoke all language-specific
    resolution logic, replacing the three separate call sites.

    Args:
        db: Database instance
        unit_id: Context/unit ID to scope resolution (None = all contexts)
        file_ids: File IDs to scope resolution (None = all files)

    Returns:
        List of CrossFileResolutionStats, one per pass
    """
    if not _RESOLUTION_PASSES:
        _register_resolution_passes()

    return [fn(db, unit_id, file_ids) for fn in _RESOLUTION_PASSES]
