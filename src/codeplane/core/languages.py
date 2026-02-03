"""Canonical language definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from codeplane.index.models import LanguageFamily


def _get_language_family() -> type:
    from codeplane.index.models import LanguageFamily

    return LanguageFamily


@dataclass(frozen=True, slots=True)
class Language:
    family: str
    extensions: frozenset[str]
    filenames: frozenset[str] = frozenset()
    markers_workspace: tuple[str, ...] = ()
    markers_package: tuple[str, ...] = ()
    include_globs: tuple[str, ...] = ()
    grammar: str | None = None
    test_patterns: tuple[str, ...] = ()
    ambient: bool = False


# fmt: off
_LANGS: tuple[tuple[Any, ...], ...] = (
    # (family, extensions, filenames, ws_markers, pkg_markers, globs, grammar, tests, ambient)
    ("python", {".py",".pyi",".pyw",".pyx",".pxd",".pxi"}, {"pipfile","setup.py","pyproject.toml"},
     ("uv.lock","poetry.lock","Pipfile.lock","pdm.lock"),
     ("pyproject.toml","setup.py","setup.cfg","requirements.txt","Pipfile"),
     ("**/*.py","**/*.pyi"), "python", ("test_*.py","*_test.py"), False),
    ("javascript", {".js",".jsx",".ts",".tsx",".mjs",".cjs",".mts",".cts",".vue",".svelte",".astro"},
     {"package.json","deno.json","tsconfig.json","jsconfig.json"},
     ("pnpm-workspace.yaml","lerna.json","nx.json","turbo.json"),
     ("package.json","deno.json","tsconfig.json"),
     ("**/*.js","**/*.jsx","**/*.ts","**/*.tsx","**/*.vue"), "javascript",
     ("*.test.js","*.test.ts","*.spec.js","*.spec.ts"), False),
    ("go", {".go"}, {"go.mod","go.sum"}, ("go.work",), ("go.mod",), ("**/*.go",), "go", ("*_test.go",), False),
    ("rust", {".rs"}, {"cargo.toml","cargo.lock"}, (), ("Cargo.toml",), ("**/*.rs",), "rust", (), False),
    ("jvm", {".java",".kt",".kts",".scala",".sc",".groovy",".gradle"},
     {"build.gradle","build.gradle.kts","pom.xml","build.sbt"},
     ("settings.gradle","settings.gradle.kts"), ("build.gradle","build.gradle.kts","pom.xml","build.sbt"),
     ("**/*.java","**/*.kt","**/*.scala"), "java", ("*Test.java","*Spec.scala","*Test.kt"), False),
    ("dotnet", {".cs",".fs",".fsx",".fsi",".vb"}, set(), (), (), ("**/*.cs","**/*.fs"), "c_sharp",
     ("*Tests.cs","*Test.cs"), False),
    ("cpp", {".c",".h",".cpp",".cc",".cxx",".hpp",".hxx",".hh",".ino",".m",".mm"},
     {"cmakelists.txt","makefile","gnumakefile","meson.build"},
     (), ("CMakeLists.txt","Makefile","meson.build","BUILD","compile_commands.json"),
     ("**/*.cpp","**/*.cc","**/*.c","**/*.h","**/*.hpp"), "cpp", (), False),
    ("ruby", {".rb",".rake",".gemspec",".erb"}, {"gemfile","rakefile","vagrantfile"},
     ("Gemfile.lock",), ("Gemfile",), ("**/*.rb",), "ruby", ("*_spec.rb","*_test.rb"), False),
    ("php", {".php",".phtml"}, set(), ("composer.lock",), ("composer.json",), ("**/*.php",), "php",
     ("*Test.php",), False),
    ("swift", {".swift"}, set(), (), ("Package.swift",), ("**/*.swift",), "swift", ("*Tests.swift",), False),
    ("elixir", {".ex",".exs",".eex",".heex",".erl",".hrl"}, set(), (), ("mix.exs",),
     ("**/*.ex","**/*.exs"), "elixir", ("*_test.exs",), False),
    ("haskell", {".hs",".lhs",".cabal"}, set(), (), ("*.cabal","stack.yaml"), ("**/*.hs",), "haskell", (), False),
    ("ocaml", {".ml",".mli",".mll",".mly",".re",".rei"}, set(), (), ("dune-project",),
     ("**/*.ml","**/*.mli"), "ocaml", (), False),
    ("clojure", {".clj",".cljs",".cljc",".edn"}, set(), (), ("project.clj","deps.edn"),
     ("**/*.clj","**/*.cljs"), None, ("*_test.clj",), False),
    ("elm", {".elm"}, set(), (), ("elm.json",), ("**/*.elm",), None, (), False),
    ("shell", {".sh",".bash",".zsh",".fish",".ksh",".ps1",".psm1"}, set(), (), (),
     ("**/*.sh","**/*.bash"), "bash", (), False),
    ("lua", {".lua",".luau"}, set(), (), (), ("**/*.lua",), "lua", (), False),
    ("perl", {".pl",".pm",".pod",".t"}, set(), (), ("Makefile.PL","Build.PL"), ("**/*.pl","**/*.pm"), None, ("*.t",), False),
    ("r", {".r",".R",".rmd",".Rmd"}, set(), (), ("DESCRIPTION",), ("**/*.R",), None, (), False),
    ("julia", {".jl"}, set(), (), ("Project.toml",), ("**/*.jl",), "julia", (), False),
    ("zig", {".zig"}, set(), (), ("build.zig",), ("**/*.zig",), "zig", (), False),
    ("nim", {".nim",".nims",".nimble"}, set(), (), ("*.nimble",), ("**/*.nim",), None, (), False),
    ("d", {".d",".di"}, set(), (), ("dub.json","dub.sdl"), ("**/*.d",), None, (), False),
    ("ada", {".adb",".ads"}, set(), (), ("*.gpr",), ("**/*.adb","**/*.ads"), "ada", (), False),
    ("fortran", {".f",".f77",".f90",".f95",".f03",".f08"}, set(), (), (), ("**/*.f90",), "fortran", (), False),
    ("pascal", {".pas",".pp",".lpr",".dpr"}, set(), (), (), ("**/*.pas",), None, (), False),
    ("dart", {".dart"}, set(), (), ("pubspec.yaml",), ("**/*.dart",), None, ("*_test.dart",), False),
    ("gleam", {".gleam"}, set(), (), ("gleam.toml",), ("**/*.gleam",), None, (), False),
    ("crystal", {".cr"}, set(), (), ("shard.yml",), ("**/*.cr",), None, ("spec/**/*.cr",), False),
    ("v", {".v",".vv"}, set(), (), ("v.mod",), ("**/*.v",), None, (), False),
    ("odin", {".odin"}, set(), (), (), ("**/*.odin",), "odin", (), False),
    ("html", {".html",".htm",".xhtml",".xml",".xsl",".svg"}, set(), (), (),
     ("**/*.html","**/*.htm","**/*.xml"), "html", (), False),
    ("css", {".css",".scss",".sass",".less"}, set(), (), (), ("**/*.css","**/*.scss"), "css", (), False),
    ("verilog", {".v",".vh",".sv",".svh",".vhd",".vhdl"}, set(), (), (), ("**/*.v","**/*.sv"), "verilog", (), False),
    ("terraform", {".tf",".tfvars",".hcl"}, {"terraform.tfvars"},
     (".terraform.lock.hcl",), ("main.tf","versions.tf"), ("**/*.tf","**/*.hcl"), "hcl", (), False),
    ("sql", {".sql",".mysql",".pgsql"}, set(), (), (), ("**/*.sql",), "sql", (), True),
    ("docker", {".dockerfile"}, {"dockerfile","docker-compose.yml","docker-compose.yaml","compose.yml"},
     (), (), ("**/Dockerfile","**/docker-compose.yml"), "dockerfile", (), True),
    ("markdown", {".md",".mdx",".markdown",".rst",".adoc",".txt"},
     {"readme","readme.md","changelog","license","contributing"},
     (), (), ("**/*.md","**/*.markdown"), "markdown", (), True),
    ("json_yaml", {".json",".jsonc",".json5",".yaml",".yml",".toml"}, set(), (), (),
     ("**/*.json","**/*.yaml","**/*.yml","**/*.toml"), "json", (), True),
    ("protobuf", {".proto"}, set(), ("buf.work.yaml",), ("buf.yaml",), ("**/*.proto",), None, (), False),
    ("graphql", {".graphql",".gql"}, set(), (), (), ("**/*.graphql",), None, (), True),
    ("config", {".ini",".cfg",".conf",".env",".envrc",".properties",".editorconfig",".gitignore",".nix"},
     {".env",".editorconfig",".gitignore","flake.nix"},
     ("flake.lock",), ("flake.nix",), ("**/*.nix",), None, (), False),
    ("make", {".cmake",".meson",".ninja",".bazel",".bzl",".mk"},
     {"makefile","gnumakefile","cmakelists.txt","meson.build","justfile"},
     (), (), (), "make", (), False),
    ("assembly", {".asm",".s",".S",".nasm"}, set(), (), (), ("**/*.asm","**/*.s"), None, (), False),
)
# fmt: on

ALL_LANGUAGES: tuple[Language, ...] = tuple(
    Language(
        family=t[0],
        extensions=frozenset(t[1]),
        filenames=frozenset(t[2]),
        markers_workspace=t[3],
        markers_package=t[4],
        include_globs=t[5],
        grammar=t[6],
        test_patterns=t[7],
        ambient=t[8],
    )
    for t in _LANGS
)
LANGUAGES_BY_FAMILY: dict[str, Language] = {lang.family: lang for lang in ALL_LANGUAGES}


def _build_map(attr: str, lower: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for lang in ALL_LANGUAGES:
        for v in getattr(lang, attr):
            k = v.lower() if lower else v
            if k not in result:
                result[k] = lang.family
    return result


EXTENSION_TO_FAMILY: dict[str, str] = _build_map("extensions")
FILENAME_TO_FAMILY: dict[str, str] = _build_map("filenames", lower=True)
AMBIENT_FAMILIES: frozenset[str] = frozenset(lang.family for lang in ALL_LANGUAGES if lang.ambient)


def detect_language_family(path: str | Path) -> str | None:
    p = Path(path) if isinstance(path, str) else path
    if family := FILENAME_TO_FAMILY.get(p.name.lower()):
        return family
    return EXTENSION_TO_FAMILY.get(p.suffix.lower())


def detect_language_family_enum(path: str | Path) -> LanguageFamily | None:
    if (family := detect_language_family(path)) is None:
        return None
    try:
        result: LanguageFamily = _get_language_family()(family)
        return result
    except ValueError:
        return None


def get_include_globs(family: str) -> tuple[str, ...]:
    return LANGUAGES_BY_FAMILY[family].include_globs if family in LANGUAGES_BY_FAMILY else ()


def get_markers(family: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if (lang := LANGUAGES_BY_FAMILY.get(family)) is None:
        return (), ()
    return lang.markers_workspace, lang.markers_package


def get_test_patterns(family: str) -> tuple[str, ...]:
    return LANGUAGES_BY_FAMILY[family].test_patterns if family in LANGUAGES_BY_FAMILY else ()


def get_grammar_name(family: str) -> str | None:
    return LANGUAGES_BY_FAMILY[family].grammar if family in LANGUAGES_BY_FAMILY else None


def has_grammar(family: str) -> bool:
    return get_grammar_name(family) is not None


def get_all_indexable_extensions() -> set[str]:
    return set(EXTENSION_TO_FAMILY.keys())


def get_all_indexable_filenames() -> set[str]:
    return set(FILENAME_TO_FAMILY.keys())


def build_marker_definitions() -> dict[str, dict[str, tuple[str, ...]]]:
    """Build {family: {"workspace": (...), "package": (...)}} for scanner."""
    result: dict[str, dict[str, tuple[str, ...]]] = {}
    for lang in ALL_LANGUAGES:
        if lang.markers_workspace or lang.markers_package:
            result[lang.family] = {
                "workspace": lang.markers_workspace,
                "package": lang.markers_package,
            }
    return result


def build_include_specs() -> dict[str, tuple[str, ...]]:
    """Build {family: globs} for scanner."""
    return {lang.family: lang.include_globs for lang in ALL_LANGUAGES if lang.include_globs}
