"""Config file cross-language reference resolver.

Creates synthetic ImportFact edges from config files (TOML, YAML, JSON,
Makefile) to source files when a string literal in the config file
deterministically resolves to an existing file in the repo tree.

Resolution strategies (all require the target file to actually exist):
1. Direct file path: string matches a known repo-relative path
2. Dotted module path: ``a.b.c`` → ``a/b/c.py`` or ``a/b/c/__init__.py``
3. Entry point: ``a.b.c:obj`` → resolve ``a.b.c`` as module path
4. Directory path: ``tests/`` → ``tests`` (if directory exists in repo)

No heuristics - every edge is backed by a verified file existence check.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from codeplane.index._internal.db import Database

logger = logging.getLogger(__name__)

# Config file extensions that we scan for cross-file string references.
_CONFIG_EXTENSIONS: frozenset[str] = frozenset({
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".cfg",
    ".ini",
})

# Basenames (no extension) that are also config files.
_CONFIG_BASENAMES: frozenset[str] = frozenset({
    "makefile",
    "gnumakefile",
    "dockerfile",
})

# Regex to extract double- and single-quoted strings (3–200 chars inside quotes).
_RE_DQUOTE = re.compile(r'"([^"\n]{3,200})"')
_RE_SQUOTE = re.compile(r"'([^'\n]{3,200})'")

# Pattern for dotted Python module paths (e.g. ``evee.cli.main``).
_RE_MODULE_PATH = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$")

# Pattern for entry points (e.g. ``evee.cli.main:app``).
_RE_ENTRY_POINT = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_.]+):([a-zA-Z_][a-zA-Z0-9_]*)$")

# Pattern for bare file/directory paths (e.g. ``src/evee``, ``tests/``).
_RE_PATH_LIKE = re.compile(r"^[a-zA-Z0-9_.][a-zA-Z0-9_./\-]*$")

# Strings to skip (common non-path values found in config files).
_SKIP_PREFIXES: tuple[str, ...] = (
    "http://",
    "https://",
    "git://",
    "ssh://",
    "ftp://",
    "mailto:",
    ">=",
    "<=",
    "==",
    "!=",
    "~=",
)


def _is_config_file(path: str) -> bool:
    """Check if a file path is a config file we should scan."""
    lower = path.lower()
    basename = lower.rsplit("/", 1)[-1]

    # Check basename match (Makefile, Dockerfile, etc.)
    name_no_ext = basename.split(".")[0]
    if name_no_ext in _CONFIG_BASENAMES or basename in _CONFIG_BASENAMES:
        return True

    # Check extension match
    dot_idx = basename.rfind(".")
    if dot_idx >= 0:
        ext = basename[dot_idx:]
        return ext in _CONFIG_EXTENSIONS

    return False


def _extract_strings(content: str) -> list[tuple[str, int]]:
    """Extract quoted strings from file content with line numbers.

    Returns:
        List of (string_value, 1-indexed_line_number) tuples.
    """
    results: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    for pattern in (_RE_DQUOTE, _RE_SQUOTE):
        for match in pattern.finditer(content):
            value = match.group(1).strip()
            if not value:
                continue

            # Skip URLs, version constraints, etc.
            if any(value.startswith(p) for p in _SKIP_PREFIXES):
                continue

            # Compute 1-indexed line number
            line = content[:match.start()].count("\n") + 1

            key = (value, line)
            if key not in seen:
                seen.add(key)
                results.append((value, line))

    return results


def _make_import_uid(config_path: str, resolved_path: str, line: int) -> str:
    """Generate a deterministic import UID for a config file ref."""
    raw = f"config_ref:{config_path}:{line}:{resolved_path}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _try_resolve(
    value: str,
    path_set: frozenset[str],
    dir_set: frozenset[str],
) -> str | None:
    """Try to resolve a string value to a repo-relative file path.

    Returns the resolved path if found, None otherwise.
    All resolution is deterministic — the target file must exist.
    """
    # Strip leading ./ if present
    cleaned = value.lstrip("./") if value.startswith("./") else value
    # Strip trailing / for directory matching
    cleaned_no_slash = cleaned.rstrip("/")

    # 1. Direct file path match
    if cleaned in path_set:
        return cleaned

    # Also try with trailing slash stripped
    if cleaned_no_slash and cleaned_no_slash in path_set:
        return cleaned_no_slash

    # 2. Entry point format: ``module.path:object``
    ep_match = _RE_ENTRY_POINT.match(cleaned)
    if ep_match:
        module_part = ep_match.group(1)
        resolved = _resolve_module_path(module_part, path_set)
        if resolved:
            return resolved

    # 3. Dotted module path: ``a.b.c`` → ``a/b/c.py`` or ``a/b/c/__init__.py``
    if _RE_MODULE_PATH.match(cleaned):
        resolved = _resolve_module_path(cleaned, path_set)
        if resolved:
            return resolved

    # 4. Directory → __init__.py (e.g. ``tests`` → ``tests/__init__.py``)
    if cleaned_no_slash and cleaned_no_slash in dir_set:
        init_path = f"{cleaned_no_slash}/__init__.py"
        if init_path in path_set:
            return init_path

    return None


def _resolve_module_path(dotted: str, path_set: frozenset[str]) -> str | None:
    """Convert a dotted module path to a file path and check existence.

    Tries:
    - ``a.b.c`` → ``a/b/c.py``
    - ``a.b.c`` → ``a/b/c/__init__.py``
    - Also tries with ``src/`` prefix for common repo layouts.
    """
    path_base = dotted.replace(".", "/")

    # Try direct .py file
    py_path = f"{path_base}.py"
    if py_path in path_set:
        return py_path

    # Try package __init__.py
    init_path = f"{path_base}/__init__.py"
    if init_path in path_set:
        return init_path

    # Try with src/ prefix (common layout)
    src_py = f"src/{py_path}"
    if src_py in path_set:
        return src_py

    src_init = f"src/{init_path}"
    if src_init in path_set:
        return src_init

    return None


def resolve_config_file_refs(
    db: Database,
    repo_path: Path,
) -> int:
    """Scan config files for string literals that resolve to repo files.

    Creates ImportFact rows with ``import_kind='config_file_ref'``
    and a populated ``resolved_path`` for each match. These edges
    connect config files to the source files they reference, making
    them discoverable via the import harvester in recon.

    Idempotent: deletes existing ``config_file_ref`` imports before
    re-creating them.

    Args:
        db: Database instance.
        repo_path: Absolute path to the repository root.

    Returns:
        Number of ImportFact rows created.
    """
    from sqlmodel import select, text

    from codeplane.index.models import DefFact, File, ImportFact

    # 1. Build complete file path set and directory set from DB.
    with db.session() as session:
        all_files: list[tuple[int, str]] = list(
            session.exec(select(File.id, File.path)).all()
        )

    path_set = frozenset(path for _, path in all_files)

    # Build directory set from known paths
    dir_set: set[str] = set()
    for path in path_set:
        parts = path.split("/")
        for i in range(1, len(parts)):
            dir_set.add("/".join(parts[:i]))
    frozen_dir_set = frozenset(dir_set)

    # 2. Identify config files already in the index.
    config_files: list[tuple[int, str]] = [
        (fid, path) for fid, path in all_files
        if fid is not None and _is_config_file(path)
    ]

    if not config_files:
        logger.debug("No config files found in index; skipping config ref resolution.")
        return 0

    # 3. Get unit_id for each config file (from existing DefFacts).
    config_file_ids = [fid for fid, _ in config_files]
    unit_id_map: dict[int, int] = {}
    with db.session() as session:
        for fid in config_file_ids:
            row = session.exec(
                select(DefFact.unit_id)
                .where(DefFact.file_id == fid)
                .limit(1)
            ).first()
            if row is not None:
                unit_id_map[fid] = row

    # 4. Delete existing config_file_ref imports (idempotent).
    with db.session() as session:
        session.execute(
            text("DELETE FROM import_facts WHERE import_kind = :kind"),
            {"kind": "config_file_ref"},
        )
        session.commit()

    # 5. Scan config files and resolve strings to file paths.
    new_imports: list[dict[str, object]] = []
    total_strings_checked = 0
    files_scanned = 0

    for file_id, file_path in config_files:
        unit_id = unit_id_map.get(file_id)
        if unit_id is None:
            logger.debug("Skipping config file %s: no unit_id found.", file_path)
            continue

        # Read file content
        full_path = repo_path / file_path
        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        files_scanned += 1

        # Extract strings
        strings = _extract_strings(content)
        total_strings_checked += len(strings)

        # Track resolved paths per file to avoid duplicate edges
        seen_resolved: set[str] = set()

        for value, line in strings:
            resolved = _try_resolve(value, path_set, frozen_dir_set)
            if resolved is None:
                continue

            # Don't create self-referential edges
            if resolved == file_path:
                continue

            # Skip duplicates within the same config file
            if resolved in seen_resolved:
                continue
            seen_resolved.add(resolved)

            import_uid = _make_import_uid(file_path, resolved, line)
            new_imports.append({
                "import_uid": import_uid,
                "file_id": file_id,
                "unit_id": unit_id,
                "scope_id": None,
                "imported_name": resolved.rsplit("/", 1)[-1],
                "alias": None,
                "source_literal": value,
                "resolved_path": resolved,
                "import_kind": "config_file_ref",
                "certainty": "certain",
                "start_line": line,
                "start_col": 0,
                "end_line": line,
                "end_col": 0,
            })

    # 6. Bulk insert new ImportFacts.
    if new_imports:
        with db.bulk_writer() as writer:
            writer.insert_many(ImportFact, new_imports)

    logger.info(
        "Config file ref resolution: scanned %d files, checked %d strings, "
        "created %d import edges.",
        files_scanned,
        total_strings_checked,
        len(new_imports),
    )

    return len(new_imports)
