"""Extension-based language detection for fallback context.

Maps file extensions to LanguageFamily for files not claimed by
marker-based contexts. Used by the root fallback context (tier 3).

Covers all major tree-sitter supported languages.
"""

from __future__ import annotations

from pathlib import Path

from codeplane.index.models import LanguageFamily

# Extension to language family mapping
# Comprehensive coverage of tree-sitter supported languages
EXTENSION_TO_FAMILY: dict[str, LanguageFamily] = {
    # Python
    ".py": LanguageFamily.PYTHON,
    ".pyi": LanguageFamily.PYTHON,
    ".pyw": LanguageFamily.PYTHON,
    ".pyx": LanguageFamily.PYTHON,
    ".pxd": LanguageFamily.PYTHON,
    ".pxi": LanguageFamily.PYTHON,
    # JavaScript/TypeScript
    ".js": LanguageFamily.JAVASCRIPT,
    ".jsx": LanguageFamily.JAVASCRIPT,
    ".ts": LanguageFamily.JAVASCRIPT,
    ".tsx": LanguageFamily.JAVASCRIPT,
    ".mjs": LanguageFamily.JAVASCRIPT,
    ".cjs": LanguageFamily.JAVASCRIPT,
    ".vue": LanguageFamily.JAVASCRIPT,
    ".svelte": LanguageFamily.JAVASCRIPT,
    ".astro": LanguageFamily.JAVASCRIPT,
    # Go
    ".go": LanguageFamily.GO,
    # Rust
    ".rs": LanguageFamily.RUST,
    # JVM
    ".java": LanguageFamily.JVM,
    ".kt": LanguageFamily.JVM,
    ".kts": LanguageFamily.JVM,
    ".scala": LanguageFamily.JVM,
    ".sc": LanguageFamily.JVM,
    ".groovy": LanguageFamily.JVM,
    ".gradle": LanguageFamily.JVM,
    ".clj": LanguageFamily.CLOJURE,
    ".cljs": LanguageFamily.CLOJURE,
    ".cljc": LanguageFamily.CLOJURE,
    ".edn": LanguageFamily.CLOJURE,
    # .NET
    ".cs": LanguageFamily.DOTNET,
    ".fs": LanguageFamily.DOTNET,
    ".fsx": LanguageFamily.DOTNET,
    ".fsi": LanguageFamily.DOTNET,
    ".vb": LanguageFamily.DOTNET,
    # C/C++/Objective-C
    ".c": LanguageFamily.CPP,
    ".h": LanguageFamily.CPP,
    ".cpp": LanguageFamily.CPP,
    ".cc": LanguageFamily.CPP,
    ".cxx": LanguageFamily.CPP,
    ".c++": LanguageFamily.CPP,
    ".hpp": LanguageFamily.CPP,
    ".hxx": LanguageFamily.CPP,
    ".hh": LanguageFamily.CPP,
    ".h++": LanguageFamily.CPP,
    ".ino": LanguageFamily.CPP,  # Arduino
    ".m": LanguageFamily.CPP,  # Objective-C
    ".mm": LanguageFamily.CPP,  # Objective-C++
    # Ruby
    ".rb": LanguageFamily.RUBY,
    ".rake": LanguageFamily.RUBY,
    ".gemspec": LanguageFamily.RUBY,
    ".podspec": LanguageFamily.RUBY,
    ".jbuilder": LanguageFamily.RUBY,
    ".erb": LanguageFamily.RUBY,
    # PHP
    ".php": LanguageFamily.PHP,
    ".phtml": LanguageFamily.PHP,
    ".php3": LanguageFamily.PHP,
    ".php4": LanguageFamily.PHP,
    ".php5": LanguageFamily.PHP,
    ".phps": LanguageFamily.PHP,
    # Swift
    ".swift": LanguageFamily.SWIFT,
    # Elixir/Erlang
    ".ex": LanguageFamily.ELIXIR,
    ".exs": LanguageFamily.ELIXIR,
    ".eex": LanguageFamily.ELIXIR,
    ".heex": LanguageFamily.ELIXIR,
    ".leex": LanguageFamily.ELIXIR,
    ".erl": LanguageFamily.ELIXIR,
    ".hrl": LanguageFamily.ELIXIR,
    # Haskell
    ".hs": LanguageFamily.HASKELL,
    ".lhs": LanguageFamily.HASKELL,
    ".cabal": LanguageFamily.HASKELL,
    # OCaml/ReasonML
    ".ml": LanguageFamily.OCAML,
    ".mli": LanguageFamily.OCAML,
    ".mll": LanguageFamily.OCAML,
    ".mly": LanguageFamily.OCAML,
    ".re": LanguageFamily.OCAML,
    ".rei": LanguageFamily.OCAML,
    # Elm
    ".elm": LanguageFamily.ELM,
    # Shell
    ".sh": LanguageFamily.SHELL,
    ".bash": LanguageFamily.SHELL,
    ".zsh": LanguageFamily.SHELL,
    ".fish": LanguageFamily.SHELL,
    ".ksh": LanguageFamily.SHELL,
    ".csh": LanguageFamily.SHELL,
    ".tcsh": LanguageFamily.SHELL,
    ".ps1": LanguageFamily.SHELL,
    ".psm1": LanguageFamily.SHELL,
    ".psd1": LanguageFamily.SHELL,
    # Lua
    ".lua": LanguageFamily.LUA,
    ".luau": LanguageFamily.LUA,
    ".nse": LanguageFamily.LUA,  # Nmap scripts
    # Perl
    ".pl": LanguageFamily.PERL,
    ".pm": LanguageFamily.PERL,
    ".pod": LanguageFamily.PERL,
    ".t": LanguageFamily.PERL,
    ".psgi": LanguageFamily.PERL,
    # R
    ".r": LanguageFamily.R,
    ".R": LanguageFamily.R,
    ".rmd": LanguageFamily.R,
    ".Rmd": LanguageFamily.R,
    ".rnw": LanguageFamily.R,
    ".Rnw": LanguageFamily.R,
    # Julia
    ".jl": LanguageFamily.JULIA,
    # Zig
    ".zig": LanguageFamily.ZIG,
    # Nim
    ".nim": LanguageFamily.NIM,
    ".nims": LanguageFamily.NIM,
    ".nimble": LanguageFamily.NIM,
    # D
    ".d": LanguageFamily.D,
    ".di": LanguageFamily.D,
    # Ada
    ".adb": LanguageFamily.ADA,
    ".ads": LanguageFamily.ADA,
    # Fortran
    ".f": LanguageFamily.FORTRAN,
    ".f77": LanguageFamily.FORTRAN,
    ".f90": LanguageFamily.FORTRAN,
    ".f95": LanguageFamily.FORTRAN,
    ".f03": LanguageFamily.FORTRAN,
    ".f08": LanguageFamily.FORTRAN,
    ".for": LanguageFamily.FORTRAN,
    ".ftn": LanguageFamily.FORTRAN,
    ".fpp": LanguageFamily.FORTRAN,
    # Pascal
    ".pas": LanguageFamily.PASCAL,
    ".pp": LanguageFamily.PASCAL,
    ".inc": LanguageFamily.PASCAL,
    ".lpr": LanguageFamily.PASCAL,
    ".dpr": LanguageFamily.PASCAL,
    ".dpk": LanguageFamily.PASCAL,
    # Dart
    ".dart": LanguageFamily.DART,
    # Gleam
    ".gleam": LanguageFamily.GLEAM,
    # Crystal
    ".cr": LanguageFamily.CRYSTAL,
    # V
    ".v": LanguageFamily.V,
    ".vv": LanguageFamily.V,
    # Odin
    ".odin": LanguageFamily.ODIN,
    # HTML/XML
    ".html": LanguageFamily.HTML,
    ".htm": LanguageFamily.HTML,
    ".xhtml": LanguageFamily.HTML,
    ".xml": LanguageFamily.HTML,
    ".xsl": LanguageFamily.HTML,
    ".xslt": LanguageFamily.HTML,
    ".svg": LanguageFamily.HTML,
    ".rss": LanguageFamily.HTML,
    ".atom": LanguageFamily.HTML,
    ".plist": LanguageFamily.HTML,
    ".wsdl": LanguageFamily.HTML,
    # CSS
    ".css": LanguageFamily.CSS,
    ".scss": LanguageFamily.CSS,
    ".sass": LanguageFamily.CSS,
    ".less": LanguageFamily.CSS,
    ".styl": LanguageFamily.CSS,
    ".stylus": LanguageFamily.CSS,
    # Hardware description (note: .v could also be V lang - we default to Verilog)
    ".vh": LanguageFamily.VERILOG,
    ".sv": LanguageFamily.VERILOG,
    ".svh": LanguageFamily.VERILOG,
    ".vhd": LanguageFamily.VERILOG,
    ".vhdl": LanguageFamily.VERILOG,
    # Terraform/HCL
    ".tf": LanguageFamily.TERRAFORM,
    ".tfvars": LanguageFamily.TERRAFORM,
    ".hcl": LanguageFamily.TERRAFORM,
    # SQL
    ".sql": LanguageFamily.SQL,
    ".mysql": LanguageFamily.SQL,
    ".pgsql": LanguageFamily.SQL,
    ".plsql": LanguageFamily.SQL,
    ".cql": LanguageFamily.SQL,  # Cassandra
    # Docker
    ".dockerfile": LanguageFamily.DOCKER,
    # Markdown/docs
    ".md": LanguageFamily.MARKDOWN,
    ".mdx": LanguageFamily.MARKDOWN,
    ".markdown": LanguageFamily.MARKDOWN,
    ".rst": LanguageFamily.MARKDOWN,
    ".adoc": LanguageFamily.MARKDOWN,
    ".asciidoc": LanguageFamily.MARKDOWN,
    ".org": LanguageFamily.MARKDOWN,
    ".txt": LanguageFamily.MARKDOWN,
    ".text": LanguageFamily.MARKDOWN,
    # Config/Data
    ".json": LanguageFamily.JSON_YAML,
    ".jsonc": LanguageFamily.JSON_YAML,
    ".json5": LanguageFamily.JSON_YAML,
    ".yaml": LanguageFamily.JSON_YAML,
    ".yml": LanguageFamily.JSON_YAML,
    ".toml": LanguageFamily.JSON_YAML,
    ".ini": LanguageFamily.CONFIG,
    ".cfg": LanguageFamily.CONFIG,
    ".conf": LanguageFamily.CONFIG,
    ".config": LanguageFamily.CONFIG,
    ".env": LanguageFamily.CONFIG,
    ".envrc": LanguageFamily.CONFIG,
    ".properties": LanguageFamily.CONFIG,
    ".editorconfig": LanguageFamily.CONFIG,
    ".gitignore": LanguageFamily.CONFIG,
    ".gitattributes": LanguageFamily.CONFIG,
    ".gitmodules": LanguageFamily.CONFIG,
    ".dockerignore": LanguageFamily.CONFIG,
    ".npmignore": LanguageFamily.CONFIG,
    ".prettierrc": LanguageFamily.CONFIG,
    ".eslintrc": LanguageFamily.CONFIG,
    ".babelrc": LanguageFamily.CONFIG,
    # Protobuf
    ".proto": LanguageFamily.PROTOBUF,
    # GraphQL
    ".graphql": LanguageFamily.GRAPHQL,
    ".gql": LanguageFamily.GRAPHQL,
    # Build systems
    ".cmake": LanguageFamily.MAKE,
    ".meson": LanguageFamily.MAKE,
    ".ninja": LanguageFamily.MAKE,
    ".bazel": LanguageFamily.MAKE,
    ".bzl": LanguageFamily.MAKE,
    ".mk": LanguageFamily.MAKE,
    # Assembly
    ".asm": LanguageFamily.ASSEMBLY,
    ".s": LanguageFamily.ASSEMBLY,
    ".S": LanguageFamily.ASSEMBLY,
    ".nasm": LanguageFamily.ASSEMBLY,
    # Nix
    ".nix": LanguageFamily.CONFIG,
}

# Special filename patterns (case-insensitive)
FILENAME_TO_FAMILY: dict[str, LanguageFamily] = {
    # Docker
    "dockerfile": LanguageFamily.DOCKER,
    "docker-compose.yml": LanguageFamily.DOCKER,
    "docker-compose.yaml": LanguageFamily.DOCKER,
    "compose.yml": LanguageFamily.DOCKER,
    "compose.yaml": LanguageFamily.DOCKER,
    "containerfile": LanguageFamily.DOCKER,
    # Build systems
    "makefile": LanguageFamily.MAKE,
    "gnumakefile": LanguageFamily.MAKE,
    "cmakelists.txt": LanguageFamily.MAKE,
    "meson.build": LanguageFamily.MAKE,
    "build.ninja": LanguageFamily.MAKE,
    "justfile": LanguageFamily.MAKE,
    "taskfile.yml": LanguageFamily.MAKE,
    "taskfile.yaml": LanguageFamily.MAKE,
    "build.gradle": LanguageFamily.JVM,
    "build.gradle.kts": LanguageFamily.JVM,
    "settings.gradle": LanguageFamily.JVM,
    "settings.gradle.kts": LanguageFamily.JVM,
    "pom.xml": LanguageFamily.JVM,
    "build.sbt": LanguageFamily.JVM,
    # Ruby
    "gemfile": LanguageFamily.RUBY,
    "rakefile": LanguageFamily.RUBY,
    "guardfile": LanguageFamily.RUBY,
    "vagrantfile": LanguageFamily.RUBY,
    "brewfile": LanguageFamily.RUBY,
    "fastfile": LanguageFamily.RUBY,
    "appfile": LanguageFamily.RUBY,
    "matchfile": LanguageFamily.RUBY,
    "podfile": LanguageFamily.RUBY,
    # Python
    "pipfile": LanguageFamily.PYTHON,
    "setup.py": LanguageFamily.PYTHON,
    "pyproject.toml": LanguageFamily.PYTHON,
    # JavaScript/Node
    "package.json": LanguageFamily.JAVASCRIPT,
    "tsconfig.json": LanguageFamily.JAVASCRIPT,
    "jsconfig.json": LanguageFamily.JAVASCRIPT,
    ".eslintrc.json": LanguageFamily.JAVASCRIPT,
    ".prettierrc.json": LanguageFamily.JAVASCRIPT,
    "vite.config.js": LanguageFamily.JAVASCRIPT,
    "vite.config.ts": LanguageFamily.JAVASCRIPT,
    "webpack.config.js": LanguageFamily.JAVASCRIPT,
    "rollup.config.js": LanguageFamily.JAVASCRIPT,
    # Go
    "go.mod": LanguageFamily.GO,
    "go.sum": LanguageFamily.GO,
    # Rust
    "cargo.toml": LanguageFamily.RUST,
    "cargo.lock": LanguageFamily.RUST,
    # Config
    ".env": LanguageFamily.CONFIG,
    ".env.local": LanguageFamily.CONFIG,
    ".env.development": LanguageFamily.CONFIG,
    ".env.production": LanguageFamily.CONFIG,
    ".editorconfig": LanguageFamily.CONFIG,
    ".gitignore": LanguageFamily.CONFIG,
    ".gitattributes": LanguageFamily.CONFIG,
    # Terraform
    "terraform.tfvars": LanguageFamily.TERRAFORM,
    # Markdown
    "readme": LanguageFamily.MARKDOWN,
    "readme.md": LanguageFamily.MARKDOWN,
    "readme.txt": LanguageFamily.MARKDOWN,
    "changelog": LanguageFamily.MARKDOWN,
    "changelog.md": LanguageFamily.MARKDOWN,
    "license": LanguageFamily.MARKDOWN,
    "license.md": LanguageFamily.MARKDOWN,
    "license.txt": LanguageFamily.MARKDOWN,
    "contributing": LanguageFamily.MARKDOWN,
    "contributing.md": LanguageFamily.MARKDOWN,
}


def detect_language_family(path: str | Path) -> LanguageFamily | None:
    """Detect language family from file path.

    Uses extension-based detection with special handling for certain filenames.

    Args:
        path: File path (relative or absolute)

    Returns:
        LanguageFamily if detected, None otherwise
    """
    p = Path(path)
    name_lower = p.name.lower()

    # Check special filenames first
    if name_lower in FILENAME_TO_FAMILY:
        return FILENAME_TO_FAMILY[name_lower]

    # Check extension
    ext = p.suffix.lower()
    if ext in EXTENSION_TO_FAMILY:
        return EXTENSION_TO_FAMILY[ext]

    return None


def get_all_indexable_extensions() -> set[str]:
    """Get all file extensions we can index."""
    return set(EXTENSION_TO_FAMILY.keys())


def get_all_indexable_filenames() -> set[str]:
    """Get all special filenames we can index."""
    return set(FILENAME_TO_FAMILY.keys())
