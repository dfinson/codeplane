"""SQLModel definitions for the indexing engine.

Single source of truth for all table schemas. High-volume tables (File, Symbol,
Occurrence, Edge, Export) should use BulkWriter for inserts. Low-volume tables
(Context, RefreshJob, RepoState) can use ORM sessions.
"""

from __future__ import annotations

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
    """
    Canonical language family identifiers (19 total).

    Code families (11): require meaningful named nodes in Tree-sitter parse.
    Data families (8): require valid tree with content.
    """

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
    def code_families(cls) -> frozenset[LanguageFamily]:
        """Return the set of code families."""
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
            }
        )

    @classmethod
    def data_families(cls) -> frozenset[LanguageFamily]:
        """Return the set of data families."""
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
    """Semantic confidence level."""

    CERTAIN = "certain"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class Layer(str, Enum):
    """Index layer (syntactic or semantic)."""

    SYNTACTIC = "syntactic"
    SEMANTIC = "semantic"


class Role(str, Enum):
    """Symbol occurrence role."""

    DEFINITION = "definition"
    REFERENCE = "reference"
    IMPORT = "import"


class JobStatus(str, Enum):
    """Refresh job status."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class ProbeStatus(str, Enum):
    """Context probe status."""

    PENDING = "pending"
    VALID = "valid"
    FAILED = "failed"
    EMPTY = "empty"
    DETACHED = "detached"


class MarkerTier(str, Enum):
    """Marker tier (workspace fence vs package root)."""

    TIER1 = "tier1"
    TIER2 = "tier2"


# ============================================================================
# TABLE MODELS
# ============================================================================


class File(SQLModel, table=True):
    """
    Tracked file in the repository.

    HIGH-VOLUME TABLE: Use BulkWriter.insert_many_returning_ids() with
    key_columns=["path"] to get path -> id mapping before inserting Symbols.
    """

    __tablename__ = "files"

    id: int | None = Field(default=None, primary_key=True)
    path: str = Field(unique=True, index=True)
    language: str | None = None
    content_hash: str
    syntactic_interface_hash: str | None = None
    indexed_at: float | None = None

    # Relationships (loaded lazily)
    symbols: list[Symbol] = Relationship(back_populates="file")
    occurrences: list[Occurrence] = Relationship(back_populates="file")


class Context(SQLModel, table=True):
    """
    Semantic context for indexing.

    LOW-VOLUME TABLE: Use ORM session.
    """

    __tablename__ = "contexts"

    id: int | None = Field(default=None, primary_key=True)
    name: str | None = None
    language_family: str = Field(index=True)
    root_path: str = Field(index=True)  # "" = repo root
    tier: int | None = None  # 1 = workspace, 2 = package, None = ambient
    probe_status: str = Field(default=ProbeStatus.PENDING.value, index=True)
    include_spec: str | None = None  # JSON array of globs
    exclude_spec: str | None = None  # JSON array of globs
    config_hash: str | None = None
    tool_version: str | None = None
    enabled: bool = Field(default=True)
    refreshed_at: float | None = None

    # Relationships
    markers: list[ContextMarker] = Relationship(back_populates="context")
    refresh_jobs: list[RefreshJob] = Relationship(back_populates="context")

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
    """
    Marker file that triggered context discovery.

    LOW-VOLUME TABLE: Use ORM session.
    """

    __tablename__ = "context_markers"

    id: int | None = Field(default=None, primary_key=True)
    context_id: int = Field(foreign_key="contexts.id", index=True)
    marker_path: str
    marker_tier: str  # 'tier1' or 'tier2'
    detected_at: float | None = None

    # Relationships
    context: Context | None = Relationship(back_populates="markers")


class Symbol(SQLModel, table=True):
    """
    Symbol definition (function, class, method, variable).

    HIGH-VOLUME TABLE: Use BulkWriter.insert_many_returning_ids() with
    key_columns=["file_id", "name", "line"] to get IDs before inserting
    Occurrences or Exports.
    """

    __tablename__ = "symbols"

    id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", index=True)
    context_id: int | None = Field(default=None, foreign_key="contexts.id", index=True)
    name: str = Field(index=True)
    qualified_name: str | None = None
    kind: str  # function, class, method, variable, etc.
    line: int
    column: int | None = None
    signature: str | None = None
    layer: str  # 'syntactic' or 'semantic'

    # Relationships
    file: File | None = Relationship(back_populates="symbols")
    occurrences: list[Occurrence] = Relationship(back_populates="symbol")
    exports: list[Export] = Relationship(back_populates="symbol")


class Occurrence(SQLModel, table=True):
    """
    Symbol occurrence (where a symbol appears).

    HIGH-VOLUME TABLE: Use BulkWriter.insert_many() AFTER obtaining
    symbol_id and file_id from parent inserts.
    """

    __tablename__ = "occurrences"

    id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", index=True)
    symbol_id: int = Field(foreign_key="symbols.id", index=True)
    context_id: int = Field(foreign_key="contexts.id", index=True)
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    role: str  # definition, reference, import
    layer: str  # syntactic, semantic
    anchor_before: str | None = None
    anchor_after: str | None = None

    # Relationships
    file: File | None = Relationship(back_populates="occurrences")
    symbol: Symbol | None = Relationship(back_populates="occurrences")


class Export(SQLModel, table=True):
    """
    Exported symbol for public name reasoning.

    HIGH-VOLUME TABLE: Use BulkWriter.insert_many() AFTER obtaining
    symbol_id and file_id from parent inserts.
    """

    __tablename__ = "exports"

    id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", index=True)
    context_id: int = Field(foreign_key="contexts.id", index=True)
    symbol_id: int = Field(foreign_key="symbols.id", index=True)
    visibility: str  # public, internal, private
    layer: str

    # Relationships
    symbol: Symbol | None = Relationship(back_populates="exports")


class Edge(SQLModel, table=True):
    """
    File dependency edge (import relationship).

    HIGH-VOLUME TABLE: Use BulkWriter.insert_many() AFTER obtaining
    file_ids for both source and target.
    """

    __tablename__ = "edges"

    id: int | None = Field(default=None, primary_key=True)
    source_file: int = Field(foreign_key="files.id", index=True)
    target_file: int = Field(foreign_key="files.id", index=True)
    dependency_type: str  # import, include, require, etc.
    context_id: int | None = Field(default=None, foreign_key="contexts.id", index=True)
    layer: str


class FileSemanticFacts(SQLModel, table=True):
    """
    Semantic facts per (file, context) pair.

    MEDIUM-VOLUME TABLE: Use BulkWriter.upsert_many() for batch updates.
    """

    __tablename__ = "file_semantic_facts"

    file_id: int = Field(foreign_key="files.id", primary_key=True)
    context_id: int = Field(foreign_key="contexts.id", primary_key=True)
    semantic_interface_hash: str | None = None
    content_hash_at_index: str | None = None
    ambiguity_flags: str | None = None  # JSON
    refreshed_at: float | None = None

    def get_ambiguity_flags(self) -> dict[str, str]:
        """Parse ambiguity_flags JSON to dict."""
        if self.ambiguity_flags is None:
            return {}
        result: dict[str, str] = json.loads(self.ambiguity_flags)
        return result


class RefreshJob(SQLModel, table=True):
    """
    SCIP indexer job queue entry.

    MEDIUM-VOLUME TABLE: Use ORM for atomic status transitions
    (requires WHERE clause for race safety).
    """

    __tablename__ = "refresh_jobs"

    id: int | None = Field(default=None, primary_key=True)
    context_id: int = Field(foreign_key="contexts.id", index=True)
    status: str = Field(index=True)  # queued, running, completed, superseded, failed
    scope: str | None = None  # JSON: RefreshScope
    desired_scope: str | None = None  # JSON: for running-job widening
    trigger_reason: str | None = None
    head_at_enqueue: str
    created_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    superseded_reason: str | None = None
    error: str | None = None

    # Relationships
    context: Context | None = Relationship(back_populates="refresh_jobs")


class RepoState(SQLModel, table=True):
    """
    Repository state tracking (singleton row, id=1).

    LOW-VOLUME TABLE: Use db.immediate_transaction() for updates to
    prevent race conditions between concurrent reconciliations.
    """

    __tablename__ = "repo_state"

    id: int = Field(default=1, primary_key=True)
    last_seen_head: str | None = None
    last_seen_index_mtime: float | None = None
    checked_at: float | None = None


class DecisionCache(SQLModel, table=True):
    """
    Cached agent decisions for ambiguity replay.

    LOW-VOLUME TABLE: Use ORM session.
    """

    __tablename__ = "decision_cache"

    id: int | None = Field(default=None, primary_key=True)
    ambiguity_signature: str = Field(index=True)
    repo_head: str
    file_hashes: str  # JSON: {path: hash}
    decision: str  # JSON: selected candidates
    proof_payload: str  # JSON
    created_at: float | None = None


# ============================================================================
# NON-TABLE MODELS (Pydantic only, for data transfer)
# ============================================================================


class FileState(SQLModel):
    """Computed file state (not persisted directly)."""

    freshness: Freshness
    certainty: Certainty


class RefreshScope(SQLModel):
    """Scope for refresh job."""

    files: list[str] | None = None
    packages: list[str] | None = None
    changed_since: float | None = None

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_json(cls, data: str | None) -> RefreshScope | None:
        """Deserialize from JSON string."""
        if data is None:
            return None
        return cls.model_validate_json(data)


class CandidateContext(SQLModel):
    """Candidate context during discovery (not persisted directly)."""

    language_family: LanguageFamily
    root_path: str
    tier: int | None = None
    markers: list[str] = Field(default_factory=list)
    include_spec: list[str] | None = None
    exclude_spec: list[str] | None = None
    probe_status: ProbeStatus = ProbeStatus.PENDING
