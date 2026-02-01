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
from enum import Enum
from typing import TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    pass


# ============================================================================
# ENUMS
# ============================================================================


class LanguageFamily(str, Enum):
    """Canonical language family identifiers (20 total)."""

    # Code families
    JAVASCRIPT = "javascript"
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    JVM = "jvm"
    DOTNET = "dotnet"
    RUBY = "ruby"
    PHP = "php"
    SWIFT = "swift"
    ELIXIR = "elixir"
    HASKELL = "haskell"
    CPP = "cpp"
    # Data families
    TERRAFORM = "terraform"
    SQL = "sql"
    DOCKER = "docker"
    MARKDOWN = "markdown"
    JSON_YAML = "json_yaml"
    PROTOBUF = "protobuf"
    GRAPHQL = "graphql"
    CONFIG = "config"

    @classmethod
    def code_families(cls) -> "frozenset[LanguageFamily]":
        """Return code families."""
        return frozenset(
            {
                cls.JAVASCRIPT,
                cls.PYTHON,
                cls.GO,
                cls.RUST,
                cls.JVM,
                cls.DOTNET,
                cls.RUBY,
                cls.PHP,
                cls.SWIFT,
                cls.ELIXIR,
                cls.HASKELL,
                cls.CPP,
            }
        )

    @classmethod
    def data_families(cls) -> "frozenset[LanguageFamily]":
        """Return data families."""
        return frozenset(
            {
                cls.TERRAFORM,
                cls.SQL,
                cls.DOCKER,
                cls.MARKDOWN,
                cls.JSON_YAML,
                cls.PROTOBUF,
                cls.GRAPHQL,
                cls.CONFIG,
            }
        )

    @property
    def is_code(self) -> bool:
        """True if this is a code family."""
        return self in self.code_families()

    @property
    def is_data(self) -> bool:
        """True if this is a data family."""
        return self in self.data_families()


class Freshness(str, Enum):
    """Index currency state."""

    CLEAN = "clean"
    DIRTY = "dirty"
    STALE = "stale"
    PENDING_CHECK = "pending_check"
    UNINDEXED = "unindexed"


class Certainty(str, Enum):
    """Confidence level for facts."""

    CERTAIN = "certain"
    UNCERTAIN = "uncertain"


class RefTier(str, Enum):
    """Reference tier classification (assigned at index time, never upgraded at query time)."""

    PROVEN = "proven"  # Same-file lexical bind with LocalBindFact certainty=CERTAIN
    STRONG = "strong"  # Cross-file with explicit ImportFact + ExportSurface trace
    ANCHORED = "anchored"  # Ambiguous but grouped in AnchorGroup
    UNKNOWN = "unknown"  # Cannot classify


class Role(str, Enum):
    """Reference role in source code."""

    DEFINITION = "definition"
    REFERENCE = "reference"
    IMPORT = "import"
    EXPORT = "export"


class ScopeKind(str, Enum):
    """Lexical scope kind."""

    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    BLOCK = "block"
    COMPREHENSION = "comprehension"
    LAMBDA = "lambda"


class BindTargetKind(str, Enum):
    """Target kind for LocalBindFact."""

    DEF = "def"  # Bound to a DefFact
    IMPORT = "import"  # Bound to an ImportFact
    UNKNOWN = "unknown"  # Cannot determine


class BindReasonCode(str, Enum):
    """Reason for binding classification."""

    PARAM = "param"  # Function parameter
    LOCAL_ASSIGN = "local_assign"  # Assignment target
    DEF_IN_SCOPE = "def_in_scope"  # Definition in enclosing scope
    IMPORT_ALIAS = "import_alias"  # Import alias
    FOR_TARGET = "for_target"  # For loop target
    WITH_AS = "with_as"  # With statement alias
    EXCEPT_AS = "except_as"  # Exception handler alias


class ImportKind(str, Enum):
    """Import statement kind."""

    PYTHON_IMPORT = "python_import"  # import foo
    PYTHON_FROM = "python_from"  # from foo import bar
    JS_IMPORT = "js_import"  # import { foo } from 'bar'
    JS_REQUIRE = "js_require"  # const foo = require('bar')
    TS_IMPORT_TYPE = "ts_import_type"  # import type { Foo } from 'bar'
    GO_IMPORT = "go_import"  # import "foo"
    RUST_USE = "rust_use"  # use foo::bar


class ExportThunkMode(str, Enum):
    """Re-export mode for ExportThunk."""

    REEXPORT_ALL = "reexport_all"  # export * from 'module'
    EXPLICIT_NAMES = "explicit_names"  # export { a, b } from 'module'
    ALIAS_MAP = "alias_map"  # export { a as x, b as y } from 'module'


class DynamicAccessPattern(str, Enum):
    """Dynamic access pattern types (telemetry only)."""

    BRACKET_ACCESS = "bracket_access"  # obj[key]
    GETATTR = "getattr"  # getattr(obj, name)
    REFLECT = "reflect"  # Reflect.get(obj, name)
    EVAL = "eval"  # eval(), exec()
    IMPORT_MODULE = "import_module"  # importlib.import_module(var)


class ProbeStatus(str, Enum):
    """Context probe status."""

    PENDING = "pending"
    VALID = "valid"
    FAILED = "failed"
    EMPTY = "empty"
    DETACHED = "detached"


class MarkerTier(str, Enum):
    """Marker tier for context discovery hierarchy."""

    WORKSPACE = "workspace"
    PACKAGE = "package"


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
    indexed_at: float | None = None
    last_indexed_epoch: int | None = Field(default=None, index=True)

    # Relationships
    defs: list["DefFact"] = Relationship(back_populates="file")
    refs: list["RefFact"] = Relationship(back_populates="file")
    scopes: list["ScopeFact"] = Relationship(back_populates="file")
    binds: list["LocalBindFact"] = Relationship(back_populates="file")
    imports: list["ImportFact"] = Relationship(back_populates="file")
    dynamic_sites: list["DynamicAccessSite"] = Relationship(back_populates="file")


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
    enabled: bool = Field(default=True)
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
    file_id: int = Field(foreign_key="files.id", index=True)
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    kind: str = Field(index=True)  # function, class, method, variable, etc.
    name: str = Field(index=True)  # Simple name
    qualified_name: str | None = None  # Full path (e.g., module.Class.method)
    lexical_path: str = Field(index=True)  # Syntactic nesting path for identity
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    signature_hash: str | None = None  # Hash of syntactic signature
    display_name: str | None = None  # Human-readable form

    # Relationships
    file: File | None = Relationship(back_populates="defs")
    # Note: refs relationship removed - use FactQueries.list_refs_by_def_uid() instead


class RefFact(SQLModel, table=True):
    """Reference fact (identifier occurrence). See SPEC.md §7.3.2."""

    __tablename__ = "ref_facts"

    ref_id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", index=True)
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
    file_id: int = Field(foreign_key="files.id", index=True)
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
    file_id: int = Field(foreign_key="files.id", index=True)
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
    file_id: int = Field(foreign_key="files.id", index=True)
    unit_id: int = Field(foreign_key="contexts.id", index=True)
    scope_id: int | None = Field(default=None, foreign_key="scope_facts.scope_id", index=True)
    imported_name: str = Field(index=True)  # Name being imported
    alias: str | None = None  # Local alias (NULL if none)
    source_literal: str | None = None  # Import source string literal (if extractable)
    import_kind: str  # python_import, python_from, js_import, etc.
    certainty: str = Field(default=Certainty.CERTAIN.value)

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
    file_id: int = Field(foreign_key="files.id", index=True)
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


class LexicalHit(SQLModel):
    """Result from Tier 0 lexical search."""

    file_id: int
    unit_id: int
    path: str
    score: float
    snippet: str | None = None
