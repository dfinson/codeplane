"""Lint models - diagnostics and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class ToolCategory(Enum):
    """Category of lint tool."""

    TYPE_CHECK = "type_check"
    LINT = "lint"
    FORMAT = "format"
    SECURITY = "security"


class Severity(Enum):
    """Diagnostic severity level."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    HINT = "hint"


@dataclass
class Diagnostic:
    """A single diagnostic from a lint/type-check/format tool."""

    path: str
    line: int
    message: str
    source: str  # tool that produced this
    severity: Severity = Severity.WARNING
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    code: str | None = None  # "E501", "arg-type", "no-unused-vars"
    fix_applied: bool = False  # True if auto-fixed
    fix_diff: str | None = None  # For dry_run mode


@dataclass
class ToolResult:
    """Result from running a single tool."""

    tool_id: str
    status: Literal["clean", "dirty", "error", "skipped"]
    diagnostics: list[Diagnostic] = field(default_factory=list)
    files_checked: int = 0
    files_modified: int = 0  # When fix applied
    duration_seconds: float = 0.0
    error_detail: str | None = None  # If status=="error"
    command: list[str] | None = None  # Command that was run


@dataclass
class LintResult:
    """Aggregated result from lint operation."""

    action: Literal["check", "fix"]
    dry_run: bool
    tools_run: list[ToolResult] = field(default_factory=list)
    duration_seconds: float = 0.0
    # Agentic fallback - when no tools detected, suggest what agent should run
    agentic_hint: str | None = None

    @property
    def total_diagnostics(self) -> int:
        return sum(len(t.diagnostics) for t in self.tools_run)

    @property
    def total_files_modified(self) -> int:
        return sum(t.files_modified for t in self.tools_run)

    @property
    def has_errors(self) -> bool:
        return any(d.severity == Severity.ERROR for t in self.tools_run for d in t.diagnostics)

    @property
    def status(self) -> Literal["clean", "dirty", "error"]:
        if any(t.status == "error" for t in self.tools_run):
            return "error"
        if any(t.status == "dirty" for t in self.tools_run):
            return "dirty"
        return "clean"


@dataclass
class ParseResult:
    """Result from parsing tool output."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    parse_error: str | None = None

    @property
    def success(self) -> bool:
        return self.parse_error is None

    @classmethod
    def ok(cls, diagnostics: list[Diagnostic]) -> ParseResult:
        return cls(diagnostics=diagnostics)

    @classmethod
    def error(cls, message: str) -> ParseResult:
        return cls(parse_error=message)
