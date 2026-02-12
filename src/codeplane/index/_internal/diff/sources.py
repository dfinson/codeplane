"""Layer 1: Produce DefSnapshot lists from any state.

Three source strategies:
- snapshots_from_index: reads current DefFacts from the DB
- snapshots_from_epoch: reads DefSnapshotRecords for a published epoch
- snapshots_from_blob: parses a git blob with tree-sitter on the fly
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import structlog
from sqlmodel import select

from codeplane.index._internal.diff.models import DefSnapshot
from codeplane.index._internal.parsing.treesitter import SyntacticSymbol

if TYPE_CHECKING:
    from sqlmodel import Session


log = structlog.get_logger(__name__)


# ============================================================================
# Source 1: Current index state
# ============================================================================


def snapshots_from_index(session: Session, file_path: str) -> list[DefSnapshot]:
    """Read current DefFacts for a file from the index."""
    from codeplane.index.models import DefFact, File

    stmt = (
        select(DefFact)
        .join(File, DefFact.file_id == File.id)  # type: ignore[arg-type]
        .where(File.path == file_path)
    )
    rows = session.exec(stmt).all()
    return [
        DefSnapshot(
            kind=r.kind,
            name=r.name,
            lexical_path=r.lexical_path,
            signature_hash=r.signature_hash,
            display_name=r.display_name,
            start_line=r.start_line,
            end_line=r.end_line,
        )
        for r in rows
    ]


# ============================================================================
# Source 2: Published epoch snapshots
# ============================================================================


def snapshots_from_epoch(session: Session, epoch_id: int, file_path: str) -> list[DefSnapshot]:
    """Read DefSnapshotRecords for a file at a given epoch.

    Reconstructs the file state at `epoch_id` by selecting the latest
    snapshot per symbol with epoch_id <= the requested epoch. This is
    necessary because publish_epoch() only writes rows for files touched
    in each epoch — unchanged files have no rows for that epoch.
    """
    from sqlalchemy import func

    from codeplane.index.models import DefSnapshotRecord

    # Subquery: max epoch_id per (file_path, lexical_path) where epoch_id <= target
    latest = (
        select(
            DefSnapshotRecord.lexical_path,
            func.max(DefSnapshotRecord.epoch_id).label("max_epoch"),
        )
        .where(
            DefSnapshotRecord.file_path == file_path,
            DefSnapshotRecord.epoch_id <= epoch_id,
        )
        .group_by(DefSnapshotRecord.lexical_path)
        .subquery()
    )

    stmt = (
        select(DefSnapshotRecord)
        .join(
            latest,
            (DefSnapshotRecord.lexical_path == latest.c.lexical_path)  # type: ignore[arg-type]
            & (DefSnapshotRecord.epoch_id == latest.c.max_epoch),
        )
        .where(DefSnapshotRecord.file_path == file_path)
    )
    rows = session.exec(stmt).all()
    return [
        DefSnapshot(
            kind=r.kind,
            name=r.name,
            lexical_path=r.lexical_path,
            signature_hash=r.signature_hash,
            display_name=r.display_name,
            start_line=r.start_line or 0,
            end_line=r.end_line or 0,
        )
        for r in rows
    ]


# ============================================================================
# Source 3: Parse git blob on the fly
# ============================================================================


def snapshots_from_blob(
    repo: object,  # pygit2.Repository
    commit: object,  # pygit2.Commit
    file_path: str,
) -> list[DefSnapshot]:
    """Parse a git blob with tree-sitter to extract DefSnapshots.

    This is used for the base side of a git diff when the index may not
    have the old state.
    """
    import pygit2

    from codeplane.core.languages import detect_language_family, has_grammar
    from codeplane.index._internal.parsing.treesitter import TreeSitterParser

    assert isinstance(repo, pygit2.Repository)
    assert isinstance(commit, pygit2.Commit)

    try:
        tree_entry = commit.tree[file_path]
    except KeyError:
        return []

    blob = repo[tree_entry.id]
    if not isinstance(blob, pygit2.Blob):
        return []

    lang = detect_language_family(file_path)
    if not lang or not has_grammar(lang):
        return []

    source = blob.data
    if isinstance(source, memoryview):
        source = bytes(source)

    parser = TreeSitterParser()
    try:
        # parse() takes a Path (for lang detection) and optional content as bytes
        from pathlib import Path as _Path

        result = parser.parse(_Path(file_path), content=source)
        symbols = parser.extract_symbols(result)
    except Exception:
        log.warning("blob_parse_failed", path=file_path)
        return []

    snapshots: list[DefSnapshot] = []
    for sym in symbols:
        lp = _compute_lexical_path(sym, symbols)
        sig_hash = _compute_signature_hash(sym)
        snapshots.append(
            DefSnapshot(
                kind=sym.kind,
                name=sym.name,
                lexical_path=lp,
                signature_hash=sig_hash,
                display_name=sym.signature or sym.name,
                start_line=sym.line,
                end_line=sym.end_line,
            )
        )
    return snapshots


def _compute_lexical_path(sym: SyntacticSymbol, all_symbols: list[SyntacticSymbol]) -> str:
    """Compute the lexical path for a symbol (same logic as structural.py)."""
    if sym.parent_name:
        return f"{sym.parent_name}.{sym.name}"

    if sym.kind in ("class", "function"):
        return sym.name

    for other in all_symbols:
        if other.kind == "class" and (
            other.line <= sym.line <= other.end_line and other.column <= sym.column
        ):
            return f"{other.name}.{sym.name}"

    return sym.name


def _compute_signature_hash(sym: SyntacticSymbol) -> str | None:
    """Compute a hash of the symbol's signature for change detection.

    Must use the same hashing/truncation scheme as the structural indexer
    (see structural.py: SHA-256 hexdigest truncated to 8 chars) so blob
    snapshots are directly comparable to DB snapshots.
    """
    if sym.signature:
        return hashlib.sha256(sym.signature.encode()).hexdigest()[:8]
    return None
