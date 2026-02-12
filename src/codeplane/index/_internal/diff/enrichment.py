"""Layer 3: Enrich structural diff with reference, import, and test data.

Each enrichment is independently fail-open: if a query fails, that
enrichment is skipped but the rest of the result is preserved.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlmodel import select

from codeplane.index._internal.diff.models import (
    ImpactInfo,
    RawDiffResult,
    RefTierBreakdown,
    SemanticDiffResult,
    StructuralChange,
)

if TYPE_CHECKING:
    from sqlmodel import Session

log = structlog.get_logger(__name__)


def enrich_diff(
    raw: RawDiffResult,
    session: Session,
    repo_root: Path,
) -> SemanticDiffResult:
    """Enrich raw structural changes with impact information.

    Each enrichment is independently fail-open.
    """
    enriched: list[StructuralChange] = []

    for rc in raw.changes:
        if rc.is_internal:
            # Skip internal variables (local to a function)
            continue

        change = _enrich_single_change(rc, session, repo_root)
        enriched.append(change)

    # Build nested structure (methods inside classes)
    enriched = _nest_changes(enriched)

    summary = _build_summary(enriched)
    breaking = _build_breaking_summary(enriched)

    return SemanticDiffResult(
        structural_changes=enriched,
        non_structural_changes=raw.non_structural_files,
        summary=summary,
        breaking_summary=breaking,
        files_analyzed=raw.files_analyzed,
        base_description="",  # Filled by caller
        target_description="",  # Filled by caller
    )


def _enrich_single_change(
    rc: object,  # RawStructuralChange
    session: Session,
    _repo_root: Path,
) -> StructuralChange:
    """Enrich a single raw change with impact info."""
    from codeplane.index._internal.diff.models import RawStructuralChange

    assert isinstance(rc, RawStructuralChange)

    impact: ImpactInfo | None = None

    # Try each enrichment independently (fail-open)
    ref_tiers: RefTierBreakdown | None = None
    ref_files: list[str] | None = None
    ref_basis: str = "unknown"
    imp_files: list[str] | None = None
    test_files: list[str] | None = None
    entity_id: str | None = None
    visibility: str | None = None
    is_static: bool | None = None

    try:
        ref_tiers, ref_files, ref_basis, entity_id = _enrich_references(
            session, rc.path, rc.kind, rc.name
        )
    except Exception:
        log.debug("enrich_refs_failed", path=rc.path, name=rc.name, exc_info=True)

    try:
        imp_files = _enrich_imports(session, rc.name)
    except Exception:
        log.debug("enrich_imports_failed", path=rc.path, name=rc.name, exc_info=True)

    try:
        test_files = _enrich_test_files(ref_files, imp_files)
    except Exception:
        log.debug("enrich_tests_failed", path=rc.path, name=rc.name, exc_info=True)

    try:
        visibility, is_static = _enrich_visibility(session, rc.path, rc.kind, rc.name)
    except Exception:
        log.debug("enrich_visibility_failed", path=rc.path, name=rc.name, exc_info=True)

    ref_count = ref_tiers.total if ref_tiers else None
    has_any = ref_count is not None or ref_files or imp_files or test_files or visibility

    if has_any:
        impact = ImpactInfo(
            reference_count=ref_count,
            ref_tiers=ref_tiers,
            reference_basis=ref_basis,
            referencing_files=ref_files,
            importing_files=imp_files,
            affected_test_files=test_files,
            confidence=_get_confidence(rc.path),
            visibility=visibility,
            is_static=is_static,
        )

    # Determine behavior_change_risk from change type
    behavior_change_risk = _assess_behavior_risk(rc.change, ref_count)

    return StructuralChange(
        path=rc.path,
        kind=rc.kind,
        name=rc.name,
        qualified_name=rc.qualified_name,
        change=rc.change,
        structural_severity=rc.structural_severity,
        behavior_change_risk=behavior_change_risk,
        old_sig=rc.old_sig,
        new_sig=rc.new_sig,
        impact=impact,
        entity_id=entity_id,
        start_line=rc.start_line,
        start_col=rc.start_col,
        end_line=rc.end_line,
        end_col=rc.end_col,
        lines_changed=rc.lines_changed,
    )


def _assess_behavior_risk(change: str, ref_count: int | None) -> str:
    """Assess behavior change risk based on change type and blast radius.

    This is an honest heuristic — it cannot detect actual behavioral changes,
    only estimate likelihood based on structural signals.
    """
    if change in ("added",):
        return "low"
    if change in ("removed", "renamed"):
        return "high"
    if change == "signature_changed":
        return "high"
    if change == "body_changed":
        # Body changes are unknown by default — we can't tell if the behavior
        # actually changed without deeper analysis (delta tags can help)
        if ref_count is not None and ref_count > 10:
            return "medium"  # High blast radius body change
        return "unknown"
    return "unknown"


def _enrich_references(
    session: Session,
    file_path: str,
    kind: str,
    name: str,
) -> tuple[RefTierBreakdown | None, list[str] | None, str, str | None]:
    """Find references to a symbol via DefFact + RefFact.

    Returns:
        (tier_breakdown, referencing_files, reference_basis, entity_id)
    """
    from codeplane.index.models import DefFact, File, RefFact

    # Find the DefFact for this symbol
    stmt = (
        select(DefFact)
        .join(File, DefFact.file_id == File.id)  # type: ignore[arg-type]
        .where(File.path == file_path, DefFact.kind == kind, DefFact.name == name)
    )
    def_fact = session.exec(stmt).first()
    if not def_fact:
        return None, None, "unknown", None

    entity_id = def_fact.def_uid

    # Find RefFacts pointing to this def_uid
    ref_stmt = select(RefFact).where(RefFact.target_def_uid == def_fact.def_uid)
    refs = session.exec(ref_stmt).all()
    if not refs:
        return RefTierBreakdown(), [], "ref_facts_resolved", entity_id

    # Build tier breakdown
    tiers = RefTierBreakdown()
    for r in refs:
        tier = r.ref_tier
        if tier == "proven":
            tiers.proven += 1
        elif tier == "strong":
            tiers.strong += 1
        elif tier == "anchored":
            tiers.anchored += 1
        else:
            tiers.unknown += 1

    # Determine basis honesty
    if tiers.proven + tiers.strong > 0:
        basis = "ref_facts_resolved"
    elif tiers.anchored + tiers.unknown > 0:
        basis = "ref_facts_partial"
    else:
        basis = "unknown"

    # Get unique file paths for referencing files
    ref_file_ids = {r.file_id for r in refs}
    file_stmt = select(File.path).where(File.id.in_(list(ref_file_ids)))  # type: ignore[union-attr]
    file_paths = list(session.exec(file_stmt).all())

    return tiers, file_paths, basis, entity_id


def _enrich_imports(
    session: Session,
    name: str,
) -> list[str] | None:
    """Find files that import a symbol by name."""
    from codeplane.index.models import File, ImportFact

    stmt = (
        select(File.path)
        .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
        .where(ImportFact.imported_name == name)
    )
    paths = list(session.exec(stmt).all())
    return paths if paths else None


def _enrich_visibility(
    session: Session,
    file_path: str,
    _kind: str,
    name: str,
) -> tuple[str | None, bool | None]:
    """Look up visibility and static status from TypeMemberFact.

    Returns (visibility, is_static) or (None, None) if not found.
    """
    from codeplane.index.models import File, TypeMemberFact

    stmt = (
        select(TypeMemberFact)
        .join(File, TypeMemberFact.file_id == File.id)  # type: ignore[arg-type]
        .where(
            File.path == file_path,
            TypeMemberFact.member_name == name,
        )
    )
    member = session.exec(stmt).first()
    if not member:
        return None, None

    return member.visibility, member.is_static


def _enrich_test_files(
    referencing_files: list[str] | None,
    importing_files: list[str] | None,
) -> list[str] | None:
    """Find test files among referencing and importing files."""
    from codeplane.core.languages import is_test_file

    all_files: set[str] = set()
    if referencing_files:
        all_files.update(referencing_files)
    if importing_files:
        all_files.update(importing_files)

    if not all_files:
        return None

    test_files = sorted({f for f in all_files if is_test_file(f)})
    return test_files if test_files else None


def _get_confidence(file_path: str) -> str:
    """Determine confidence level based on language support."""
    from codeplane.core.languages import detect_language_family, has_grammar

    lang = detect_language_family(file_path)
    if lang and has_grammar(lang):
        return "high"
    return "low"


def _nest_changes(changes: list[StructuralChange]) -> list[StructuralChange]:
    """Group method changes under their parent class change."""
    class_changes: dict[tuple[str, str], StructuralChange] = {}
    method_changes: list[StructuralChange] = []
    other_changes: list[StructuralChange] = []

    for c in changes:
        if c.kind == "class":
            class_changes[(c.path, c.name)] = c
        elif c.kind == "method" and c.qualified_name and "." in c.qualified_name:
            method_changes.append(c)
        else:
            other_changes.append(c)

    for mc in method_changes:
        assert mc.qualified_name is not None
        class_name = mc.qualified_name.rsplit(".", 1)[0]
        parent_key = (mc.path, class_name)
        if parent_key in class_changes:
            parent = class_changes[parent_key]
            if parent.nested_changes is None:
                parent.nested_changes = []
            parent.nested_changes.append(mc)
        else:
            other_changes.append(mc)

    return list(class_changes.values()) + other_changes


def _build_summary(changes: list[StructuralChange]) -> str:
    """Build a human-readable summary of changes."""
    if not changes:
        return "No changes detected"

    counts: dict[str, int] = {}
    for c in changes:
        key = c.change.replace("_", " ")
        counts[key] = counts.get(key, 0) + 1

    parts = [f"{count} {kind}" for kind, count in sorted(counts.items())]
    return ", ".join(parts)


def _build_breaking_summary(changes: list[StructuralChange]) -> str | None:
    """Build a summary of breaking changes, or None if none."""
    breaking = [c for c in changes if c.structural_severity == "breaking"]
    if not breaking:
        return None

    n = len(breaking)
    names = ", ".join(c.name for c in breaking[:5])
    suffix = f" (and {n - 5} more)" if n > 5 else ""
    return f"{n} breaking change{'s' if n != 1 else ''}: {names}{suffix}"
