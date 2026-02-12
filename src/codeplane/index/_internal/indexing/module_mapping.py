"""Module path ↔ file path mapping utilities.

Shared between the reference resolver and the import graph.
Converts between dotted Python module paths (e.g. ``codeplane.refactor.ops``)
and filesystem paths (e.g. ``src/codeplane/refactor/ops.py``).
"""

from __future__ import annotations


def path_to_module(path: str) -> str | None:
    """Convert a file path to a dotted Python module path.

    Examples:
        >>> path_to_module("src/codeplane/refactor/ops.py")
        'src.codeplane.refactor.ops'
        >>> path_to_module("src/codeplane/__init__.py")
        'src.codeplane'
        >>> path_to_module("README.md")
    """
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


def module_to_candidate_paths(source_literal: str) -> list[str]:
    """Generate candidate module keys for a dotted import path.

    These are keys to match against ``path_to_module()`` output.
    Order is significant — earlier entries are preferred matches.

    Args:
        source_literal: Dotted module name (e.g. ``codeplane.refactor.ops``).

    Returns:
        List of candidate module key strings to look up.
    """
    slash_form = source_literal.replace(".", "/")
    return [
        source_literal,
        slash_form,
        f"{slash_form}/__init__",
        # src/ prefix convention
        f"src.{source_literal}",
        f"src/{slash_form}",
    ]


def resolve_module_to_path(
    source_literal: str,
    module_to_path_map: dict[str, str],
) -> str | None:
    """Resolve a dotted module name to a file path.

    Args:
        source_literal: Dotted module name.
        module_to_path_map: Mapping from ``path_to_module()`` output
            to the original file path.

    Returns:
        File path if found, None otherwise.
    """
    for candidate in module_to_candidate_paths(source_literal):
        if candidate in module_to_path_map:
            return module_to_path_map[candidate]
    return None


def build_module_index(file_paths: list[str]) -> dict[str, str]:
    """Build a mapping from module key → file path.

    This creates the lookup table consumed by ``resolve_module_to_path``.

    Args:
        file_paths: All known file paths in the repository.

    Returns:
        Dict mapping module key (from ``path_to_module``) to original path.
    """
    index: dict[str, str] = {}
    for fp in file_paths:
        module_key = path_to_module(fp)
        if module_key:
            index[module_key] = fp
    return index
