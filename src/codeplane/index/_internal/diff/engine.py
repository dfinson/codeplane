"""Layer 2: Pure structural diff engine.

Compares two sets of DefSnapshots (base vs target) for each file and
classifies changes.  No DB or git access — purely functional.

Change types:
- added: new symbol in target
- removed: symbol in base but not target
- signature_changed: same identity key, different signature_hash
- body_changed: same identity key + same sig, but hunk intersects span
- renamed: same kind + same sig_hash, different name (1-to-1 matching)
"""

from __future__ import annotations

import structlog

from codeplane.index._internal.diff.models import (
    ChangedFile,
    DefSnapshot,
    FileChangeInfo,
    RawDiffResult,
    RawStructuralChange,
)

log = structlog.get_logger(__name__)

# Identity key type: (kind, lexical_path)
_IdentityKey = tuple[str, str]


def compute_structural_diff(
    base_facts: dict[str, list[DefSnapshot]],
    target_facts: dict[str, list[DefSnapshot]],
    changed_files: list[ChangedFile],
    hunks: dict[str, list[tuple[int, int]]] | None = None,
) -> RawDiffResult:
    """Compute structural diff across all changed files.

    Args:
        base_facts: file_path -> DefSnapshots at base state
        target_facts: file_path -> DefSnapshots at target state
        changed_files: list of files from the git diff
        hunks: optional file_path -> [(start_line, end_line)] from git diff.
               None means epoch mode (no hunk info available).

    Returns:
        RawDiffResult with classified changes.
    """
    all_changes: list[RawStructuralChange] = []
    non_structural_files: list[FileChangeInfo] = []
    files_analyzed = 0

    for cf in changed_files:
        if not cf.has_grammar:
            non_structural_files.append(
                FileChangeInfo(
                    path=cf.path,
                    status=cf.status,
                    category=_classify_file(cf.path),
                    language=cf.language,
                )
            )
            continue

        files_analyzed += 1
        base = base_facts.get(cf.path, [])
        target = target_facts.get(cf.path, [])
        file_hunks = hunks.get(cf.path, []) if hunks is not None else None

        changes = _diff_file(cf.path, base, target, file_hunks)

        if changes:
            all_changes.extend(changes)
        else:
            non_structural_files.append(
                FileChangeInfo(
                    path=cf.path,
                    status=cf.status,
                    category=_classify_file(cf.path),
                    language=cf.language,
                )
            )

    return RawDiffResult(
        changes=all_changes,
        non_structural_files=non_structural_files,
        files_analyzed=files_analyzed,
    )


def _classify_file(path: str) -> str:
    """Classify a file into a category based on path patterns."""
    from codeplane.core.languages import is_test_file

    lower = path.lower()

    if is_test_file(path):
        return "test"

    # Build/config patterns
    build_names = {
        "makefile",
        "cmakelists.txt",
        "meson.build",
        "build.gradle",
        "build.gradle.kts",
        "pom.xml",
        "build.sbt",
        "cargo.toml",
        "go.mod",
        "package.json",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        ".eslintrc",
        ".prettierrc",
        "tsconfig.json",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
    }
    basename = lower.rsplit("/", 1)[-1]
    if basename in build_names:
        return "build"

    config_exts = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".env", ".json", ".xml"}
    ext = "." + basename.rsplit(".", 1)[-1] if "." in basename else ""
    if ext in config_exts and "/" not in path.replace(".", "", 1):
        # Root-level config files
        return "config"

    doc_patterns = ("/docs/", "/doc/", "readme", "changelog", "license", "contributing")
    doc_exts = {".md", ".rst", ".txt", ".adoc"}
    if any(p in lower for p in doc_patterns) or ext in doc_exts:
        return "docs"

    return "prod"


def _compute_delta_tags(
    change: str,
    old: DefSnapshot | None,
    new: DefSnapshot | None,
    lines_changed: int | None = None,
) -> list[str]:
    """Compute delta tags describing what kind of change occurred.

    Tags are additive and describe observable structural properties.
    They are NOT behavioral assertions — an agent should treat them
    as heuristic signals, not guarantees.
    """
    tags: list[str] = []

    if change == "added":
        return ["symbol_added"]
    if change == "removed":
        return ["symbol_removed"]
    if change == "renamed":
        tags.append("symbol_renamed")
        return tags

    if change == "signature_changed" and old and new:
        old_sig = old.display_name or ""
        new_sig = new.display_name or ""
        # Compare parameter list heuristically
        old_params = _extract_params(old_sig)
        new_params = _extract_params(new_sig)
        if old_params != new_params:
            tags.append("parameters_changed")
        # Compare return type heuristically
        old_ret = _extract_return_type(old_sig)
        new_ret = _extract_return_type(new_sig)
        if old_ret != new_ret:
            tags.append("return_type_changed")
        if not tags:
            tags.append("signature_changed")
        return tags

    if change == "body_changed":
        if lines_changed is not None:
            if lines_changed <= 3:
                tags.append("minor_change")
                # Guard against comment-only misclassification: very small
                # body changes (≤2 lines) often turn out to be comment edits,
                # whitespace, or docstring tweaks rather than logic changes.
                if lines_changed <= 2:
                    tags.append("possibly_comment_or_whitespace")
            elif lines_changed > 20:
                tags.append("major_change")
            else:
                tags.append("body_logic_changed")
        else:
            tags.append("body_logic_changed")
        return tags

    return tags


def _extract_params(sig: str) -> str:
    """Extract parameter portion from a signature string.

    Looks for content between first '(' and its matching ')'.
    Returns empty string if no parens found.
    """
    paren_start = sig.find("(")
    if paren_start == -1:
        return ""
    depth = 0
    for i in range(paren_start, len(sig)):
        if sig[i] == "(":
            depth += 1
        elif sig[i] == ")":
            depth -= 1
            if depth == 0:
                return sig[paren_start : i + 1]
    return sig[paren_start:]


def _extract_return_type(sig: str) -> str:
    """Extract return type annotation from a signature.

    Looks for '->...' after the parameter list (Python style)
    or ':...' before '{' (TypeScript/C-style).
    Returns empty string if none found.
    """
    # Python-style: -> ReturnType
    arrow = sig.rfind("->")
    if arrow != -1:
        return sig[arrow + 2 :].strip().rstrip(":")
    # TypeScript/C-style: ): ReturnType or ): ReturnType {
    paren_close = sig.rfind(")")
    if paren_close != -1 and paren_close + 1 < len(sig):
        rest = sig[paren_close + 1 :].strip().lstrip(":").strip()
        brace = rest.find("{")
        if brace != -1:
            rest = rest[:brace].strip()
        return rest
    return ""


def _diff_file(
    path: str,
    base: list[DefSnapshot],
    target: list[DefSnapshot],
    hunks: list[tuple[int, int]] | None,
) -> list[RawStructuralChange]:
    """Diff a single file's definitions."""
    base_map: dict[_IdentityKey, DefSnapshot] = {(s.kind, s.lexical_path): s for s in base}
    target_map: dict[_IdentityKey, DefSnapshot] = {(s.kind, s.lexical_path): s for s in target}

    changes: list[RawStructuralChange] = []

    # Pass 1: Find removed symbols
    removed_items: list[tuple[_IdentityKey, DefSnapshot]] = []
    for key, snap in base_map.items():
        if key not in target_map:
            removed_items.append((key, snap))

    # Pass 2: Find added symbols
    added_items: list[tuple[_IdentityKey, DefSnapshot]] = []
    for key, snap in target_map.items():
        if key not in base_map:
            added_items.append((key, snap))

    # Pass 3: Detect renames (same kind + same sig_hash, different name)
    renames = _detect_renames(removed_items, added_items)
    renamed_old_keys = {(old.kind, old.lexical_path) for old, _ in renames}
    renamed_new_keys = {(new.kind, new.lexical_path) for _, new in renames}

    # Emit renames
    for old, new in renames:
        changes.append(
            RawStructuralChange(
                path=path,
                kind=new.kind,
                name=new.name,
                qualified_name=new.lexical_path if "." in new.lexical_path else None,
                change="renamed",
                structural_severity="breaking",
                old_sig=old.display_name,
                new_sig=new.display_name,
                is_internal=_is_internal_variable(new, target),
                start_line=new.start_line,
                start_col=new.start_col,
                end_line=new.end_line,
                end_col=new.end_col,
                old_name=old.name,
                delta_tags=_compute_delta_tags("renamed", old, new),
            )
        )

    # Emit remaining removals (not part of renames)
    for key, snap in removed_items:
        if key in renamed_old_keys:
            continue
        changes.append(
            RawStructuralChange(
                path=path,
                kind=snap.kind,
                name=snap.name,
                qualified_name=snap.lexical_path if "." in snap.lexical_path else None,
                change="removed",
                structural_severity="breaking",
                old_sig=snap.display_name,
                new_sig=None,
                is_internal=_is_internal_variable(snap, base),
                start_line=snap.start_line,
                start_col=snap.start_col,
                end_line=snap.end_line,
                end_col=snap.end_col,
                delta_tags=_compute_delta_tags("removed", snap, None),
            )
        )

    # Emit remaining additions (not part of renames)
    for key, snap in added_items:
        if key in renamed_new_keys:
            continue
        changes.append(
            RawStructuralChange(
                path=path,
                kind=snap.kind,
                name=snap.name,
                qualified_name=snap.lexical_path if "." in snap.lexical_path else None,
                change="added",
                structural_severity="non_breaking",
                old_sig=None,
                new_sig=snap.display_name,
                is_internal=_is_internal_variable(snap, target),
                start_line=snap.start_line,
                start_col=snap.start_col,
                end_line=snap.end_line,
                end_col=snap.end_col,
                delta_tags=_compute_delta_tags("added", None, snap),
            )
        )

    # Pass 4: Check modified symbols (same identity, different content)
    for key in base_map:
        if key in target_map:
            old = base_map[key]
            new = target_map[key]

            if old.signature_hash != new.signature_hash:
                changes.append(
                    RawStructuralChange(
                        path=path,
                        kind=new.kind,
                        name=new.name,
                        qualified_name=(new.lexical_path if "." in new.lexical_path else None),
                        change="signature_changed",
                        structural_severity="breaking",
                        old_sig=old.display_name,
                        new_sig=new.display_name,
                        is_internal=_is_internal_variable(new, target),
                        start_line=new.start_line,
                        start_col=new.start_col,
                        end_line=new.end_line,
                        end_col=new.end_col,
                        delta_tags=_compute_delta_tags("signature_changed", old, new),
                    )
                )
            elif _intersects_hunks(new.start_line, new.end_line, hunks):
                # Same signature but source changed in span
                lc = _count_changed_lines(new.start_line, new.end_line, hunks)
                changes.append(
                    RawStructuralChange(
                        path=path,
                        kind=new.kind,
                        name=new.name,
                        qualified_name=(new.lexical_path if "." in new.lexical_path else None),
                        change="body_changed",
                        structural_severity="non_breaking",
                        old_sig=old.display_name,
                        new_sig=new.display_name,
                        is_internal=_is_internal_variable(new, target),
                        start_line=new.start_line,
                        start_col=new.start_col,
                        end_line=new.end_line,
                        end_col=new.end_col,
                        lines_changed=lc,
                        delta_tags=_compute_delta_tags("body_changed", old, new, lc),
                    )
                )

    return changes


def _count_changed_lines(
    start: int,
    end: int,
    hunks: list[tuple[int, int]] | None,
) -> int | None:
    """Count lines within [start, end] that are covered by diff hunks."""
    if hunks is None:
        return None
    total = 0
    for h_start, h_end in hunks:
        overlap_start = max(start, h_start)
        overlap_end = min(end, h_end)
        if overlap_start <= overlap_end:
            total += overlap_end - overlap_start + 1
    return total if total > 0 else None


def _detect_renames(
    removed: list[tuple[_IdentityKey, DefSnapshot]],
    added: list[tuple[_IdentityKey, DefSnapshot]],
) -> list[tuple[DefSnapshot, DefSnapshot]]:
    """Detect renames: same kind + same signature_hash, different name."""
    renames: list[tuple[DefSnapshot, DefSnapshot]] = []

    # Build lookup by (kind, signature_hash) for removed symbols
    removed_by_sig: dict[tuple[str, str], list[DefSnapshot]] = {}
    for _key, snap in removed:
        if snap.signature_hash:
            sig_key = (snap.kind, snap.signature_hash)
            removed_by_sig.setdefault(sig_key, []).append(snap)

    used_removed: set[str] = set()  # Track by lexical_path

    for _key, new_snap in added:
        if not new_snap.signature_hash:
            continue
        sig_key = (new_snap.kind, new_snap.signature_hash)
        candidates = removed_by_sig.get(sig_key, [])
        for old_snap in candidates:
            if old_snap.lexical_path not in used_removed:
                renames.append((old_snap, new_snap))
                used_removed.add(old_snap.lexical_path)
                break

    return renames


def _intersects_hunks(
    start: int,
    end: int,
    hunks: list[tuple[int, int]] | None,
) -> bool:
    """Check if a symbol span [start, end] intersects any diff hunk.

    If hunks is None (epoch mode), treat everything as potentially changed.
    If hunks is empty list, nothing changed in this file.
    """
    if hunks is None:
        return True
    return any(h_start <= end and h_end >= start for h_start, h_end in hunks)


def _is_internal_variable(snap: DefSnapshot, all_snaps: list[DefSnapshot]) -> bool:
    """Check if a variable is local to a function/method (not public)."""
    if snap.kind != "variable":
        return False

    for other in all_snaps:
        if (
            other.kind in ("function", "method")
            and other is not snap
            and other.start_line <= snap.start_line <= other.end_line
        ):
            return True
    return False
