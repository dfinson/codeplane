"""Canonical exclude patterns with tiered architecture.

Tier 0 (HARDCODED_DIRS): Never traversed, not user-configurable.
    - VCS internals, CodePlane data directories
    - These are ALWAYS excluded regardless of .cplignore

Tier 1 (DEFAULT_PRUNABLE_DIRS): Excluded by default, user can override with !pattern.
    - Dependencies, caches, build outputs
    - Users can opt-in by adding "!dirname" to .cplignore

The combined PRUNABLE_DIRS = HARDCODED_DIRS | DEFAULT_PRUNABLE_DIRS for backward compat.
"""

from __future__ import annotations

# =============================================================================
# Tier 0: HARDCODED - Never traverse, not user-configurable
# =============================================================================
# These directories are ALWAYS excluded. Users cannot opt-in via .cplignore.
# Rationale: VCS internals and our own data should never be indexed.

HARDCODED_DIRS: frozenset[str] = frozenset(
    (
        # VCS internals
        ".git",
        ".svn",
        ".hg",
        ".bzr",
        # CodePlane data
        ".codeplane",
    )
)

# =============================================================================
# Tier 1: DEFAULT_PRUNABLE - Excluded by default, user can override
# =============================================================================
# These directories are excluded by default for performance, but users can
# opt-in by adding "!dirname" to .cplignore (e.g., "!vendor/" to index vendor).
#
# Organized by ecosystem for maintainability.

DEFAULT_PRUNABLE_DIRS: frozenset[str] = frozenset(
    (
        # -------------------------------------------------------------------------
        # JavaScript/Node.js ecosystem
        # -------------------------------------------------------------------------
        "node_modules",
        ".npm",
        ".yarn",
        ".pnpm-store",
        "bower_components",
        ".next",  # Next.js build
        ".nuxt",  # Nuxt.js build
        ".turbo",  # Turborepo cache
        # -------------------------------------------------------------------------
        # Python ecosystem
        # -------------------------------------------------------------------------
        "venv",
        ".venv",
        ".virtualenv",
        "virtualenv",
        "env",
        ".env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "eggs",
        ".eggs",
        "site-packages",
        ".ipynb_checkpoints",
        ".hypothesis",
        "htmlcov",
        # -------------------------------------------------------------------------
        # Ruby ecosystem
        # -------------------------------------------------------------------------
        ".bundle",
        # -------------------------------------------------------------------------
        # Go ecosystem
        # -------------------------------------------------------------------------
        "pkg",  # Go pkg cache (older GOPATH style)
        # -------------------------------------------------------------------------
        # Rust ecosystem
        # -------------------------------------------------------------------------
        "target",  # Cargo build output
        # -------------------------------------------------------------------------
        # Elixir/Erlang ecosystem
        # -------------------------------------------------------------------------
        "_build",  # Mix build output
        "deps",  # Mix dependencies
        "ebin",  # Erlang compiled beam files
        # -------------------------------------------------------------------------
        # Haskell ecosystem
        # -------------------------------------------------------------------------
        ".stack-work",  # Stack build artifacts
        ".cabal-sandbox",  # Cabal sandbox (legacy)
        # -------------------------------------------------------------------------
        # OCaml/Reason ecosystem
        # -------------------------------------------------------------------------
        "_opam",  # opam local switch
        "_esy",  # esy package cache
        # -------------------------------------------------------------------------
        # Scala ecosystem
        # -------------------------------------------------------------------------
        ".bsp",  # Build Server Protocol
        ".metals",  # Metals LSP cache
        ".bloop",  # Bloop build cache
        # -------------------------------------------------------------------------
        # Clojure ecosystem
        # -------------------------------------------------------------------------
        ".cpcache",  # Clojure tools.deps cache
        ".clj-kondo",  # clj-kondo linter cache
        ".lsp",  # LSP data
        # -------------------------------------------------------------------------
        # Dart/Flutter ecosystem
        # -------------------------------------------------------------------------
        ".dart_tool",  # Dart tooling
        ".pub-cache",  # Pub package cache
        # -------------------------------------------------------------------------
        # JVM ecosystem (Java, Kotlin, Groovy)
        # -------------------------------------------------------------------------
        ".gradle",  # Gradle cache
        ".m2",  # Maven local repo
        # -------------------------------------------------------------------------
        # .NET ecosystem
        # -------------------------------------------------------------------------
        "bin",  # .NET build output
        "obj",  # .NET intermediate
        "packages",  # NuGet packages (older style)
        # -------------------------------------------------------------------------
        # iOS/macOS ecosystem
        # Note: macOS is case-insensitive, but we use lowercase for consistency.
        # The watcher/scanner should do case-insensitive matching on macOS.
        # -------------------------------------------------------------------------
        "pods",  # CocoaPods
        "deriveddata",  # Xcode build
        ".swiftpm",  # Swift Package Manager
        # -------------------------------------------------------------------------
        # Android ecosystem
        # -------------------------------------------------------------------------
        ".android",  # Android SDK/AVD
        # -------------------------------------------------------------------------
        # Infrastructure/DevOps
        # -------------------------------------------------------------------------
        ".terraform",  # Terraform state/plugins
        # -------------------------------------------------------------------------
        # Generic build/output directories
        # -------------------------------------------------------------------------
        "dist",
        "build",
        "out",
        "coverage",
        ".coverage",
        ".nyc_output",
        # -------------------------------------------------------------------------
        # IDE/Editor directories
        # -------------------------------------------------------------------------
        ".idea",  # JetBrains
        ".vscode",  # VS Code
        ".vs",  # Visual Studio
        # -------------------------------------------------------------------------
        # Misc caches
        # -------------------------------------------------------------------------
        ".cache",
        "tmp",
        "temp",
        "vendor",  # Multi-language vendoring
    )
)

# =============================================================================
# Combined set for backward compatibility
# =============================================================================
# Use this for simple "should we skip this directory" checks.
# For tiered behavior, use is_hardcoded_dir() and is_default_prunable().

PRUNABLE_DIRS: frozenset[str] = HARDCODED_DIRS | DEFAULT_PRUNABLE_DIRS


def is_hardcoded_dir(dirname: str) -> bool:
    """Check if directory is hardcoded (never traversable, not overridable)."""
    return dirname in HARDCODED_DIRS


def is_default_prunable(dirname: str) -> bool:
    """Check if directory is prunable by default (but user can override)."""
    return dirname in DEFAULT_PRUNABLE_DIRS


UNIVERSAL_EXCLUDE_GLOBS: tuple[str, ...] = tuple(f"**/{d}/**" for d in sorted(PRUNABLE_DIRS))

_CPLIGNORE = """\
# CodePlane ignore patterns (gitignore syntax)
# Use !pattern to opt-in directories that are excluded by default.
# Example: !vendor/ to index the vendor directory.

# =============================================================================
# VCS (always excluded, cannot be overridden)
# =============================================================================
.git/
.svn/
.hg/
.bzr/

# =============================================================================
# Dependencies and Package Managers
# =============================================================================
# JavaScript/Node.js
node_modules/
.npm/
.yarn/
.pnpm-store/
bower_components/

# Python
.venv/
venv/
env/
.env/
__pycache__/
*.pyc
*.pyo
*.pyd
site-packages/
.tox/
.nox/

# Ruby
.bundle/
vendor/bundle/

# Go
# go.sum is a file, intentionally NOT excluded (useful for security audits)

# Elixir/Erlang
_build/
deps/
*.beam

# Haskell
.stack-work/
.cabal-sandbox/
*.hi
*.hie

# OCaml/Reason
_opam/
_esy/
*.cmo
*.cmi
*.cmx
*.cma
*.cmxa

# Scala
.bsp/
.metals/
.bloop/

# Clojure
.cpcache/
.clj-kondo/
.lsp/

# Dart/Flutter
.dart_tool/
.pub-cache/
*.dill

# JVM (Java, Kotlin, Groovy)
.gradle/
.m2/

# .NET
packages/

# iOS/macOS
pods/
deriveddata/
.swiftpm/

# Generic vendor
vendor/

# =============================================================================
# Build Outputs
# =============================================================================
dist/
build/
_build/
out/
target/
bin/
obj/

# Python
*.egg-info/
*.egg
*.whl

# Archives
*.tar.gz
*.zip
*.rar
*.7z

# JVM
*.jar
*.war
*.class

# Rust
Cargo.lock

# Native
*.dll
*.exe
*.pdb
*.so
*.dylib
*.a
*.o
*.obj

# =============================================================================
# Testing & Coverage
# =============================================================================
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
coverage/
.nyc_output/
.hypothesis/

# =============================================================================
# IDE & Editor
# =============================================================================
.idea/
.vscode/
.vs/
*.swp
*.swo
*~
.project
.classpath
.settings/

# =============================================================================
# Secrets (NEVER index - security critical)
# =============================================================================
.env
.env.*
!.env.example
*.pem
*.key
*.crt
*.p12
*.pfx
**/secrets/
**/credentials/
*.keystore
service-account*.json

# =============================================================================
# Large & Binary Files
# =============================================================================
# Documents
*.pdf
*.doc
*.docx
*.xls
*.xlsx
*.ppt
*.pptx

# Archives
*.tar
*.gz
*.bz2
*.iso
*.dmg
*.deb
*.rpm
*.msi

# Media
*.jpg
*.jpeg
*.png
*.gif
*.ico
*.svg
*.mp3
*.mp4
*.avi
*.mov
*.webm
*.wav
*.ogg
*.ttf
*.otf
*.woff
*.woff2
*.eot

# Databases
*.sqlite
*.sqlite3
*.db
*.dump
*.bak

# =============================================================================
# Logs & Temp
# =============================================================================
*.log
logs/
tmp/
temp/
*.tmp
*.cache
.cache/

# =============================================================================
# OS Files
# =============================================================================
.DS_Store
._*
Thumbs.db
desktop.ini

# =============================================================================
# Lock Files (large, auto-generated)
# =============================================================================
package-lock.json
yarn.lock
pnpm-lock.yaml
composer.lock
Gemfile.lock
poetry.lock
Pipfile.lock
uv.lock
pdm.lock

# =============================================================================
# Generated Code
# =============================================================================
**/generated/
**/*_generated.*
**/*.gen.*
**/*.pb.go
**/*.pb.h
**/*.pb.cc
**/*.pb.py
"""


def generate_cplignore_template() -> str:
    return _CPLIGNORE


__all__ = [
    "HARDCODED_DIRS",
    "DEFAULT_PRUNABLE_DIRS",
    "PRUNABLE_DIRS",
    "UNIVERSAL_EXCLUDE_GLOBS",
    "is_hardcoded_dir",
    "is_default_prunable",
    "generate_cplignore_template",
]
