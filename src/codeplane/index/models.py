"""SQLModel definitions for the Tier 0 + Tier 1 stacked index.

Single source of truth for all table schemas. See SPEC.md §7 for architecture.

Architecture:
- Tier 0: Tantivy lexical index (always-on, candidate discovery)
- Tier 1: Tree-sitter/SQLite structural facts (defs, refs, scopes, binds, imports, exports)

This index provides syntactic facts only. No semantic resolution, no call graph,
no type information. It enables a future refactor planner but provides no
semantic guarantees itself.
"""

import json
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Integer
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    pass


# ============================================================================
# ENUMS
# ============================================================================


class LanguageFamily(StrEnum):
    """Canonical language name identifiers.

    All languages get lexical indexing (full-text search).
    Languages with grammar != None also get structural indexing (symbols, refs).

    NOTE: These values MUST match the 'name' strings in core/languages.py.
    Run validate_language_families() to check for mismatches.
    """

    # --- Code families - mainstream ---
    JAVASCRIPT = "javascript"  # JS, TS, JSX, TSX, Vue, Svelte
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    # JVM languages (split for correct grammar selection)
    JAVA = "java"
    KOTLIN = "kotlin"
    SCALA = "scala"
    GROOVY = "groovy"
    # .NET languages (split for correct grammar selection)
    CSHARP = "csharp"
    FSHARP = "fsharp"
    VBNET = "vbnet"
    RUBY = "ruby"
    PHP = "php"
    SWIFT = "swift"
    C_CPP = "c_cpp"  # C and C++ unified (header files are ambiguous)
    OBJC = "objc"  # Objective-C, Objective-C++
    MATLAB = "matlab"  # MATLAB/Octave

    # --- Code families - functional ---
    ELIXIR = "elixir"
    ERLANG = "erlang"
    HASKELL = "haskell"
    OCAML = "ocaml"
    REASON = "reason"  # ReasonML
    CLOJURE = "clojure"
    ELM = "elm"

    # --- Code families - scripting ---
    SHELL = "shell"  # Bash, Zsh, Ksh (POSIX-compatible)
    FISH = "fish"  # Fish shell (separate - incompatible syntax)
    POWERSHELL = "powershell"
    LUA = "lua"
    PERL = "perl"
    R = "r"
    JULIA = "julia"

    # --- Code families - systems ---
    ZIG = "zig"
    NIM = "nim"
    D = "d"
    ADA = "ada"
    FORTRAN = "fortran"
    PASCAL = "pascal"

    # --- Code families - other ---
    DART = "dart"
    GLEAM = "gleam"
    CRYSTAL = "crystal"
    VLANG = "vlang"  # V language
    ODIN = "odin"

    # --- Hardware description ---
    VERILOG = "verilog"  # Verilog, SystemVerilog
    VHDL = "vhdl"

    # --- Web/markup ---
    HTML = "html"
    XML = "xml"
    CSS = "css"  # CSS, SCSS, Less

    # --- Data/config families ---
    TERRAFORM = "terraform"
    HCL = "hcl"
    SQL = "sql"
    DOCKER = "docker"
    MARKDOWN = "markdown"
    RST = "rst"  # reStructuredText
    ASCIIDOC = "asciidoc"
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    PROTOBUF = "protobuf"
    GRAPHQL = "graphql"
    NIX = "nix"

    # --- Build systems ---
    MAKE = "make"
    CMAKE = "cmake"
    MESON = "meson"
    BAZEL = "bazel"  # Starlark
    JUST = "just"

    # --- Other ---
    ASSEMBLY = "assembly"
    UNKNOWN = "unknown"  # Root fallback for files not claimed by any project

    @classmethod
    def code_families(cls) -> "frozenset[LanguageFamily]":
        """Return code families (programming languages)."""
        return frozenset(
            {
                # Mainstream
                cls.JAVASCRIPT,
                cls.PYTHON,
                cls.GO,
                cls.RUST,
                # JVM languages
                cls.JAVA,
                cls.KOTLIN,
                cls.SCALA,
                cls.GROOVY,
                # .NET languages
                cls.CSHARP,
                cls.FSHARP,
                cls.VBNET,
                cls.RUBY,
                cls.PHP,
                cls.SWIFT,
                cls.C_CPP,
                cls.OBJC,
                cls.MATLAB,
                # Functional
                cls.ELIXIR,
                cls.ERLANG,
                cls.HASKELL,
                cls.OCAML,
                cls.REASON,
                cls.CLOJURE,
                cls.ELM,
                # Scripting
                cls.SHELL,
                cls.FISH,
                cls.POWERSHELL,
                cls.LUA,
                cls.PERL,
                cls.R,
                cls.JULIA,
                # Systems
                cls.ZIG,
                cls.NIM,
                cls.D,
                cls.ADA,
                cls.FORTRAN,
                cls.PASCAL,
                # Other
                cls.DART,
                cls.GLEAM,
                cls.CRYSTAL,
                cls.VLANG,
                cls.ODIN,
                # Hardware
                cls.VERILOG,
                cls.VHDL,
                cls.ASSEMBLY,
            }
        )

    @classmethod
    def data_families(cls) -> "frozenset[LanguageFamily]":
        """Return data/config families."""
        return frozenset(
            {
                cls.TERRAFORM,
                cls.HCL,
                cls.SQL,
                cls.DOCKER,
                cls.MARKDOWN,
                cls.RST,
                cls.ASCIIDOC,
                cls.JSON,
                cls.YAML,
                cls.TOML,
                cls.PROTOBUF,
                cls.GRAPHQL,
                cls.NIX,
                cls.HTML,
                cls.XML,
                cls.CSS,
                cls.MAKE,
                cls.CMAKE,
                cls.MESON,
                cls.BAZEL,
                cls.JUST,
            }
        )

    @property
    def is_code(self) -> bool:
        """True if this is a code name."""
        return self in self.code_families()

    @property
    def is_data(self) -> bool:
        """True if this is a data name."""
        return self in self.data_families()


class Freshness(StrEnum):
    """Index currency state."""

    CLEAN = "clean"
    DIRTY = "dirty"
    STALE = "stale"
    PENDING_CHECK = "pending_check"
    UNINDEXED = "unindexed"


class Certainty(StrEnum):
    """Confidence level for facts."""

    CERTAIN = "certain"
    UNCERTAIN = "uncertain"


class RefTier(StrEnum):
    """Reference tier classification (assigned at index time, never upgraded at query time)."""

    PROVEN = "proven"  # Same-file lexical bind with LocalBindFact certainty=CERTAIN
    STRONG = "strong"  # Cross-file with explicit ImportFact + ExportSurface trace
    ANCHORED = "anchored"  # Ambiguous but grouped in AnchorGroup
    UNKNOWN = "unknown"  # Cannot classify


class Role(StrEnum):
    """Reference role in source code."""

    DEFINITION = "definition"
    REFERENCE = "reference"
    IMPORT = "import"
    EXPORT = "export"


class ScopeKind(StrEnum):
    """Lexical scope kind."""

    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    BLOCK = "block"
    COMPREHENSION = "comprehension"
    LAMBDA = "lambda"


class BindTargetKind(StrEnum):
    """Target kind for LocalBindFact."""

    DEF = "def"  # Bound to a DefFact
    IMPORT = "import"  # Bound to an ImportFact
    UNKNOWN = "unknown"  # Cannot determine


class BindReasonCode(StrEnum):
    """Reason for binding classification."""

    PARAM = "param"  # Function parameter
    LOCAL_ASSIGN = "local_assign"  # Assignment target
    DEF_IN_SCOPE = "def_in_scope"  # Definition in enclosing scope
    IMPORT_ALIAS = "import_alias"  # Import alias
    FOR_TARGET = "for_target"  # For loop target
    WITH_AS = "with_as"  # With statement alias
    EXCEPT_AS = "except_as"  # Exception handler alias


class ImportKind(StrEnum):
    """Import statement kind."""

    PYTHON_IMPORT = "python_import"  # import foo
    PYTHON_FROM = "python_from"  # from foo import bar
    JS_IMPORT = "js_import"  # import { foo } from 'bar'
    JS_REQUIRE = "js_require"  # const foo = require('bar')
    TS_IMPORT_TYPE = "ts_import_type"  # import type { Foo } from 'bar'
    GO_IMPORT = "go_import"  # import "foo"
    RUST_USE = "rust_use"  # use foo::bar
    CSHARP_USING = "csharp_using"  # using Namespace;
    CSHARP_USING_STATIC = "csharp_using_static"  # using static Namespace.Type;
    # Tier 1 languages - high impact
    JAVA_IMPORT = "java_import"  # import com.foo.Bar;
    JAVA_IMPORT_STATIC = "java_import_static"  # import static com.foo.Bar.baz;
    # Tier 2 languages - commonly encountered
    KOTLIN_IMPORT = "kotlin_import"  # import com.foo.Bar
    RUBY_REQUIRE = "ruby_require"  # require 'foo'
    RUBY_REQUIRE_RELATIVE = "ruby_require_relative"  # require_relative 'foo'
    PHP_USE = "php_use"  # use Namespace\Class;
    SWIFT_IMPORT = "swift_import"  # import Foundation
    # Tier 3 languages - niche
    SCALA_IMPORT = "scala_import"  # import com.foo.{Bar, Baz => B}
    ELIXIR_IMPORT = "elixir_import"  # import Module / alias Module / use Module
    HASKELL_IMPORT = "haskell_import"  # import Module
    OCAML_OPEN = "ocaml_open"  # open Module
    LUA_REQUIRE = "lua_require"  # require("module")
    JULIA_USING = "julia_using"  # using Module / import Module
    # Special - textual inclusion
    C_INCLUDE = "c_include"  # #include <header> / #include "header"


class ExportThunkMode(StrEnum):
    """Re-export mode for ExportThunk."""

    REEXPORT_ALL = "reexport_all"  # export * from 'module'
    EXPLICIT_NAMES = "explicit_names"  # export { a, b } from 'module'
    ALIAS_MAP = "alias_map"  # export { a as x, b as y } from 'module'


class DynamicAccessPattern(StrEnum):
    """Dynamic access pattern types (telemetry only)."""

    BRACKET_ACCESS = "bracket_access"  # obj[key]
    GETATTR = "getattr"  # getattr(obj, name)
    REFLECT = "reflect"  # Reflect.get(obj, name)
    EVAL = "eval"  # eval(), exec()
    IMPORT_MODULE = "import_module"  # importlib.import_module(var)


class ProbeStatus(StrEnum):
    """Context probe status."""

    PENDING = "pending"
    VALID = "valid"
    FAILED = "failed"
    EMPTY = "empty"
    DETACHED = "detached"


class MarkerTier(StrEnum):
    """Marker tier for context discovery hierarchy."""

    WORKSPACE = "workspace"
    PACKAGE = "package"


class AccessStyle(StrEnum):
    """Member access syntax style."""

    DOT = "dot"  # obj.member (most languages)
    ARROW = "arrow"  # obj->member (C, C++, PHP)
    SCOPE = "scope"  # Namespace::member (C++, Rust, PHP)
    BRACKET = "bracket"  # obj["member"] (dynamic access)


class ResolutionMethod(StrEnum):
    """How a reference was resolved to its target."""

    TYPE_TRACED = "type_traced"  # Via type annotation chain
    IMPORT_TRACED = "import_traced"  # Via import resolution
    INTERFACE_MATCHED = "interface_matched"  # Via interface/trait impl
    SHAPE_MATCHED = "shape_matched"  # Via duck-type shape inference
    LEXICAL = "lexical"  # Lexical search only (lowest confidence)
    UNRESOLVED = "unresolved"  # Could not resolve


class AnnotationTargetKind(StrEnum):
    """What kind of entity has a type annotation."""

    PARAMETER = "parameter"  # Function/method parameter
    VARIABLE = "variable"  # Local variable
    FIELD = "field"  # Class/struct field
    PROPERTY = "property"  # Property (getter/setter)
    RETURN = "return"  # Function return type
    CLASS_VAR = "class_var"  # Class-level variable


class MemberKind(StrEnum):
    """Kind of type member."""

    FIELD = "field"  # Instance field
    METHOD = "method"  # Instance method
    PROPERTY = "property"  # Property
    STATIC_FIELD = "static_field"  # Static/class field
    STATIC_METHOD = "static_method"  # Static method
    CLASS_METHOD = "class_method"  # Class method (Python)
    CONSTRUCTOR = "constructor"  # Constructor/initializer


class TypeParentKind(StrEnum):
    """Kind of type that contains members."""

    CLASS = "class"
    STRUCT = "struct"
    INTERFACE = "interface"
    TRAIT = "trait"
    PROTOCOL = "protocol"  # Swift
    MODULE = "module"  # Module-level namespace
    ENUM = "enum"


class ImplStyle(StrEnum):
    """How interface implementation is declared."""

    EXPLICIT = "explicit"  # implements/impl keyword
    STRUCTURAL = "structural"  # Go-style implicit (shape match)
    INFERRED = "inferred"  # Type inference


# ============================================================================
# TIER 1 FACT TABLES (per SPEC.md §7.3)
# ============================================================================


class File(SQLModel, table=True):
    """Tracked file in the repository."""

    __tablename__ = "files"

    id: int | None = Field(default=None, primary_key=True)
    path: str = Field(unique=True, index=True)
    language_family: str | None = None
    content_hash: str | None = None
    line_count: int | None = None
    indexed_at: float | None = None
    last_indexed_epoch: int | None = Field(default=None, index=True)
    declared_module: str | None = Field(
        default=None,
        index=True,
        description="Language-level module/package identity extracted from source "
        "(e.g. 'cats.effect' from Scala `package cats.effect`, "
        "'Newtonsoft.Json' from C# `namespace Newtonsoft.Json`). "
        "NULL for languages without declarations (JS/TS, C/C++) "
        "or files missing declarations.",
    )

    # Relationships
    defs: list["DefFact"] = Relationship(back_populates="file")
    refs: list["RefFact"] = Relationship(back_populates="file")
    scopes: list["ScopeFact"] = Relationship(back_populates="file")
    binds: list["LocalBindFact"] = Relationship(back_populates="file")
    imports: list["ImportFact"] = Relationship(back_populates="file")
    dynamic_sites: list["DynamicAccessSite"] = Relationship(back_populates="file")


class TestTarget(SQLModel, table=True):
    """Test target discovered during indexing."""

    __tablename__ = "test_targets"

    id: int | None = Field(default=None, primary_key=True)
    context_id: int = Field(foreign_key="contexts.id", index=True)
    target_id: str = Field(unique=True, index=True)  # Stable ID for run()
    selector: str  # Path or pattern
    kind: str = Field(index=True)  # "file", "directory", "module"
    language: str = Field(index=True)
    runner_pack_id: str = Field(index=True)  # "python.pytest", "js.vitest", etc.
    workspace_root: str
    estimated_cost: str = "medium"  # "low", "medium", "high"
    test_count: int | None = None
    path: str | None = None  # File path if kind is "file"
    discovered_at: float | None = None


class IndexedLintTool(SQLModel, table=True):
    """Lint tool discovered during indexing."""

    __tablename__ = "indexed_lint_tools"

    id: int | None = Field(default=None, primary_key=True)
    tool_id: str = Field(unique=True, index=True)  # "python.ruff", "js.eslint", etc.
    name: str
    category: str = Field(index=True)  # "lint", "format", "type_check", "security"
    languages: str  # JSON array of language strings
    executable: str
    workspace_root: str
    config_file: str | None = None  # Which config file triggered detection
    discovered_at: float | None = None


class IndexedCoverageCapability(SQLModel, table=True):
    """Coverage capability discovered during indexing.

    Stores whether coverage tools are available for each (workspace, runner_pack) pair.
    Queried at execution time instead of re-detecting tools per invocation.
    """

    __tablename__ = "indexed_coverage_capabilities"

    id: int | None = Field(default=None, primary_key=True)
    workspace_root: str = Field(index=True)
    runner_pack_id: str = Field(index=True)  # "python.pytest", "js.jest", etc.
    tools_json: str  # JSON dict of tool_name -> is_available
    discovered_at: float | None = None

    def get_tools(self) -> dict[str, bool]:
        """Parse tools_json to dict."""
        if self.tools_json is None:
            return {}
        result: dict[str, bool] = json.loads(self.tools_json)
        return result

    def set_tools(self, tools: dict[str, bool]) -> None:
        """Set tools_json from dict."""
        self.tools_json = json.dumps(tools) if tools else "{}"


class Context(SQLModel, table=True):
    """Indexing context (package, workspace, etc) - represents a build unit."""

    __tablename__ = "contexts"

    id: int | None = Field(default=None, primary_key=True)
    name: str | None = None
    language_family: str = Field(index=True)
    root_path: str = Field(index=True)
    tier: int | None = None
    probe_status: str = Field(default=ProbeStatus.PENDING.value, index=True)
    include_spec: str | None = None
    exclude_spec: str | None = None
    config_hash: str | None = None
    refreshed_at: float | None = None

    # Relationships
    markers: list["ContextMarker"] = Relationship(back_populates="context")

    def get_include_globs(self) -> list[str]:
        """Parse include_spec JSON to list."""
        if self.include_spec is None:
            return []
        result: list[str] = json.loads(self.include_spec)
        return result

    def get_exclude_globs(self) -> list[str]:
        """Parse exclude_spec JSON to list."""
        if self.exclude_spec is None:
            return []
        result: list[str] = json.loads(self.exclude_spec)
        return result


class ContextMarker(SQLModel, table=True):
    """Marker file that triggered context discovery."""

    __tablename__ = "context_markers"

    id: int | None = Field(default=None, primary_key=True)
    context_id: int = Field(foreign_key="contexts.id", index=True)
    marker_path: str
    marker_tier: str
    detected_at: float | None = None

    # Relationships
    context: Context | None = Relationship(back_populates="markers")


class DefFact(SQLModel, table=True):
    """Definition fact (function, class, method, variable). See SPEC.md §7.3.1."""

    __tablename__ = "def_facts"

    def_uid: str = Field(primary_key=True)  # Stable identity (see §7.4)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    kind: str = Field(index=True)  # function, class, method, variable, etc.
    name: str = Field(index=True)  # Simple name
    qualified_name: str | None = None  # Full path (e.g., module.Class.method)
    lexical_path: str = Field(index=True)  # Syntactic nesting path for identity
    namespace: str | None = Field(
        default=None, index=True
    )  # Declaring namespace/package (C#, Java, Go, etc.)
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    signature_hash: str | None = None  # Hash of syntactic signature
    display_name: str | None = None  # Human-readable form
    signature_text: str | None = None  # Raw signature text (e.g. '(self, x: int, y: int)')
    decorators_json: str | None = None  # JSON array of decorator/annotation strings
    docstring: str | None = None  # First paragraph of docstring text
    return_type: str | None = None  # Return type annotation text

    # Relationships
    file: File | None = Relationship(back_populates="defs")
    # Note: refs relationship removed - use FactQueries.list_refs_by_def_uid() instead


class RefFact(SQLModel, table=True):
    """Reference fact (identifier occurrence). See SPEC.md §7.3.2."""

    __tablename__ = "ref_facts"

    ref_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    scope_id: int | None = Field(default=None, foreign_key="scope_facts.scope_id", index=True)
    token_text: str = Field(index=True)  # Exact text slice from source
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    role: str = Field(index=True)  # DEFINITION, REFERENCE, IMPORT, EXPORT
    ref_tier: str = Field(
        default=RefTier.UNKNOWN.value, index=True
    )  # PROVEN, STRONG, ANCHORED, UNKNOWN
    certainty: str = Field(default=Certainty.CERTAIN.value)
    target_def_uid: str | None = Field(
        default=None, index=True
    )  # Target def_uid (not FK, join manually)

    # Relationships
    file: File | None = Relationship(back_populates="refs")
    scope: "ScopeFact" = Relationship(back_populates="refs")
    # Note: target_def relationship removed - use FactQueries.get_def() instead


class ScopeFact(SQLModel, table=True):
    """Lexical scope fact. See SPEC.md §7.3.3."""

    __tablename__ = "scope_facts"

    scope_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    parent_scope_id: int | None = Field(default=None, index=True)  # NULL for file scope
    kind: str = Field(index=True)  # file, class, function, block, etc.
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    # Relationships
    file: File | None = Relationship(back_populates="scopes")
    refs: list[RefFact] = Relationship(back_populates="scope")
    binds: list["LocalBindFact"] = Relationship(back_populates="scope")


class LocalBindFact(SQLModel, table=True):
    """Same-file binding fact (index-time only, NO query-time inference). See SPEC.md §7.3.4."""

    __tablename__ = "local_bind_facts"

    bind_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    scope_id: int | None = Field(default=None, foreign_key="scope_facts.scope_id", index=True)
    name: str = Field(index=True)  # Bound identifier name
    target_kind: str  # DEF, IMPORT, UNKNOWN
    target_uid: str | None = None  # def_uid or import_uid or NULL
    certainty: str = Field(default=Certainty.CERTAIN.value)
    reason_code: str  # PARAM, LOCAL_ASSIGN, DEF_IN_SCOPE, IMPORT_ALIAS

    # Relationships (scope_id nullable, so relationship is optional)
    file: File | None = Relationship(back_populates="binds")
    scope: "ScopeFact" = Relationship(
        back_populates="binds", sa_relationship_kwargs={"foreign_keys": "[LocalBindFact.scope_id]"}
    )


class ImportFact(SQLModel, table=True):
    """Import statement fact (syntactic only). See SPEC.md §7.3.5."""

    __tablename__ = "import_facts"

    import_uid: str = Field(primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    scope_id: int | None = Field(default=None, foreign_key="scope_facts.scope_id", index=True)
    imported_name: str = Field(index=True)  # Name being imported
    alias: str | None = None  # Local alias (NULL if none)
    source_literal: str | None = None  # Import source string literal (if extractable)
    resolved_path: str | None = Field(
        default=None,
        index=True,
        description="Repo-relative file path this import resolves to. "
        "Populated at index time by resolving source_literal against "
        "declared_module values (declaration-based languages) or file "
        "paths (path-based languages like JS/TS, C/C++, Python).",
    )
    import_kind: str  # python_import, python_from, js_import, etc.
    certainty: str = Field(default=Certainty.CERTAIN.value)
    start_line: int | None = None  # 1-indexed start line of import statement
    start_col: int | None = None  # 0-indexed start column
    end_line: int | None = None  # 1-indexed end line of import statement
    end_col: int | None = None  # 0-indexed end column

    # Relationships
    file: File | None = Relationship(back_populates="imports")


class ExportSurface(SQLModel, table=True):
    """Materialized export surface per build unit. See SPEC.md §7.3.6."""

    __tablename__ = "export_surfaces"

    surface_id: int | None = Field(default=None, primary_key=True)
    unit_id: int = Field(foreign_key="contexts.id", index=True, unique=True)
    surface_hash: str | None = None  # Hash of all entries for invalidation
    epoch_id: int | None = None  # Epoch when surface was computed

    # Relationships
    entries: list["ExportEntry"] = Relationship(back_populates="surface")


class ExportEntry(SQLModel, table=True):
    """Individual exported name within an ExportSurface. See SPEC.md §7.3.7."""

    __tablename__ = "export_entries"

    entry_id: int | None = Field(default=None, primary_key=True)
    surface_id: int = Field(foreign_key="export_surfaces.surface_id", index=True)
    exported_name: str = Field(index=True)  # Public name
    def_uid: str | None = None  # Target definition (NULL if unresolved)
    certainty: str = Field(default=Certainty.CERTAIN.value)
    evidence_kind: str | None = None  # explicit_export, default_module, __all__literal, etc.

    # Relationships
    surface: ExportSurface | None = Relationship(back_populates="entries")


class ExportThunk(SQLModel, table=True):
    """Re-export declaration (strictly constrained forms only). See SPEC.md §7.3.8."""

    __tablename__ = "export_thunks"

    thunk_id: int | None = Field(default=None, primary_key=True)
    source_unit: int = Field(foreign_key="contexts.id", index=True)  # Unit doing the re-export
    target_unit: int = Field(foreign_key="contexts.id", index=True)  # Unit being re-exported from
    mode: str  # REEXPORT_ALL, EXPLICIT_NAMES, ALIAS_MAP
    explicit_names: str | None = None  # JSON array of names (if EXPLICIT_NAMES)
    alias_map: str | None = None  # JSON object of name→alias (if ALIAS_MAP)
    evidence_kind: str | None = None  # Syntax node type that produced this

    def get_explicit_names(self) -> list[str]:
        """Parse explicit_names JSON to list."""
        if self.explicit_names is None:
            return []
        result: list[str] = json.loads(self.explicit_names)
        return result

    def get_alias_map(self) -> dict[str, str]:
        """Parse alias_map JSON to dict."""
        if self.alias_map is None:
            return {}
        result: dict[str, str] = json.loads(self.alias_map)
        return result


class AnchorGroup(SQLModel, table=True):
    """Bounded ambiguity bucket for refs. See SPEC.md §7.3.9."""

    __tablename__ = "anchor_groups"

    group_id: int | None = Field(default=None, primary_key=True)
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    member_token: str = Field(index=True)  # The identifier text (e.g., 'foo')
    receiver_shape: str | None = None  # Receiver pattern (e.g., 'self.', 'obj.', 'None')
    total_count: int = Field(default=0)  # Total refs in this group
    exemplar_ids: str | None = None  # JSON array of ref_ids (hard-capped)

    def get_exemplar_ids(self) -> list[int]:
        """Parse exemplar_ids JSON to list."""
        if self.exemplar_ids is None:
            return []
        result: list[int] = json.loads(self.exemplar_ids)
        return result


class DynamicAccessSite(SQLModel, table=True):
    """Telemetry for dynamic access patterns (reporting only). See SPEC.md §7.3.10."""

    __tablename__ = "dynamic_access_sites"

    site_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    start_line: int
    start_col: int
    pattern_type: str  # bracket_access, getattr, reflect, eval, etc.
    extracted_literals: str | None = None  # JSON array of literal strings (if any)
    has_non_literal_key: bool = Field(default=False)  # True if key is computed/dynamic

    # Relationships
    file: File | None = Relationship(back_populates="dynamic_sites")

    def get_extracted_literals(self) -> list[str]:
        """Parse extracted_literals JSON to list."""
        if self.extracted_literals is None:
            return []
        result: list[str] = json.loads(self.extracted_literals)
        return result


# ============================================================================
# TIER 2 TYPE-AWARE FACT TABLES (Type-traced refactoring support)
# ============================================================================


class TypeAnnotationFact(SQLModel, table=True):
    """Type annotation extracted from source code.

    Captures explicit type annotations from any language that supports them.
    Used by Pass 3 (type-traced resolution) to resolve member accesses.
    """

    __tablename__ = "type_annotation_facts"

    annotation_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    scope_id: int | None = Field(default=None, foreign_key="scope_facts.scope_id", index=True)

    # What's annotated
    target_kind: str  # AnnotationTargetKind value
    target_name: str = Field(index=True)  # Variable/param name

    # The annotation (as written and normalized)
    raw_annotation: str  # As written: "List[int]", "int[]", "[]int"
    canonical_type: str = Field(index=True)  # Normalized: "List<int>"
    base_type: str = Field(index=True)  # Just the base: "List", "int"

    # Type modifiers (language-agnostic flags)
    is_optional: bool = Field(default=False)  # None/null/nil allowed
    is_array: bool = Field(default=False)  # Collection type
    is_generic: bool = Field(default=False)  # Has type parameters
    is_reference: bool = Field(default=False)  # Pointer/ref type
    is_mutable: bool = Field(default=True)  # const/final/let vs var

    # Generic parameters (if applicable)
    type_args_json: str | None = None  # ["int"] for List<int>

    # Source info
    start_line: int
    start_col: int

    def get_type_args(self) -> list[str]:
        """Parse type_args_json to list."""
        if self.type_args_json is None:
            return []
        result: list[str] = json.loads(self.type_args_json)
        return result


class TypeMemberFact(SQLModel, table=True):
    """Member of a type definition (class field, struct field, interface method, etc.).

    Used to resolve attribute chains like `obj.field.method()`.
    """

    __tablename__ = "type_member_facts"

    member_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)

    # Parent type
    parent_def_uid: str = Field(index=True)  # DefFact.def_uid of class/struct/interface
    parent_type_name: str = Field(index=True)  # "AppContext", "MutationOps"
    parent_kind: str  # TypeParentKind value

    # Member info
    member_kind: str  # MemberKind value
    member_name: str = Field(index=True)
    member_def_uid: str | None = None  # DefFact.def_uid if method/property

    # Type info (for fields/properties)
    type_annotation: str | None = None  # "MutationOps", "str | None"
    canonical_type: str | None = Field(default=None, index=True)  # Normalized
    base_type: str | None = Field(default=None, index=True)  # Base type only

    # Visibility
    visibility: str | None = None  # "public", "private", "protected", "internal"
    is_static: bool = Field(default=False)
    is_abstract: bool = Field(default=False)

    start_line: int
    start_col: int


class MemberAccessFact(SQLModel, table=True):
    """Member access chain extracted from source code.

    Captures `obj.field.method()` patterns for type-traced resolution.
    """

    __tablename__ = "member_access_facts"

    access_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    scope_id: int | None = Field(default=None, foreign_key="scope_facts.scope_id", index=True)

    # Access pattern
    access_style: str  # AccessStyle value: dot, arrow, scope

    # The chain
    full_expression: str  # "ctx.mutation_ops.write_source"
    receiver_name: str = Field(index=True)  # Leftmost identifier: "ctx"
    member_chain: str  # Rest of chain: "mutation_ops.write_source"
    final_member: str = Field(index=True)  # Rightmost: "write_source"
    chain_depth: int  # Number of accesses: 2

    # Call info
    is_invocation: bool = Field(default=False)  # Ends with ()
    arg_count: int | None = None  # Number of arguments if call

    # Receiver type (from annotation in scope, if found)
    receiver_declared_type: str | None = Field(default=None, index=True)

    # Resolution results (filled by Pass 3)
    resolved_type_path: str | None = None  # "AppContext.MutationOps"
    final_target_def_uid: str | None = Field(default=None, index=True)
    resolution_method: str | None = None  # ResolutionMethod value
    resolution_confidence: float | None = None  # 0.0-1.0

    start_line: int
    start_col: int
    end_line: int
    end_col: int


class InterfaceImplFact(SQLModel, table=True):
    """Interface/trait implementation relationship.

    Used for Go, Rust, TypeScript interface matching.
    """

    __tablename__ = "interface_impl_facts"

    impl_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)

    # The implementing type
    implementor_def_uid: str = Field(index=True)
    implementor_name: str = Field(index=True)

    # The interface/trait
    interface_name: str = Field(index=True)
    interface_def_uid: str | None = None  # If found in same codebase

    # How declared
    impl_style: str  # ImplStyle value: explicit, structural, inferred

    start_line: int
    start_col: int


class ReceiverShapeFact(SQLModel, table=True):
    """Observed shape for duck-typing inference.

    Aggregates all member accesses on a receiver to infer its type.
    Used for dynamic languages without type annotations.
    """

    __tablename__ = "receiver_shape_facts"

    shape_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), index=True)
    )
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    scope_id: int | None = Field(default=None, foreign_key="scope_facts.scope_id", index=True)

    receiver_name: str = Field(index=True)  # "ctx", "self", "obj"
    declared_type: str | None = Field(default=None, index=True)  # If annotated

    # Observed shape (all members accessed on this receiver in this scope)
    shape_hash: str = Field(index=True)  # Hash for fast comparison
    observed_members_json: str  # {"fields": ["x"], "methods": ["run"]}

    # Inference results (filled by Pass 5)
    matched_types_json: str | None = None  # [{"type": "Foo", "confidence": 0.9}]
    best_match_type: str | None = Field(default=None, index=True)
    match_confidence: float | None = None  # 0.0-1.0

    def get_observed_members(self) -> dict[str, list[str]]:
        """Parse observed_members_json to dict."""
        result: dict[str, list[str]] = json.loads(self.observed_members_json)
        return result

    def get_matched_types(self) -> list[dict[str, float | str]]:
        """Parse matched_types_json to list."""
        if self.matched_types_json is None:
            return []
        result: list[dict[str, float | str]] = json.loads(self.matched_types_json)
        return result


class RepoState(SQLModel, table=True):
    """Repository state tracking (singleton row, id=1)."""

    __tablename__ = "repo_state"

    id: int = Field(default=1, primary_key=True)
    last_seen_head: str | None = None
    last_seen_index_mtime: float | None = None
    checked_at: float | None = None
    current_epoch_id: int | None = None  # Current epoch ID
    cplignore_hash: str | None = None  # Hash of .codeplane/.cplignore content


class Epoch(SQLModel, table=True):
    """Epoch record for incremental snapshot barriers. See SPEC.md §7.6."""

    __tablename__ = "epochs"

    epoch_id: int | None = Field(default=None, primary_key=True)
    published_at: float | None = None
    files_indexed: int = Field(default=0)
    commit_hash: str | None = None  # Git commit at epoch time (if available)


class DefSnapshotRecord(SQLModel, table=True):
    """Point-in-time snapshot of a DefFact, captured when an epoch is published.

    Used by semantic diff to compare symbol state across epochs without
    needing the full Index data to still exist.  No FK to File — the
    snapshot must survive file deletions.
    """

    __tablename__ = "def_snapshot_record"

    id: int | None = Field(default=None, primary_key=True)
    epoch_id: int = Field(index=True)
    file_path: str = Field(index=True)
    kind: str
    name: str
    lexical_path: str
    signature_hash: str | None = None
    display_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None


# ============================================================================
# NON-TABLE MODELS (Pydantic only, for data transfer)
# ============================================================================


class FileState(SQLModel):
    """Computed file state (not persisted directly)."""

    freshness: Freshness
    certainty: Certainty


class CandidateContext(SQLModel):
    """Candidate context during discovery (not persisted directly)."""

    language_family: LanguageFamily
    root_path: str
    tier: int | None = None
    markers: list[str] = Field(default_factory=list)
    include_spec: list[str] | None = None
    exclude_spec: list[str] | None = None
    probe_status: ProbeStatus = ProbeStatus.PENDING
    is_root_fallback: bool = False  # True for tier-3 root fallback context


class LexicalHit(SQLModel):
    """Result from Tier 0 lexical search."""

    file_id: int
    unit_id: int
    path: str
    score: float
    snippet: str | None = None
