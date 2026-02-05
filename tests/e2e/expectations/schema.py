"""Pydantic models for E2E test expectations.

Defines the schema for YAML expectation files and timeout configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class TimeoutConfig(BaseModel):
    """Per-phase timeout configuration for E2E tests.

    Each phase of the test lifecycle has an independent timeout to allow
    fine-grained control over slow operations like cloning large repos.
    """

    clone_sec: float = Field(default=120.0, description="Timeout for git clone")
    init_sec: float = Field(default=120.0, description="Timeout for cpl init")
    server_ready_sec: float = Field(default=60.0, description="Timeout waiting for server ready")
    health_check_sec: float = Field(default=10.0, description="Timeout for health check requests")
    tool_call_sec: float = Field(default=60.0, description="Default timeout for MCP tool calls")
    shutdown_sec: float = Field(default=30.0, description="Timeout for graceful shutdown")


# Preset timeout profiles for different repo sizes
TIMEOUT_PROFILES: dict[str, TimeoutConfig] = {
    "small": TimeoutConfig(
        clone_sec=60.0,
        init_sec=60.0,
        server_ready_sec=30.0,
        health_check_sec=5.0,
        tool_call_sec=30.0,
        shutdown_sec=15.0,
    ),
    "medium": TimeoutConfig(
        clone_sec=120.0,
        init_sec=120.0,
        server_ready_sec=60.0,
        health_check_sec=10.0,
        tool_call_sec=60.0,
        shutdown_sec=30.0,
    ),
    "large": TimeoutConfig(
        clone_sec=300.0,
        init_sec=180.0,
        server_ready_sec=90.0,
        health_check_sec=15.0,
        tool_call_sec=120.0,
        shutdown_sec=45.0,
    ),
}


class ContextExpectation(BaseModel):
    """Expected language context for a repository."""

    root: str = Field(default=".", description="Root path for this context")
    language: str = Field(..., description="Primary language identifier")


class FilesExpectation(BaseModel):
    """Expectations for file listing."""

    indexed_min: int | None = Field(default=None, description="Minimum indexed files")
    indexed_max: int | None = Field(default=None, description="Maximum indexed files")
    must_include: list[str] = Field(default_factory=list, description="Files that must be indexed")
    must_exclude: list[str] = Field(
        default_factory=list, description="Patterns that must not appear"
    )


class AnchorExpectation(BaseModel):
    """Expected symbol anchor."""

    symbol: str = Field(..., description="Symbol name to find")
    kind: str = Field(..., description="Symbol kind (class, function, etc.)")
    file_contains: str | None = Field(
        default=None, description="Path substring where symbol should be"
    )
    line_min: int | None = Field(default=None, description="Minimum line number")
    line_max: int | None = Field(default=None, description="Maximum line number")


class RefExpectation(BaseModel):
    """Expected reference relationship."""

    to_symbol: str = Field(..., description="Target symbol name")
    min_refs: int = Field(default=1, description="Minimum reference count")


class ImportExpectation(BaseModel):
    """Expected import relationships."""

    file_contains: str = Field(..., description="Path substring of file to check")
    imports_min: int = Field(default=0, description="Minimum import count")
    must_import: list[str] = Field(
        default_factory=list, description="Modules that must be imported"
    )


class ScopeExpectation(BaseModel):
    """Expected scope structure."""

    file_contains: str = Field(..., description="Path substring of file to check")
    has_class_scope: bool = Field(default=False, description="Should have class-level scope")
    class_has_methods_min: int = Field(default=0, description="Minimum methods in class")


class TestTargetsExpectation(BaseModel):
    """Expectations for test discovery."""

    runner: str | None = Field(default=None, description="Expected test runner")
    targets_min: int | None = Field(default=None, description="Minimum test targets")


class SearchExpectation(BaseModel):
    """Expected search result."""

    query: str = Field(..., description="Search query")
    must_find_file: str | None = Field(default=None, description="File that must appear in results")


class IncrementalExpectation(BaseModel):
    """Expectations for incremental indexing."""

    touch_file: str | None = Field(default=None, description="File to touch for incremental test")
    reindexed_max: int | None = Field(default=None, description="Maximum files reindexed")


class DaemonExpectation(BaseModel):
    """Expectations for daemon lifecycle."""

    starts: bool = Field(default=True, description="Daemon should start successfully")
    status_shows_running: bool = Field(default=True, description="Status should show running")
    stops_cleanly: bool = Field(default=True, description="Should stop without errors")


class RepoExpectation(BaseModel):
    """Complete expectation specification for a repository."""

    repo: str = Field(..., description="GitHub repo in owner/name format")
    commit: str | None = Field(default=None, description="Specific commit or tag to checkout")
    clone_depth: int = Field(default=1, description="Shallow clone depth")
    timeout_profile: Literal["small", "medium", "large"] = Field(
        default="medium",
        description="Timeout profile to use",
    )

    contexts: list[ContextExpectation] = Field(default_factory=list)
    files: FilesExpectation | None = Field(default=None)
    anchors: list[AnchorExpectation] = Field(default_factory=list)
    refs: list[RefExpectation] = Field(default_factory=list)
    imports: list[ImportExpectation] = Field(default_factory=list)
    scopes: list[ScopeExpectation] = Field(default_factory=list)
    test_targets: TestTargetsExpectation | None = Field(default=None)
    search: list[SearchExpectation] = Field(default_factory=list)
    incremental: IncrementalExpectation | None = Field(default=None)
    daemon: DaemonExpectation | None = Field(default=None)

    @property
    def timeout_config(self) -> TimeoutConfig:
        """Get the timeout configuration for this repo."""
        return TIMEOUT_PROFILES[self.timeout_profile]

    @property
    def test_id(self) -> str:
        """Generate a pytest-friendly test ID."""
        return self.repo.replace("/", "_").replace("-", "_")


def load_all_expectations() -> list[RepoExpectation]:
    """Load all expectation YAML files from the expectations directory."""
    expectations_dir = Path(__file__).parent
    expectations: list[RepoExpectation] = []

    for yaml_path in sorted(expectations_dir.glob("*.yaml")):
        with yaml_path.open() as f:
            data = yaml.safe_load(f)
            if data:
                expectations.append(RepoExpectation.model_validate(data))

    return expectations
