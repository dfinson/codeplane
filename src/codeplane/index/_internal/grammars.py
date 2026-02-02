"""Dynamic tree-sitter grammar installation.

Scans repo for file extensions, determines needed grammars, installs on demand.
This keeps the base install minimal - only grammars actually needed are installed.
"""

from __future__ import annotations

import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from codeplane.index.models import LanguageFamily

# Map LanguageFamily -> (PyPI package name, min version, import name)
# Import name is the module to check if already installed
GRAMMAR_PACKAGES: dict[LanguageFamily, tuple[str, str, str]] = {
    # Core/mainstream
    LanguageFamily.PYTHON: ("tree-sitter-python", "0.23.0", "tree_sitter_python"),
    LanguageFamily.JAVASCRIPT: ("tree-sitter-javascript", "0.23.0", "tree_sitter_javascript"),
    LanguageFamily.GO: ("tree-sitter-go", "0.23.0", "tree_sitter_go"),
    LanguageFamily.RUST: ("tree-sitter-rust", "0.23.0", "tree_sitter_rust"),
    LanguageFamily.JVM: ("tree-sitter-java", "0.23.0", "tree_sitter_java"),
    LanguageFamily.DOTNET: ("tree-sitter-c-sharp", "0.23.0", "tree_sitter_c_sharp"),
    LanguageFamily.RUBY: ("tree-sitter-ruby", "0.23.0", "tree_sitter_ruby"),
    LanguageFamily.PHP: ("tree-sitter-php", "0.23.0", "tree_sitter_php"),
    LanguageFamily.SWIFT: ("tree-sitter-swift", "0.0.1", "tree_sitter_swift"),
    LanguageFamily.CPP: ("tree-sitter-cpp", "0.23.0", "tree_sitter_cpp"),
    # Functional
    LanguageFamily.ELIXIR: ("tree-sitter-elixir", "0.3.0", "tree_sitter_elixir"),
    LanguageFamily.HASKELL: ("tree-sitter-haskell", "0.23.0", "tree_sitter_haskell"),
    LanguageFamily.OCAML: ("tree-sitter-ocaml", "0.23.0", "tree_sitter_ocaml"),
    # Scripting
    LanguageFamily.SHELL: ("tree-sitter-bash", "0.23.0", "tree_sitter_bash"),
    LanguageFamily.LUA: ("tree-sitter-lua", "0.2.0", "tree_sitter_lua"),
    LanguageFamily.JULIA: ("tree-sitter-julia", "0.23.0", "tree_sitter_julia"),
    # Systems
    LanguageFamily.ZIG: ("tree-sitter-zig", "1.1.0", "tree_sitter_zig"),
    LanguageFamily.ADA: ("tree-sitter-ada", "0.1.0", "tree_sitter_ada"),
    LanguageFamily.FORTRAN: ("tree-sitter-fortran", "0.5.0", "tree_sitter_fortran"),
    LanguageFamily.ODIN: ("tree-sitter-odin", "1.2.0", "tree_sitter_odin"),
    # Web
    LanguageFamily.HTML: ("tree-sitter-html", "0.23.0", "tree_sitter_html"),
    LanguageFamily.CSS: ("tree-sitter-css", "0.23.0", "tree_sitter_css"),
    # Hardware
    LanguageFamily.VERILOG: ("tree-sitter-verilog", "1.0.0", "tree_sitter_verilog"),
    # Data/Config
    LanguageFamily.TERRAFORM: ("tree-sitter-hcl", "1.0.0", "tree_sitter_hcl"),
    LanguageFamily.SQL: ("tree-sitter-sql", "0.3.0", "tree_sitter_sql"),
    LanguageFamily.DOCKER: ("tree-sitter-dockerfile", "0.2.0", "tree_sitter_dockerfile"),
    LanguageFamily.MARKDOWN: ("tree-sitter-markdown", "0.3.0", "tree_sitter_markdown"),
    LanguageFamily.JSON_YAML: ("tree-sitter-json", "0.24.0", "tree_sitter_json"),
    LanguageFamily.GRAPHQL: ("tree-sitter-graphql", "0.1.0", "tree_sitter_graphql"),
    LanguageFamily.MAKE: ("tree-sitter-make", "1.1.0", "tree_sitter_make"),
}

# Additional packages for language families that need multiple grammars
EXTRA_PACKAGES: dict[LanguageFamily, list[tuple[str, str, str]]] = {
    LanguageFamily.JAVASCRIPT: [
        ("tree-sitter-typescript", "0.23.0", "tree_sitter_typescript"),
    ],
    LanguageFamily.CPP: [
        ("tree-sitter-c", "0.23.0", "tree_sitter_c"),
    ],
    LanguageFamily.JSON_YAML: [
        ("tree-sitter-yaml", "0.6.0", "tree_sitter_yaml"),
        ("tree-sitter-toml", "0.6.0", "tree_sitter_toml"),
    ],
    LanguageFamily.HTML: [
        ("tree-sitter-xml", "0.6.0", "tree_sitter_xml"),
    ],
    LanguageFamily.JVM: [
        ("tree-sitter-kotlin", "1.0.0", "tree_sitter_kotlin"),
        ("tree-sitter-scala", "0.23.0", "tree_sitter_scala"),
    ],
}


def is_grammar_installed(import_name: str) -> bool:
    """Check if a grammar package is installed."""
    return find_spec(import_name) is not None


def get_needed_grammars(languages: set[LanguageFamily]) -> list[tuple[str, str]]:
    """Get list of (package, version) tuples needed but not installed."""
    needed: list[tuple[str, str]] = []

    for lang in languages:
        if lang not in GRAMMAR_PACKAGES:
            continue

        pkg, version, import_name = GRAMMAR_PACKAGES[lang]
        if not is_grammar_installed(import_name):
            needed.append((pkg, version))

        # Check extra packages for this language
        for extra_pkg, extra_ver, extra_import in EXTRA_PACKAGES.get(lang, []):
            if not is_grammar_installed(extra_import):
                needed.append((extra_pkg, extra_ver))

    return needed


def install_grammars(
    packages: list[tuple[str, str]], quiet: bool = False, status_fn: Any = None
) -> bool:
    """Install grammar packages via pip.

    Uses the current Python interpreter to install packages into the running
    environment. This ensures packages are installed where they can be imported.

    Args:
        packages: List of (package_name, min_version) tuples
        quiet: Suppress output
        status_fn: Optional status callback for progress messages

    Returns True if all installed successfully.
    """
    if not packages:
        return True

    import importlib

    specs = [f"{pkg}>={ver}" for pkg, ver in packages]
    pkg_names = [p for p, _ in packages]

    if status_fn and not quiet:
        status_fn(f"Installing: {', '.join(pkg_names)}", style="none", indent=4)

    # Always use sys.executable to ensure packages install into the current env
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + specs
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            importlib.invalidate_caches()
            return True
        else:
            if status_fn and not quiet:
                status_fn(f"Failed to install grammars: {result.stderr}", style="error", indent=4)
            return False
    except subprocess.TimeoutExpired:
        if status_fn and not quiet:
            status_fn("Grammar installation timed out", style="error", indent=4)
        return False


def scan_repo_languages(repo_root: Path) -> set[LanguageFamily]:
    """Quick scan of repo to determine which languages are present.

    Uses git ls-files for speed, falls back to filesystem walk with pruning.
    """
    import os

    from codeplane.index._internal.discovery.language_detect import detect_language_family
    from codeplane.index._internal.ignore import PRUNABLE_DIRS

    languages: set[LanguageFamily] = set()

    # Try git ls-files first (fast, respects .gitignore)
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                lang = detect_language_family(line)
                if lang is not None:
                    languages.add(lang)
            return languages
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fall back to walking the filesystem with pruning
    for dirpath, dirnames, filenames in os.walk(repo_root):
        # Prune excluded directories in-place
        dirnames[:] = [d for d in dirnames if d not in PRUNABLE_DIRS]

        for filename in filenames:
            path = Path(dirpath) / filename
            if not any(part.startswith(".") for part in path.relative_to(repo_root).parts):
                lang = detect_language_family(path)
                if lang is not None:
                    languages.add(lang)

    return languages


def ensure_grammars_for_repo(repo_root: Path, quiet: bool = False, status_fn: Any = None) -> bool:
    """Scan repo and install any missing grammars.

    Args:
        repo_root: Path to the repository
        quiet: Suppress output
        status_fn: Optional status callback for progress messages

    Returns True if all needed grammars are available.
    """
    # Scan for languages
    languages = scan_repo_languages(repo_root)

    # Check what's missing
    needed = get_needed_grammars(languages)
    if not needed:
        if status_fn and not quiet:
            lang_list = ", ".join(sorted(languages)) if languages else "none detected"
            status_fn(f"Language support ready ({lang_list})", style="success", indent=2)
        return True

    # Install
    success = install_grammars(needed, quiet=quiet, status_fn=status_fn)
    if success and status_fn and not quiet:
        lang_list = ", ".join(sorted(languages)) if languages else "none detected"
        status_fn(f"Language support ready ({lang_list})", style="success", indent=2)
    return success
