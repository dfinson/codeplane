"""Index helpers — overview builders, tree serializers, change formatters.

These helpers are used by the recon pipeline and semantic diff tool.
The MCP tool handlers that were previously in this module (search, map_repo)
have been removed in v2.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


def _change_to_text(c: Any, depth: int = 0) -> list[str]:
    """Convert a StructuralChange to compact text lines.

    Format: {change} {kind} {name}  {path}:{start}-{end}  Δ{lines}  risk:{risk}  refs:{N}  tests:{list}

    risk:unknown is omitted (it is the default; documented via header comment
    in the caller).
    """
    indent = "  " * depth
    parts = [f"{indent}{c.change} {c.kind} {c.name}"]

    if c.start_line and c.end_line:
        parts.append(f"  {c.path}:{c.start_line}-{c.end_line}")
    else:
        parts.append(f"  {c.path}")

    if c.lines_changed is not None:
        parts.append(f"  Δ{c.lines_changed}")

    if c.behavior_change_risk and c.behavior_change_risk not in ("low", "unknown"):
        parts.append(f"  risk:{c.behavior_change_risk}")

    if c.change == "signature_changed":
        parts.append(f"  old:{c.old_sig or ''}  new:{c.new_sig or ''}")

    if c.change == "renamed" and c.old_name:
        parts.append(f"  was:{c.old_name}")

    if c.impact:
        if c.impact.reference_count:
            parts.append(f"  refs:{c.impact.reference_count}")
        if c.impact.affected_test_files:
            tests = ",".join(c.impact.affected_test_files)
            parts.append(f"  tests:{tests}")

    result_lines = ["".join(parts)]
    if c.nested_changes:
        for nc in c.nested_changes:
            result_lines.extend(_change_to_text(nc, depth + 1))
    return result_lines


def _tree_to_text(
    nodes: list[Any], *, include_line_counts: bool = True, depth: int = 0
) -> list[str]:
    """Convert directory tree nodes to indented text lines.

    Directories: indent + path/ (N files)
    Files: indent + filename  [lines]
    """
    lines: list[str] = []
    indent = "  " * depth
    for node in nodes:
        if node.is_dir:
            lines.append(f"{indent}{node.path}/  ({node.file_count} files)")
            lines.extend(
                _tree_to_text(
                    node.children,
                    include_line_counts=include_line_counts,
                    depth=depth + 1,
                )
            )
        else:
            name = node.path.rsplit("/", 1)[-1] if "/" in node.path else node.path
            if include_line_counts:
                lines.append(f"{indent}{name}  {node.line_count}")
            else:
                lines.append(f"{indent}{name}")
    return lines


def _tree_to_hybrid_text(
    all_paths: list[tuple[str, int | None]],
) -> list[str]:
    """Lossless hybrid tree: indented directories with inline files.

    Combines directory hierarchy (for spatial understanding) with inline
    file listings (for compactness).  Line counts are shown as ``name:N``;
    files with zero/unknown line counts are listed without a suffix
    (the header comment documents this convention).

    Single-child directory chains are collapsed (``src/codeplane/`` instead
    of ``src/`` → ``codeplane/``).

    Example output::

        # files without :N have 0 lines
        src/codeplane/
          cli/ __init__.py | main.py:100 | init.py:50
          core/ errors.py:80 | logging.py:60
        tests/
          cli/ test_main.py:40
        pyproject.toml:200 | README.md:50

    Root-level files appear at indent level 0 at the end.
    """

    # ---- build trie ----
    class _DirNode:
        __slots__ = ("children", "files")

        def __init__(self) -> None:
            self.children: dict[str, _DirNode] = {}
            self.files: list[str] = []

    root = _DirNode()
    for path, lc in all_paths:
        parts = path.split("/")
        if len(parts) == 1:
            # root-level file
            root.files.append(f"{path}:{lc}" if lc else path)
        else:
            node = root
            for p in parts[:-1]:
                if p not in node.children:
                    node.children[p] = _DirNode()
                node = node.children[p]
            fname = parts[-1]
            node.files.append(f"{fname}:{lc}" if lc else fname)

    # ---- render ----
    def _render(node: _DirNode, indent: int) -> list[str]:
        result: list[str] = []
        for name in sorted(node.children):
            child = node.children[name]
            # collapse single-child chains with no files
            chain = [name]
            n = child
            while len(n.children) == 1 and not n.files:
                only = next(iter(n.children))
                chain.append(only)
                n = n.children[only]

            label = "/".join(chain) + "/"
            prefix = "  " * indent
            if n.files:
                result.append(f"{prefix}{label} {' | '.join(n.files)}")
            else:
                result.append(f"{prefix}{label}")
            result.extend(_render(n, indent + 1))
        return result

    lines = ["# files without :N have 0 lines"]
    lines.extend(_render(root, 0))
    if root.files:
        lines.append(" | ".join(root.files))
    return lines


def _map_repo_sections_to_text(
    result: Any,
) -> dict[str, Any]:
    """Convert map_repo result sections to hybrid text format.

    When ``all_paths`` is available on the structure, uses the lossless
    hybrid format (indented directory tree with inline files) which
    gives both spatial hierarchy and compactness.  Every filename and
    non-zero line count is preserved; zero-line files are listed without
    a ``:N`` suffix (documented in a header comment).

    Same data as JSON serializers, but as flat lines.
    """
    sections: dict[str, Any] = {}

    if result.languages:
        sections["languages"] = [
            f"{lang.language} {lang.percentage:.1f}%  {lang.file_count} files"
            for lang in result.languages
        ]

    if result.structure:
        if result.structure.all_paths:
            tree_lines = _tree_to_hybrid_text(result.structure.all_paths)
        else:
            tree_lines = _tree_to_text(result.structure.tree)
        sections["structure"] = {
            "root": result.structure.root,
            "file_count": result.structure.file_count,
            "tree": tree_lines,
        }
        if result.structure.contexts:
            sections["structure"]["contexts"] = result.structure.contexts

    if result.dependencies:
        deps = result.dependencies
        sections["dependencies"] = (
            f"{', '.join(deps.external_modules)}  "
            f"({len(deps.external_modules)} modules, {deps.import_count} imports)"
        )

    if result.test_layout:
        tl = result.test_layout
        sections["test_layout"] = (
            f"{len(tl.test_files)} test files, {tl.test_count} tests\n" + "\n".join(tl.test_files)
        )

    if result.entry_points:
        sections["entry_points"] = [
            f"{ep.kind} {ep.name}  {ep.path}"
            + (f"  ({ep.qualified_name})" if ep.qualified_name else "")
            for ep in result.entry_points
        ]

    if result.public_api:
        sections["public_api"] = [
            f"{sym.name}  {sym.certainty}"
            + (f"  {sym.def_uid}" if sym.def_uid else "")
            + (f"  [{sym.evidence}]" if sym.evidence else "")
            for sym in result.public_api
        ]

    return sections


def _build_overview(result: Any) -> dict[str, Any]:
    """Build the always-fits overview block with counts."""
    overview: dict[str, Any] = {}

    if result.structure:
        overview["file_count"] = result.structure.file_count

    if result.languages:
        overview["languages"] = [
            {"name": lang.language, "count": lang.file_count, "pct": lang.percentage}
            for lang in result.languages
        ]

    if result.dependencies:
        overview["dependency_count"] = len(result.dependencies.external_modules)
        overview["import_count"] = result.dependencies.import_count

    if result.test_layout:
        overview["test_file_count"] = result.test_layout.test_count
        overview["test_count"] = result.test_layout.test_count

    if result.entry_points:
        overview["entry_point_count"] = len(result.entry_points)

    if result.public_api:
        overview["public_api_count"] = len(result.public_api)

    return overview
