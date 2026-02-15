"""Module path ↔ file path mapping utilities.

Shared between the reference resolver and the import graph.
Converts between dotted module paths (e.g. ``codeplane.refactor.ops``)
and filesystem paths (e.g. ``src/codeplane/refactor/ops.py``).

Supports all programming languages with import systems.
Data/doc/config formats (markdown, json, yaml, etc.) are excluded
since they cannot participate in import graphs.
"""

from __future__ import annotations

from codeplane.core.languages import ALL_LANGUAGES

# Language names that have import systems (can be imported by other files).
# Data/doc/config formats (markdown, json, yaml, toml, xml, html, css, etc.)
# are intentionally excluded — they don't participate in import graphs.
_IMPORTABLE_LANGUAGE_NAMES: frozenset[str] = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "go",
        "rust",
        "java",
        "kotlin",
        "c_sharp",
        "scala",
        "php",
        "ruby",
        "c_cpp",
        "swift",
        "elixir",
        "haskell",
        "ocaml",
        "lua",
        "julia",
        "erlang",
        "shell",
        "r",
        "zig",
        "nim",
        "d",
        "ada",
        "fortran",
        "pascal",
        "gleam",
        "vlang",
        "odin",
        "nix",
        "reason",
        "elm",
    }
)

# Build extension set from importable languages only
_KNOWN_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    ext
    for lang in ALL_LANGUAGES
    if lang.name in _IMPORTABLE_LANGUAGE_NAMES
    for ext in lang.extensions
)


def path_to_module(path: str) -> str | None:
    """Convert a file path to a dotted module path.

    Only resolves files with importable programming language extensions.
    Data/doc formats (markdown, json, yaml, etc.) return None.
    For Python, also handles ``__init__.py`` → package.

    Examples:
        >>> path_to_module("src/codeplane/refactor/ops.py")
        'src.codeplane.refactor.ops'
        >>> path_to_module("src/codeplane/__init__.py")
        'src.codeplane'
        >>> path_to_module("src/utils/helper.ts")
        'src.utils.helper'
        >>> path_to_module("README.md")
        >>> path_to_module("Makefile")
    """
    # Find the extension
    dot_pos = path.rfind(".")
    if dot_pos < 0:
        return None

    ext = path[dot_pos:]  # e.g. ".py", ".ts", ".go"
    if ext not in _KNOWN_SOURCE_EXTENSIONS:
        return None

    # Remove the extension
    module = path[:dot_pos]

    # Handle Python __init__.py → package
    if ext == ".py" and module.endswith("/__init__"):
        module = module[:-9]  # strip /__init__

    # Convert path separators to dots
    module = module.replace("/", ".").replace("\\", ".")

    # Remove leading dots
    module = module.lstrip(".")

    return module


def module_to_candidate_paths(source_literal: str) -> list[str]:
    """Generate candidate module keys for a dotted import path.

    These are keys to match against ``path_to_module()`` output.
    ``path_to_module`` always returns dot-separated keys (e.g.
    ``src.codeplane.refactor.ops``), so all candidates must be
    dot-separated too.

    Args:
        source_literal: Dotted module name (e.g. ``codeplane.refactor.ops``).

    Returns:
        List of candidate module key strings to look up.
    """
    return [
        source_literal,
        # src/ prefix convention (path_to_module keeps the src. prefix)
        f"src.{source_literal}",
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
