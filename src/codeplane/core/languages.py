"""Canonical language definitions.

This module defines the authoritative mapping of:
- File extensions → language families
- Filenames → language families
- Project markers (workspace/package level)
- Include globs for indexing
- Tree-sitter grammar names
- Test file patterns

Design decisions:
1. Extensions can map to MULTIPLE families (EXTENSION_TO_NAMES) for ambiguous cases
2. detect_language_name() returns the PRIMARY name by priority;
   use get_families_for_extension() to get all candidates for ambiguous extensions
3. Markers are EXACT FILENAMES only (no globs, no suffix patterns).
   If you need glob-based detection, implement it in the scanner layer.
4. Include globs are auto-generated from BOTH extensions AND filenames
5. Grammar is None only when no usable tree-sitter grammar exists

KNOWN AMBIGUOUS EXTENSIONS (require context for correct classification):
- .m: objc (priority 60) vs matlab (priority 50) - use Xcode/MATLAB markers to resolve
- .h: c_cpp (C/C++ header) - truly ambiguous between C and C++
- .v: verilog (priority 70) vs vlang (priority 30) - verilog wins by default
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codeplane.index.models import LanguageFamily


def _get_language_family() -> type[LanguageFamily]:
    from codeplane.index.models import LanguageFamily

    return LanguageFamily


@dataclass(frozen=True, slots=True)
class Language:
    """Canonical definition for a language name.

    Attributes:
        name: Unique identifier (lowercase, e.g., "python", "javascript")
        extensions: File extensions including dot (e.g., ".py", ".js")
        filenames: Special filenames to detect (lowercase, EXACT match only)
        markers_workspace: Files indicating workspace root (EXACT filenames, lowercase)
        markers_package: Files indicating package/module root (EXACT filenames, lowercase)
        grammar: Tree-sitter grammar name, or None if no grammar available
        test_patterns: Glob patterns for test files
        ambient: If True, index files even without project markers
        priority: Higher = preferred when extension is ambiguous (default 50)
    """

    name: str
    extensions: frozenset[str]
    filenames: frozenset[str] = field(default_factory=frozenset)
    markers_workspace: tuple[str, ...] = ()
    markers_package: tuple[str, ...] = ()
    grammar: str | None = None
    test_patterns: tuple[str, ...] = ()
    ambient: bool = False
    priority: int = 50


# =============================================================================
# Language Definitions
# =============================================================================
# RULES:
# 1. All filenames/markers MUST be lowercase (exact filenames, no globs/wildcards)
# 2. Extensions are case-insensitive (normalized to lowercase during lookup)
# 3. Priority determines winner for ambiguous extensions (higher wins)
# 4. Grammar must be a real tree-sitter grammar name or None

ALL_LANGUAGES: tuple[Language, ...] = (
    # =========================================================================
    # Tier 1: Most common languages
    # =========================================================================
    Language(
        name="python",
        extensions=frozenset({".py", ".pyi", ".pyw", ".pyx", ".pxd", ".pxi"}),
        filenames=frozenset({"pipfile", "setup.py", "pyproject.toml"}),
        markers_workspace=("uv.lock", "poetry.lock", "pipfile.lock", "pdm.lock"),
        markers_package=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile"),
        grammar="python",
        test_patterns=("test_*.py", "*_test.py"),
        priority=80,
    ),
    Language(
        name="javascript",
        extensions=frozenset(
            {
                ".js",
                ".jsx",
                ".ts",
                ".tsx",
                ".mjs",
                ".cjs",
                ".mts",
                ".cts",
                ".vue",
                ".svelte",
                ".astro",
            }
        ),
        filenames=frozenset({"package.json", "deno.json", "tsconfig.json", "jsconfig.json"}),
        markers_workspace=("pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json"),
        markers_package=("package.json", "deno.json", "tsconfig.json"),
        grammar="typescript",  # TypeScript grammar handles JS/TS/JSX/TSX
        test_patterns=("*.test.js", "*.test.ts", "*.spec.js", "*.spec.ts"),
        priority=80,
    ),
    Language(
        name="go",
        extensions=frozenset({".go"}),
        filenames=frozenset({"go.mod", "go.sum"}),
        markers_workspace=("go.work",),
        markers_package=("go.mod",),
        grammar="go",
        test_patterns=("*_test.go",),
        priority=80,
    ),
    Language(
        name="rust",
        extensions=frozenset({".rs"}),
        filenames=frozenset({"cargo.toml", "cargo.lock"}),
        # Note: Cargo.toml with [workspace] section is detected by scanner logic
        markers_workspace=(),
        markers_package=("cargo.toml",),
        grammar="rust",
        priority=80,
    ),
    # =========================================================================
    # JVM Languages (each has its own grammar)
    # =========================================================================
    Language(
        name="java",
        extensions=frozenset({".java"}),
        filenames=frozenset({"pom.xml"}),
        markers_workspace=("settings.gradle", "settings.gradle.kts"),
        markers_package=("pom.xml", "build.gradle", "build.gradle.kts"),
        grammar="java",
        test_patterns=("*Test.java", "Test*.java"),
        priority=80,
    ),
    Language(
        name="kotlin",
        extensions=frozenset({".kt", ".kts"}),
        filenames=frozenset({"build.gradle.kts"}),
        markers_workspace=("settings.gradle.kts",),
        markers_package=("build.gradle.kts",),
        grammar="kotlin",
        test_patterns=("*Test.kt", "Test*.kt"),
        priority=75,
    ),
    Language(
        name="scala",
        extensions=frozenset({".scala", ".sc"}),
        filenames=frozenset({"build.sbt"}),
        markers_workspace=(),
        markers_package=("build.sbt",),
        grammar="scala",
        test_patterns=("*Spec.scala", "*Test.scala"),
        priority=75,
    ),
    Language(
        name="groovy",
        extensions=frozenset({".groovy", ".gradle"}),
        filenames=frozenset({"build.gradle", "settings.gradle"}),
        markers_workspace=("settings.gradle",),
        markers_package=("build.gradle",),
        grammar="groovy",
        test_patterns=("*Test.groovy", "*Spec.groovy"),
        priority=70,
    ),
    # =========================================================================
    # .NET Languages (each has its own grammar)
    # =========================================================================
    Language(
        name="csharp",
        extensions=frozenset({".cs"}),
        filenames=frozenset(),
        # Note: .sln and .csproj detection is done by scanner which knows file extensions
        markers_workspace=(),
        markers_package=(),
        grammar="c_sharp",
        test_patterns=("*Tests.cs", "*Test.cs"),
        priority=80,
    ),
    Language(
        name="fsharp",
        extensions=frozenset({".fs", ".fsx", ".fsi"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="fsharp",
        test_patterns=("*Tests.fs", "*Test.fs"),
        priority=75,
    ),
    Language(
        name="vbnet",
        extensions=frozenset({".vb"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="visual_basic",
        test_patterns=("*Tests.vb", "*Test.vb"),
        priority=70,
    ),
    # =========================================================================
    # C/C++ (unified as c_cpp since .h is genuinely ambiguous)
    # =========================================================================
    Language(
        name="c_cpp",
        extensions=frozenset({".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".hh", ".ino"}),
        filenames=frozenset({"cmakelists.txt", "meson.build"}),
        markers_workspace=(),
        markers_package=("cmakelists.txt", "meson.build", "compile_commands.json"),
        grammar="cpp",  # C++ grammar handles both C and C++
        priority=60,
    ),
    # =========================================================================
    # Objective-C (separate; .m is AMBIGUOUS with MATLAB)
    # =========================================================================
    Language(
        name="objc",
        extensions=frozenset({".m", ".mm"}),
        filenames=frozenset(),
        # Xcode project markers help disambiguate .m files
        markers_workspace=(),
        markers_package=("podfile", "podfile.lock", "package.swift"),
        grammar="objc",
        priority=60,  # Higher than MATLAB for .m
    ),
    # =========================================================================
    # MATLAB/Octave (.m is AMBIGUOUS with Objective-C)
    # =========================================================================
    Language(
        name="matlab",
        extensions=frozenset({".m", ".mlx"}),  # .m is ambiguous; .mlx is MATLAB-only
        filenames=frozenset({"startup.m"}),  # MATLAB-specific startup file
        markers_workspace=(),
        # MATLAB project markers help disambiguate .m files
        markers_package=("matlab.project",),
        grammar=None,  # No tree-sitter-matlab in common registries
        priority=50,  # Lower than objc for .m
    ),
    # =========================================================================
    # Other mainstream languages
    # =========================================================================
    Language(
        name="ruby",
        extensions=frozenset({".rb", ".rake", ".gemspec", ".erb"}),
        filenames=frozenset({"gemfile", "rakefile", "vagrantfile"}),
        markers_workspace=("gemfile.lock",),
        markers_package=("gemfile",),
        grammar="ruby",
        test_patterns=("*_spec.rb", "*_test.rb"),
        priority=70,
    ),
    Language(
        name="php",
        extensions=frozenset({".php", ".phtml"}),
        filenames=frozenset(),
        markers_workspace=("composer.lock",),
        markers_package=("composer.json",),
        grammar="php",
        test_patterns=("*Test.php",),
        priority=70,
    ),
    Language(
        name="swift",
        extensions=frozenset({".swift"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=("package.swift",),
        grammar="swift",
        test_patterns=("*Tests.swift",),
        priority=70,
    ),
    # =========================================================================
    # Functional languages
    # =========================================================================
    Language(
        name="elixir",
        extensions=frozenset({".ex", ".exs", ".eex", ".heex"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=("mix.exs",),
        grammar="elixir",
        test_patterns=("*_test.exs",),
        priority=70,
    ),
    Language(
        name="erlang",
        extensions=frozenset({".erl", ".hrl"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=("rebar.config",),
        grammar="erlang",
        priority=60,
    ),
    Language(
        name="haskell",
        extensions=frozenset({".hs", ".lhs", ".cabal"}),
        filenames=frozenset({"stack.yaml", "package.yaml"}),
        markers_workspace=(),
        markers_package=("stack.yaml", "package.yaml"),
        grammar="haskell",
        priority=70,
    ),
    Language(
        name="ocaml",
        extensions=frozenset({".ml", ".mli", ".mll", ".mly"}),
        filenames=frozenset({"dune-project", "dune"}),
        markers_workspace=(),
        markers_package=("dune-project", "dune"),
        grammar="ocaml",
        priority=70,
    ),
    Language(
        name="reason",
        extensions=frozenset({".re", ".rei"}),
        filenames=frozenset({"bsconfig.json"}),
        markers_workspace=(),
        markers_package=("bsconfig.json",),
        grammar="reason",
        priority=60,
    ),
    Language(
        name="clojure",
        extensions=frozenset({".clj", ".cljs", ".cljc", ".edn"}),
        filenames=frozenset({"project.clj", "deps.edn"}),
        markers_workspace=(),
        markers_package=("project.clj", "deps.edn"),
        grammar="clojure",
        test_patterns=("*_test.clj",),
        priority=70,
    ),
    Language(
        name="elm",
        extensions=frozenset({".elm"}),
        filenames=frozenset({"elm.json"}),
        markers_workspace=(),
        markers_package=("elm.json",),
        grammar="elm",
        priority=70,
    ),
    # =========================================================================
    # Scripting languages
    # =========================================================================
    # Shell: bash/zsh/ksh (POSIX-compatible shells)
    Language(
        name="shell",
        extensions=frozenset({".sh", ".bash", ".zsh", ".ksh"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="bash",
        priority=50,
    ),
    # Fish shell is SEPARATE (incompatible syntax with bash)
    Language(
        name="fish",
        extensions=frozenset({".fish"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="fish",
        priority=50,
    ),
    # PowerShell is separate (completely different syntax)
    Language(
        name="powershell",
        extensions=frozenset({".ps1", ".psm1", ".psd1"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="powershell",
        priority=60,
    ),
    Language(
        name="lua",
        extensions=frozenset({".lua", ".luau"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="lua",
        priority=60,
    ),
    Language(
        name="perl",
        extensions=frozenset({".pl", ".pm", ".pod", ".t"}),
        filenames=frozenset({"makefile.pl", "build.pl", "cpanfile"}),
        markers_workspace=(),
        markers_package=("makefile.pl", "build.pl", "cpanfile"),
        grammar="perl",
        test_patterns=("*.t",),
        priority=50,
    ),
    Language(
        name="r",
        extensions=frozenset({".r", ".rmd"}),
        filenames=frozenset({"description"}),  # R package DESCRIPTION file
        markers_workspace=(),
        markers_package=("description",),
        grammar="r",
        priority=50,
    ),
    Language(
        name="julia",
        extensions=frozenset({".jl"}),
        filenames=frozenset({"project.toml"}),
        markers_workspace=(),
        markers_package=("project.toml",),
        grammar="julia",
        priority=70,
    ),
    # =========================================================================
    # Systems languages
    # =========================================================================
    Language(
        name="zig",
        extensions=frozenset({".zig"}),
        filenames=frozenset({"build.zig"}),
        markers_workspace=(),
        markers_package=("build.zig",),
        grammar="zig",
        priority=70,
    ),
    Language(
        name="nim",
        extensions=frozenset({".nim", ".nims", ".nimble"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="nim",
        priority=60,
    ),
    Language(
        name="d",
        extensions=frozenset({".d", ".di"}),
        filenames=frozenset({"dub.json", "dub.sdl"}),
        markers_workspace=(),
        markers_package=("dub.json", "dub.sdl"),
        grammar="d",
        priority=50,
    ),
    Language(
        name="ada",
        extensions=frozenset({".adb", ".ads"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="ada",
        priority=60,
    ),
    Language(
        name="fortran",
        extensions=frozenset({".f", ".f77", ".f90", ".f95", ".f03", ".f08", ".for"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="fortran",
        priority=50,
    ),
    Language(
        name="pascal",
        extensions=frozenset({".pas", ".pp", ".lpr", ".dpr"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="pascal",
        priority=50,
    ),
    Language(
        name="dart",
        extensions=frozenset({".dart"}),
        filenames=frozenset({"pubspec.yaml"}),
        markers_workspace=(),
        markers_package=("pubspec.yaml",),
        grammar="dart",
        test_patterns=("*_test.dart",),
        priority=70,
    ),
    Language(
        name="gleam",
        extensions=frozenset({".gleam"}),
        filenames=frozenset({"gleam.toml"}),
        markers_workspace=(),
        markers_package=("gleam.toml",),
        grammar="gleam",
        priority=60,
    ),
    Language(
        name="crystal",
        extensions=frozenset({".cr"}),
        filenames=frozenset({"shard.yml"}),
        markers_workspace=(),
        markers_package=("shard.yml",),
        grammar="crystal",
        test_patterns=("*_spec.cr",),
        priority=60,
    ),
    Language(
        name="vlang",
        extensions=frozenset({".v", ".vv"}),
        filenames=frozenset({"v.mod"}),
        markers_workspace=(),
        markers_package=("v.mod",),
        grammar="v",
        priority=30,  # Low priority - .v defaults to Verilog in most contexts
    ),
    Language(
        name="odin",
        extensions=frozenset({".odin"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="odin",
        priority=60,
    ),
    # =========================================================================
    # Hardware Description Languages
    # =========================================================================
    Language(
        name="verilog",
        extensions=frozenset({".v", ".vh", ".sv", ".svh"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="verilog",
        priority=70,  # Higher than vlang for .v
    ),
    Language(
        name="vhdl",
        extensions=frozenset({".vhd", ".vhdl"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="vhdl",
        priority=60,
    ),
    # =========================================================================
    # Web/Markup
    # =========================================================================
    Language(
        name="html",
        extensions=frozenset({".html", ".htm", ".xhtml"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="html",
        priority=50,
    ),
    Language(
        name="xml",
        extensions=frozenset({".xml", ".xsl", ".xslt", ".xsd", ".svg", ".plist"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="xml",
        ambient=True,
        priority=40,
    ),
    Language(
        name="css",
        extensions=frozenset({".css", ".scss", ".sass", ".less"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="css",
        priority=50,
    ),
    # =========================================================================
    # Infrastructure/Config
    # =========================================================================
    Language(
        name="terraform",
        extensions=frozenset({".tf", ".tfvars"}),
        filenames=frozenset({"terraform.tfvars", "main.tf", "versions.tf"}),
        markers_workspace=(".terraform.lock.hcl",),
        markers_package=("main.tf", "versions.tf"),
        grammar="hcl",
        priority=70,
    ),
    Language(
        name="hcl",
        extensions=frozenset({".hcl"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="hcl",
        priority=50,
    ),
    Language(
        name="sql",
        extensions=frozenset({".sql"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="sql",
        ambient=True,
        priority=50,
    ),
    Language(
        name="docker",
        extensions=frozenset(),  # Dockerfiles don't have extensions
        filenames=frozenset(
            {
                "dockerfile",
                "dockerfile.dev",
                "dockerfile.prod",
                "dockerfile.test",
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
            }
        ),
        markers_workspace=(),
        markers_package=(),
        grammar="dockerfile",
        ambient=True,
        priority=60,
    ),
    # =========================================================================
    # Data/Documentation
    # =========================================================================
    Language(
        name="markdown",
        extensions=frozenset({".md", ".mdx", ".markdown"}),
        filenames=frozenset({"readme", "changelog", "license", "contributing"}),
        markers_workspace=(),
        markers_package=(),
        grammar="markdown",
        ambient=True,
        priority=50,
    ),
    Language(
        name="rst",
        extensions=frozenset({".rst"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="rst",
        ambient=True,
        priority=40,
    ),
    Language(
        name="asciidoc",
        extensions=frozenset({".adoc", ".asciidoc"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="asciidoc",
        ambient=True,
        priority=40,
    ),
    Language(
        name="json",
        extensions=frozenset({".json", ".jsonc", ".json5"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="json",
        ambient=True,
        priority=50,
    ),
    Language(
        name="yaml",
        extensions=frozenset({".yaml", ".yml"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="yaml",
        ambient=True,
        priority=50,
    ),
    Language(
        name="toml",
        extensions=frozenset({".toml"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="toml",
        ambient=True,
        priority=50,
    ),
    Language(
        name="protobuf",
        extensions=frozenset({".proto"}),
        filenames=frozenset({"buf.yaml", "buf.work.yaml"}),
        markers_workspace=("buf.work.yaml",),
        markers_package=("buf.yaml",),
        grammar="proto",
        priority=60,
    ),
    Language(
        name="graphql",
        extensions=frozenset({".graphql", ".gql"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="graphql",
        ambient=True,
        priority=60,
    ),
    Language(
        name="nix",
        extensions=frozenset({".nix"}),
        filenames=frozenset({"flake.nix", "default.nix", "shell.nix"}),
        markers_workspace=("flake.lock",),
        markers_package=("flake.nix", "default.nix"),
        grammar="nix",
        priority=60,
    ),
    # =========================================================================
    # Build systems (filename-based, not extension-based)
    # =========================================================================
    Language(
        name="make",
        extensions=frozenset({".mk"}),
        filenames=frozenset({"makefile", "gnumakefile", "bsdmakefile"}),
        markers_workspace=(),
        markers_package=(),  # Makefile alone doesn't indicate a project
        grammar="make",
        priority=40,
    ),
    Language(
        name="cmake",
        extensions=frozenset({".cmake"}),
        filenames=frozenset({"cmakelists.txt", "cmakepresets.json"}),
        markers_workspace=(),
        markers_package=("cmakelists.txt",),
        grammar="cmake",
        priority=50,
    ),
    Language(
        name="meson",
        extensions=frozenset(),  # Meson uses specific filenames
        filenames=frozenset({"meson.build", "meson_options.txt"}),
        markers_workspace=(),
        markers_package=("meson.build",),
        grammar="meson",
        priority=50,
    ),
    Language(
        name="bazel",
        extensions=frozenset({".bazel", ".bzl"}),
        filenames=frozenset({"build", "build.bazel", "workspace", "workspace.bazel"}),
        markers_workspace=("workspace", "workspace.bazel"),
        markers_package=("build", "build.bazel"),
        grammar="starlark",
        priority=60,
    ),
    Language(
        name="just",
        extensions=frozenset(),
        filenames=frozenset({"justfile"}),
        markers_workspace=(),
        markers_package=(),
        grammar="just",
        priority=50,
    ),
    # =========================================================================
    # Other
    # =========================================================================
    Language(
        name="assembly",
        extensions=frozenset({".asm", ".s", ".nasm"}),
        filenames=frozenset(),
        markers_workspace=(),
        markers_package=(),
        grammar="asm",
        priority=40,
    ),
)

# =============================================================================
# Lookup Tables (built from ALL_LANGUAGES)
# =============================================================================

LANGUAGES_BY_NAME: dict[str, Language] = {lang.name: lang for lang in ALL_LANGUAGES}


def _build_extension_multimap() -> dict[str, tuple[str, ...]]:
    """Build extension -> families mapping, sorted by priority (highest first).

    Extensions that appear in multiple families (like .m, .v, .h) will have
    all families listed, with highest-priority first.
    """
    ext_to_families: dict[str, list[tuple[int, str]]] = {}
    for lang in ALL_LANGUAGES:
        for ext in lang.extensions:
            key = ext.lower()
            if key not in ext_to_families:
                ext_to_families[key] = []
            ext_to_families[key].append((lang.priority, lang.name))

    # Sort by priority descending, return just families
    result: dict[str, tuple[str, ...]] = {}
    for ext, families in ext_to_families.items():
        families.sort(key=lambda x: -x[0])  # Higher priority first
        result[ext] = tuple(f for _, f in families)
    return result


def _build_filename_map() -> dict[str, str]:
    """Build lowercase filename -> primary name mapping."""
    result: dict[str, str] = {}
    # Process in priority order
    sorted_langs = sorted(ALL_LANGUAGES, key=lambda lang: -lang.priority)
    for lang in sorted_langs:
        for filename in lang.filenames:
            key = filename.lower()
            if key not in result:
                result[key] = lang.name
    return result


# Extension -> all names (highest priority first)
EXTENSION_TO_NAMES: dict[str, tuple[str, ...]] = _build_extension_multimap()

# Extension -> primary name (highest priority)
EXTENSION_TO_NAME: dict[str, str] = {
    ext: names[0] for ext, names in EXTENSION_TO_NAMES.items() if names
}

# Filename -> name
FILENAME_TO_NAME: dict[str, str] = _build_filename_map()

# Ambient names (indexed without project markers)
AMBIENT_NAMES: frozenset[str] = frozenset(lang.name for lang in ALL_LANGUAGES if lang.ambient)

# Compound suffixes that need special handling (longer compounds checked first)
_COMPOUND_SUFFIXES: dict[str, str] = {
    ".d.ts.map": "javascript",  # Source maps for TypeScript declarations
    ".d.ts": "javascript",  # TypeScript declaration files
    ".spec.ts": "javascript",  # Keep as JS name (test file)
    ".test.ts": "javascript",  # Keep as JS name (test file)
}


# =============================================================================
# Ambiguous Extension Documentation
# =============================================================================
# These extensions map to multiple families. Use context (project markers,
# directory structure) to disambiguate when possible.

AMBIGUOUS_EXTENSIONS: dict[str, tuple[str, ...]] = {
    ext: families for ext, families in EXTENSION_TO_NAMES.items() if len(families) > 1
}


# =============================================================================
# Detection Functions
# =============================================================================


def get_families_for_extension(ext: str) -> tuple[str, ...]:
    """Get all language families that use this extension, ordered by priority.

    For ambiguous extensions like .m (objc, matlab) or .v (verilog, vlang),
    returns all matching families. Caller should use context to pick the right one.

    Args:
        ext: File extension including dot (e.g., ".py", ".m")

    Returns:
        Tuple of name names, highest priority first. Empty if unknown.
    """
    return EXTENSION_TO_NAMES.get(ext.lower(), ())


def is_ambiguous_extension(ext: str) -> bool:
    """Check if an extension maps to multiple language families."""
    return len(get_families_for_extension(ext)) > 1


def detect_language_family(path: str | Path) -> str | None:
    """Detect the primary language name for a file path.

    Detection order:
    1. Exact filename match (e.g., "Makefile", "Dockerfile")
    2. Compound suffix match (e.g., ".d.ts")
    3. Simple suffix match (e.g., ".py")

    For ambiguous extensions (e.g., .v, .m), returns the highest-priority name.
    Use get_families_for_extension() if you need all candidates, or use
    context-aware detection in the scanner layer.

    Args:
        path: File path (string or Path)

    Returns:
        Family name or None if unknown.
    """
    p = Path(path) if isinstance(path, str) else path
    name_lower = p.name.lower()

    # 1. Exact filename match
    if name := FILENAME_TO_NAME.get(name_lower):
        return name

    # 2. Compound suffix match (check longer compounds first)
    suffixes = p.suffixes
    if len(suffixes) >= 2:
        # Try 3-suffix compound
        if len(suffixes) >= 3:
            compound3 = "".join(suffixes[-3:]).lower()
            if name := _COMPOUND_SUFFIXES.get(compound3):
                return name
        # Try 2-suffix compound
        compound2 = "".join(suffixes[-2:]).lower()
        if name := _COMPOUND_SUFFIXES.get(compound2):
            return name

    # 3. Simple suffix match (returns highest priority name)
    suffix_lower = p.suffix.lower()
    return EXTENSION_TO_NAME.get(suffix_lower)


def detect_language_family_enum(path: str | Path) -> LanguageFamily | None:
    """Detect language name and return as LanguageFamily enum.

    Returns None if:
    - File type is unknown
    - Family string doesn't match any LanguageFamily value
    """
    if (name := detect_language_family(path)) is None:
        return None
    try:
        result: LanguageFamily = _get_language_family()(name)
        return result
    except ValueError:
        return None


# =============================================================================
# Include Glob Generation
# =============================================================================


def _generate_include_globs(lang: Language) -> tuple[str, ...]:
    """Generate include globs from BOTH extensions AND filenames.

    This ensures all declared extensions and filenames are covered by globs.
    """
    globs: list[str] = []

    # Add extension-based globs
    for ext in sorted(lang.extensions):
        globs.append(f"**/*{ext}")

    # Add filename-based globs (exact filename matches at any depth)
    for name in sorted(lang.filenames):
        globs.append(f"**/{name}")
        # Also match common variants (e.g., Dockerfile.dev, Dockerfile.prod)
        if name in ("dockerfile",):
            globs.append(f"**/{name}.*")

    return tuple(globs)


def get_include_globs(name: str) -> tuple[str, ...]:
    """Get include globs for a language name.

    Globs cover both extensions and special filenames.
    """
    if name not in LANGUAGES_BY_NAME:
        return ()
    return _generate_include_globs(LANGUAGES_BY_NAME[name])


# =============================================================================
# Marker Functions
# =============================================================================


def get_markers(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Get (workspace_markers, package_markers) for a name.

    All markers are EXACT filenames (lowercase).
    """
    if (lang := LANGUAGES_BY_NAME.get(name)) is None:
        return (), ()
    return lang.markers_workspace, lang.markers_package


def build_marker_definitions() -> dict[str, dict[str, tuple[str, ...]]]:
    """Build {name: {"workspace": (...), "package": (...)}} for scanner.

    All marker strings are exact lowercase filenames.
    """
    result: dict[str, dict[str, tuple[str, ...]]] = {}
    for lang in ALL_LANGUAGES:
        if lang.markers_workspace or lang.markers_package:
            result[lang.name] = {
                "workspace": lang.markers_workspace,
                "package": lang.markers_package,
            }
    return result


def build_include_specs() -> dict[str, tuple[str, ...]]:
    """Build {name: globs} for scanner.

    Globs cover both extensions and filenames.
    """
    result: dict[str, tuple[str, ...]] = {}
    for lang in ALL_LANGUAGES:
        globs = _generate_include_globs(lang)
        if globs:
            result[lang.name] = globs
    return result


# =============================================================================
# Other Accessors
# =============================================================================


def get_test_patterns(name: str) -> tuple[str, ...]:
    """Get test file patterns for a name."""
    return LANGUAGES_BY_NAME[name].test_patterns if name in LANGUAGES_BY_NAME else ()


def is_test_file(path: str | Path) -> bool:
    """Check if a file path matches any known test file pattern.

    Uses the canonical ``test_patterns`` defined on each ``Language`` in
    ``ALL_LANGUAGES``.  Patterns are ``fnmatch``-style globs matched
    against the filename (e.g. ``test_*.py``, ``*_test.go``).
    Additionally, if a pattern contains ``/`` it is matched against the
    full path string to support directory-style patterns like ``tests/``.

    Args:
        path: File path (string or Path object).

    Returns:
        True if the file matches any test pattern for any language.
    """
    from fnmatch import fnmatch

    p = Path(path) if isinstance(path, str) else path
    name = p.name
    path_str = str(p)

    for lang in ALL_LANGUAGES:
        for pattern in lang.test_patterns:
            if "/" in pattern:
                # Directory-style pattern — use fnmatch against full path
                if fnmatch(path_str, pattern):
                    return True
            else:
                if fnmatch(name, pattern):
                    return True
    return False


def get_grammar_name(name: str) -> str | None:
    """Get tree-sitter grammar name for a name.

    Returns None if no tree-sitter grammar is available for the language.
    """
    return LANGUAGES_BY_NAME[name].grammar if name in LANGUAGES_BY_NAME else None


def has_grammar(name: str) -> bool:
    """Check if name has a usable tree-sitter grammar."""
    return get_grammar_name(name) is not None


def get_all_indexable_extensions() -> set[str]:
    """Get all known file extensions."""
    return set(EXTENSION_TO_NAME.keys())


def get_all_indexable_filenames() -> set[str]:
    """Get all known special filenames."""
    return set(FILENAME_TO_NAME.keys())


# =============================================================================
# Validation (for tests only - NOT run at import time)
# =============================================================================


def validate_language_families() -> list[str]:
    """Validate that all name strings match LanguageFamily enum values.

    Returns list of error messages (empty if valid).
    Call this in a unit test to catch definition/enum mismatches.
    """
    errors: list[str] = []
    try:
        LanguageFamily = _get_language_family()
        enum_values = {e.value for e in LanguageFamily}
        defined_families = set(LANGUAGES_BY_NAME.keys())

        missing_in_enum = defined_families - enum_values
        if missing_in_enum:
            errors.append(
                f"Families defined in languages.py but missing from LanguageFamily enum: "
                f"{sorted(missing_in_enum)}"
            )
    except Exception as e:
        errors.append(f"Could not import LanguageFamily: {e}")

    return errors


def validate_markers_are_exact_filenames() -> list[str]:
    """Validate that all markers are exact filenames (no wildcards/globs).

    Returns list of error messages (empty if valid).
    """
    errors: list[str] = []
    for lang in ALL_LANGUAGES:
        for marker in lang.markers_workspace + lang.markers_package:
            if "*" in marker or "?" in marker:
                errors.append(
                    f"{lang.name}: marker '{marker}' contains wildcards. "
                    f"Markers must be exact filenames."
                )
            if marker.startswith(".") and marker.count(".") == 1 and len(marker) > 1:
                # Looks like an extension (e.g., .csproj)
                errors.append(
                    f"{lang.name}: marker '{marker}' looks like an extension, not a filename. "
                    f"Markers must be exact filenames."
                )
    return errors
