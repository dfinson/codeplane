"""Import path resolution — resolves source_literal to repo-relative file paths.

This module provides deterministic resolution of import source literals
to the file paths they reference, for ALL languages with import systems.

Resolution strategies by language:

**Declaration-based** (Java, Kotlin, Scala, C#, Go, Haskell, Elixir,
Julia, Ruby, PHP):
  source_literal is matched against ``File.declared_module`` values.
  e.g. ``import cats.effect.IO`` → source_literal ``cats.effect.IO``
  matches file with ``declared_module = 'cats.effect'``.

**Path-based** (Python):
  source_literal is converted via ``module_to_candidate_paths()``
  and matched against ``path_to_module()`` output.

**Relative-path-based** (JS/TS, C/C++):
  source_literal is a relative path (e.g. ``./utils``, ``../models/user``)
  resolved from the importing file's directory with extension probing.

**Config-augmented** (Go → go.mod, Rust → Cargo.toml):
  Package declaration from tree-sitter is augmented with config file
  context to produce the full declared_module.

All resolution runs at **index time** and the result is stored in
``ImportFact.resolved_path``, making query-time matching trivial.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

# Type alias for the file reader callable
_ReadFileFn = Callable[[str], "str | None"]


# ---------------------------------------------------------------------------
# Go: go.mod resolution
# ---------------------------------------------------------------------------

_GO_MOD_MODULE_RE = re.compile(r"^module\s+(\S+)", re.MULTILINE)


def parse_go_mod(go_mod_text: str) -> str | None:
    """Extract the module path from a go.mod file.

    >>> parse_go_mod('module github.com/user/repo\\n\\ngo 1.21\\n')
    'github.com/user/repo'
    """
    m = _GO_MOD_MODULE_RE.search(go_mod_text)
    return m.group(1) if m else None


def resolve_go_module(
    file_path: str,
    _short_package: str | None,
    go_mod_path: str,
    go_mod_module: str,
) -> str | None:
    """Resolve a Go file's full import path.

    Args:
        file_path: Relative path of the .go file (e.g. 'pkg/auth/token.go').
        _short_package: The ``package`` declaration (e.g. 'auth'). May be None.
        go_mod_path: Relative path to the go.mod file.
        go_mod_module: Module path from go.mod (e.g. 'github.com/user/repo').

    Returns:
        Full import path (e.g. 'github.com/user/repo/pkg/auth').
    """
    go_mod_dir = str(PurePosixPath(go_mod_path).parent)
    if go_mod_dir == ".":
        go_mod_dir = ""

    file_dir = str(PurePosixPath(file_path).parent)
    if go_mod_dir and file_dir.startswith(go_mod_dir + "/"):
        rel_dir = file_dir[len(go_mod_dir) + 1 :]
    elif go_mod_dir:
        return None
    else:
        rel_dir = file_dir

    if rel_dir and rel_dir != ".":
        return f"{go_mod_module}/{rel_dir}"
    return go_mod_module


# ---------------------------------------------------------------------------
# Rust: Cargo.toml resolution
# ---------------------------------------------------------------------------

_CARGO_NAME_RE = re.compile(r'^\[package\].*?^name\s*=\s*"([^"]+)"', re.MULTILINE | re.DOTALL)


def parse_cargo_toml(cargo_text: str) -> str | None:
    """Extract the crate name from a Cargo.toml file.

    >>> parse_cargo_toml('[package]\\nname = "my_crate"\\nversion = "0.1.0"')
    'my_crate'
    """
    m = _CARGO_NAME_RE.search(cargo_text)
    return m.group(1) if m else None


def resolve_rust_module(
    file_path: str,
    cargo_toml_path: str,
    crate_name: str,
) -> str | None:
    """Resolve a Rust file's module path.

    Returns:
        Crate-qualified module path (e.g. 'my_crate::auth::token').
    """
    cargo_dir = str(PurePosixPath(cargo_toml_path).parent)
    if cargo_dir == ".":
        cargo_dir = ""

    fp = PurePosixPath(file_path)
    file_dir = str(fp.parent)
    file_stem = fp.stem

    if cargo_dir and file_dir.startswith(cargo_dir + "/"):
        rel = file_dir[len(cargo_dir) + 1 :]
    elif cargo_dir:
        return None
    else:
        rel = file_dir

    if rel.startswith("src/"):
        rel = rel[4:]
    elif rel == "src":
        rel = ""

    parts = [crate_name]
    if rel:
        parts.extend(rel.split("/"))
    if file_stem not in ("lib", "main", "mod"):
        parts.append(file_stem)

    return "::".join(parts)


# ---------------------------------------------------------------------------
# Config file discovery cache (Go, Rust declared_module augmentation)
# ---------------------------------------------------------------------------


class ConfigResolver:
    """Caches parsed config files for a repo and resolves module identities.

    Used during indexing to augment ``declared_module`` for Go and Rust files.
    """

    def __init__(self, repo_root: str, file_paths: list[str]) -> None:
        self._repo_root = repo_root
        self._go_mods: dict[str, str] | None = None
        self._cargo_tomls: dict[str, str] | None = None
        self._file_paths = file_paths

    def _discover_go_mods(self, read_file: _ReadFileFn) -> dict[str, str]:
        """Find and parse all go.mod files."""
        if self._go_mods is not None:
            return self._go_mods
        self._go_mods = {}
        for fp in self._file_paths:
            if PurePosixPath(fp).name == "go.mod":
                text = read_file(fp)
                if text is not None:
                    mod = parse_go_mod(text)
                    if mod:
                        self._go_mods[fp] = mod
                        logger.debug("go.mod: %s -> %s", fp, mod)
        return self._go_mods

    def _discover_cargo_tomls(self, read_file: _ReadFileFn) -> dict[str, str]:
        """Find and parse all Cargo.toml files."""
        if self._cargo_tomls is not None:
            return self._cargo_tomls
        self._cargo_tomls = {}
        for fp in self._file_paths:
            if PurePosixPath(fp).name == "Cargo.toml":
                text = read_file(fp)
                if text is not None:
                    crate = parse_cargo_toml(text)
                    if crate:
                        self._cargo_tomls[fp] = crate
                        logger.debug("Cargo.toml: %s -> %s", fp, crate)
        return self._cargo_tomls

    def _find_nearest_config(
        self, file_path: str, configs: dict[str, str]
    ) -> tuple[str, str] | None:
        """Find the nearest config file by directory nesting."""
        file_dir = str(PurePosixPath(file_path).parent)
        best: tuple[str, str] | None = None
        best_depth = -1
        for cfg_path, value in configs.items():
            cfg_dir = str(PurePosixPath(cfg_path).parent)
            if cfg_dir == ".":
                cfg_dir = ""
            if not cfg_dir or file_dir == cfg_dir or file_dir.startswith(cfg_dir + "/"):
                depth = cfg_dir.count("/") + (1 if cfg_dir else 0)
                if depth > best_depth:
                    best = (cfg_path, value)
                    best_depth = depth
        return best

    def resolve(
        self,
        file_path: str,
        language: str | None,
        short_package: str | None,
        read_file: _ReadFileFn | None = None,
    ) -> str | None:
        """Resolve declared_module for Go and Rust files."""
        if language == "go" and read_file is not None:
            go_mods = self._discover_go_mods(read_file)
            nearest = self._find_nearest_config(file_path, go_mods)
            if nearest:
                cfg_path, module_root = nearest
                return resolve_go_module(file_path, short_package, cfg_path, module_root)
        elif language == "rust" and read_file is not None:
            cargo_tomls = self._discover_cargo_tomls(read_file)
            nearest = self._find_nearest_config(file_path, cargo_tomls)
            if nearest:
                cfg_path, crate_name = nearest
                return resolve_rust_module(file_path, cfg_path, crate_name)
        return None


# ---------------------------------------------------------------------------
# Import path resolver — resolves source_literal → file path
# ---------------------------------------------------------------------------

# JS/TS extensions to probe when resolving relative imports
_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts")
_JS_INDEX_NAMES = tuple(f"/index{ext}" for ext in _JS_EXTENSIONS)

# C/C++ extensions to probe
_C_EXTENSIONS = (".h", ".hpp", ".hxx", ".c", ".cpp", ".cxx", ".cc")


class ImportPathResolver:
    """Resolves import source_literal values to repo-relative file paths.

    Operates at index time on the full set of ExtractionResults, using:
    - File path index for extension probing (JS/TS, C/C++)
    - declared_module values for declaration-based matching
    - path_to_module output for Python

    Usage::

        resolver = ImportPathResolver(all_file_paths, declared_modules)
        resolved = resolver.resolve(source_literal, import_kind, importer_path)
    """

    def __init__(
        self,
        all_file_paths: list[str],
        declared_modules: dict[str, str],  # file_path -> declared_module
    ) -> None:
        # Set of all known file paths for existence checks
        self._all_paths: set[str] = set(all_file_paths)

        # declared_module -> list of file_paths (multiple files can share a module)
        self._module_to_paths: dict[str, list[str]] = {}
        for fp, mod in declared_modules.items():
            self._module_to_paths.setdefault(mod, []).append(fp)

        # Python: path_to_module output -> file_path
        from codeplane.index._internal.indexing.module_mapping import (
            path_to_module,
        )

        self._python_module_to_path: dict[str, str] = {}
        for fp in all_file_paths:
            py_mod = path_to_module(fp)
            if py_mod:
                self._python_module_to_path[py_mod] = fp

    def resolve(
        self,
        source_literal: str | None,
        import_kind: str,
        importer_path: str,
    ) -> str | None:
        """Resolve a single import's source_literal to a file path.

        Args:
            source_literal: The import source string (e.g. 'cats.effect.IO',
                './utils', 'os.path').
            import_kind: The import classification (e.g. 'python_import',
                'js_import', 'java_import', 'c_include').
            importer_path: The file that contains this import statement.

        Returns:
            Repo-relative file path, or None if unresolvable.
        """
        if not source_literal:
            return None

        # Dispatch by import_kind
        if import_kind in ("python_import", "python_from"):
            return self._resolve_python(source_literal)
        elif import_kind in ("js_import", "js_require", "js_dynamic_import"):
            return self._resolve_js(source_literal, importer_path)
        elif import_kind == "c_include":
            return self._resolve_c(source_literal, importer_path)
        else:
            # All other languages: declaration-based resolution
            return self._resolve_declaration_based(source_literal, import_kind, importer_path)

    # ----- Python -----

    def _resolve_python(self, source_literal: str) -> str | None:
        """Resolve Python dotted import to file path.

        e.g. 'codeplane.refactor.ops' → 'src/codeplane/refactor/ops.py'
        """
        from codeplane.index._internal.indexing.module_mapping import (
            module_to_candidate_paths,
        )

        for candidate in module_to_candidate_paths(source_literal):
            if candidate in self._python_module_to_path:
                return self._python_module_to_path[candidate]
        return None

    # ----- JS/TS relative path resolution -----

    def _resolve_js(self, source_literal: str, importer_path: str) -> str | None:
        """Resolve JS/TS import source to file path.

        Handles:
        - Relative: './utils' → probe extensions + /index variants
        - Bare specifiers: 'react' → skip (external package)
        """
        if not source_literal.startswith("."):
            # Bare specifier (npm package) — cannot resolve to repo file
            return None

        importer_dir = str(PurePosixPath(importer_path).parent)
        raw = importer_dir + "/" + source_literal
        resolved = _normalize_path(raw)

        # 1. Exact match (already has extension)
        if resolved in self._all_paths:
            return resolved

        # 2. Probe extensions
        for ext in _JS_EXTENSIONS:
            candidate = resolved + ext
            if candidate in self._all_paths:
                return candidate

        # 3. Probe as directory with index file
        for idx in _JS_INDEX_NAMES:
            candidate = resolved + idx
            if candidate in self._all_paths:
                return candidate

        return None

    # ----- C/C++ include resolution -----

    def _resolve_c(self, source_literal: str, importer_path: str) -> str | None:
        """Resolve C/C++ #include to file path.

        Handles:
        - Quoted includes: "header.h" → resolve relative to importer
        - System includes: <stdio.h> → skip (system header)
        """
        importer_dir = str(PurePosixPath(importer_path).parent)
        resolved = _normalize_path(importer_dir + "/" + source_literal)

        # Exact match relative to importer
        if resolved in self._all_paths:
            return resolved

        # Try from repo root (for project-root-relative includes)
        if source_literal in self._all_paths:
            return source_literal

        return None

    # ----- Declaration-based (Java, Kotlin, Scala, C#, Go, etc.) -----

    def _resolve_declaration_based(
        self,
        source_literal: str,
        import_kind: str,
        importer_path: str,
    ) -> str | None:
        """Resolve declaration-based imports by matching against declared_module.

        Strategy:
        1. Exact match: source_literal == declared_module
        2. Prefix match: source_literal starts with declared_module + separator
           (import of a symbol within a declared module)
        3. For Ruby require_relative: resolve as relative path

        The separator depends on the language:
        - Java/Kotlin/Scala/C#/PHP/Elixir/Haskell/Julia: '.'
        - Rust: '::'
        - Go: '/'
        - Ruby: '::' or '/'
        """
        # Ruby require_relative uses path resolution
        if import_kind == "ruby_require_relative":
            return self._resolve_ruby_relative(source_literal, importer_path)

        # Exact declared_module match
        if source_literal in self._module_to_paths:
            paths = self._module_to_paths[source_literal]
            return paths[0] if paths else None

        # Determine separator for prefix matching
        sep = self._separator_for_kind(import_kind)

        # Prefix match: 'cats.effect.IO' should match declared_module 'cats.effect'
        # Walk from longest prefix to shortest
        parts = source_literal.split("::") if sep == "::" else source_literal.split(sep)

        for i in range(len(parts) - 1, 0, -1):
            prefix = "::".join(parts[:i]) if sep == "::" else sep.join(parts[:i])
            if prefix in self._module_to_paths:
                paths = self._module_to_paths[prefix]
                return paths[0] if paths else None

        return None

    def _resolve_ruby_relative(self, source_literal: str, importer_path: str) -> str | None:
        """Resolve Ruby require_relative as a path."""
        importer_dir = str(PurePosixPath(importer_path).parent)
        resolved = _normalize_path(importer_dir + "/" + source_literal)

        if resolved in self._all_paths:
            return resolved
        candidate = resolved + ".rb"
        if candidate in self._all_paths:
            return candidate
        return None

    @staticmethod
    def _separator_for_kind(import_kind: str) -> str:
        """Return the module path separator for an import kind."""
        if import_kind in ("rust_use",):
            return "::"
        elif import_kind in ("go_import",) or import_kind in ("ruby_require",):
            return "/"
        else:
            # Java, Kotlin, Scala, C#, PHP, Elixir, Haskell, Julia, etc.
            return "."


def _normalize_path(path: str) -> str:
    """Normalize a relative path (resolve . and ..).

    >>> _normalize_path('src/utils/../models/user')
    'src/models/user'
    >>> _normalize_path('src/./utils')
    'src/utils'
    """
    parts: list[str] = []
    for segment in path.replace("\\", "/").split("/"):
        if segment == "." or segment == "":
            continue
        elif segment == "..":
            if parts:
                parts.pop()
        else:
            parts.append(segment)
    return "/".join(parts)
