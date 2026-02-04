"""Tests for lint/models.py module.

Covers:
- ToolCategory enum
- Severity enum
- Diagnostic dataclass
- ToolResult dataclass
- LintResult dataclass
"""

from __future__ import annotations

from codeplane.lint.models import (
    Diagnostic,
    LintResult,
    Severity,
    ToolCategory,
    ToolResult,
)


class TestToolCategory:
    """Tests for ToolCategory enum."""

    def test_all_categories(self) -> None:
        """All categories exist."""
        expected = {"type_check", "lint", "format", "security"}
        actual = {cat.value for cat in ToolCategory}
        assert actual == expected

    def test_values(self) -> None:
        """Category values."""
        assert ToolCategory.TYPE_CHECK.value == "type_check"
        assert ToolCategory.LINT.value == "lint"
        assert ToolCategory.FORMAT.value == "format"
        assert ToolCategory.SECURITY.value == "security"


class TestSeverity:
    """Tests for Severity enum."""

    def test_all_severities(self) -> None:
        """All severities exist."""
        expected = {"error", "warning", "info", "hint"}
        actual = {sev.value for sev in Severity}
        assert actual == expected

    def test_ordering_for_display(self) -> None:
        """Severities have expected values."""
        assert Severity.ERROR.value == "error"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"
        assert Severity.HINT.value == "hint"


class TestDiagnostic:
    """Tests for Diagnostic dataclass."""

    def test_create_minimal(self) -> None:
        """Create with minimal fields."""
        diag = Diagnostic(
            path="file.py",
            line=10,
            message="Error message",
            source="ruff",
        )
        assert diag.path == "file.py"
        assert diag.line == 10
        assert diag.message == "Error message"
        assert diag.source == "ruff"

    def test_default_severity(self) -> None:
        """Default severity is WARNING."""
        diag = Diagnostic(path="x", line=1, message="m", source="s")
        assert diag.severity == Severity.WARNING

    def test_optional_fields(self) -> None:
        """Optional fields default to None."""
        diag = Diagnostic(path="x", line=1, message="m", source="s")
        assert diag.column is None
        assert diag.end_line is None
        assert diag.end_column is None
        assert diag.code is None
        assert diag.fix_diff is None

    def test_fix_applied_default(self) -> None:
        """fix_applied defaults to False."""
        diag = Diagnostic(path="x", line=1, message="m", source="s")
        assert diag.fix_applied is False

    def test_full_fields(self) -> None:
        """Create with all fields."""
        diag = Diagnostic(
            path="file.py",
            line=10,
            column=5,
            end_line=12,
            end_column=20,
            message="Error message",
            source="mypy",
            severity=Severity.ERROR,
            code="arg-type",
            fix_applied=True,
            fix_diff="diff content",
        )
        assert diag.column == 5
        assert diag.end_line == 12
        assert diag.code == "arg-type"
        assert diag.fix_applied is True


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_create_clean(self) -> None:
        """Create clean result."""
        result = ToolResult(tool_id="ruff", status="clean")
        assert result.tool_id == "ruff"
        assert result.status == "clean"
        assert result.diagnostics == []

    def test_create_dirty(self) -> None:
        """Create dirty result with diagnostics."""
        diag = Diagnostic(path="x", line=1, message="m", source="ruff")
        result = ToolResult(
            tool_id="ruff",
            status="dirty",
            diagnostics=[diag],
            files_checked=10,
        )
        assert result.status == "dirty"
        assert len(result.diagnostics) == 1
        assert result.files_checked == 10

    def test_error_status(self) -> None:
        """Create error result."""
        result = ToolResult(
            tool_id="mypy",
            status="error",
            error_detail="Command failed with exit code 2",
        )
        assert result.status == "error"
        assert result.error_detail is not None

    def test_defaults(self) -> None:
        """Default values."""
        result = ToolResult(tool_id="x", status="clean")
        assert result.files_checked == 0
        assert result.files_modified == 0
        assert result.duration_seconds == 0.0
        assert result.command is None


class TestLintResult:
    """Tests for LintResult dataclass."""

    def test_create_empty(self) -> None:
        """Create empty result."""
        result = LintResult(action="check", dry_run=False)
        assert result.action == "check"
        assert result.dry_run is False
        assert result.tools_run == []

    def test_total_diagnostics(self) -> None:
        """Counts total diagnostics across tools."""
        tool1 = ToolResult(
            tool_id="ruff",
            status="dirty",
            diagnostics=[Diagnostic(path="x", line=1, message="m", source="ruff")],
        )
        tool2 = ToolResult(
            tool_id="mypy",
            status="dirty",
            diagnostics=[
                Diagnostic(path="x", line=1, message="m", source="mypy"),
                Diagnostic(path="x", line=2, message="m", source="mypy"),
            ],
        )
        result = LintResult(action="check", dry_run=False, tools_run=[tool1, tool2])
        assert result.total_diagnostics == 3

    def test_total_files_modified(self) -> None:
        """Counts total files modified."""
        tool1 = ToolResult(tool_id="black", status="clean", files_modified=5)
        tool2 = ToolResult(tool_id="ruff", status="clean", files_modified=3)
        result = LintResult(action="fix", dry_run=False, tools_run=[tool1, tool2])
        assert result.total_files_modified == 8

    def test_has_errors_true(self) -> None:
        """has_errors is True when any error severity."""
        tool = ToolResult(
            tool_id="mypy",
            status="dirty",
            diagnostics=[
                Diagnostic(path="x", line=1, message="m", source="mypy", severity=Severity.ERROR)
            ],
        )
        result = LintResult(action="check", dry_run=False, tools_run=[tool])
        assert result.has_errors is True

    def test_has_errors_false(self) -> None:
        """has_errors is False when only warnings."""
        tool = ToolResult(
            tool_id="ruff",
            status="dirty",
            diagnostics=[
                Diagnostic(path="x", line=1, message="m", source="ruff", severity=Severity.WARNING)
            ],
        )
        result = LintResult(action="check", dry_run=False, tools_run=[tool])
        assert result.has_errors is False

    def test_status_clean(self) -> None:
        """Status is clean when all tools clean."""
        result = LintResult(
            action="check",
            dry_run=False,
            tools_run=[ToolResult(tool_id="ruff", status="clean")],
        )
        assert result.status == "clean"

    def test_status_dirty(self) -> None:
        """Status is dirty when any tool dirty."""
        result = LintResult(
            action="check",
            dry_run=False,
            tools_run=[
                ToolResult(tool_id="ruff", status="clean"),
                ToolResult(tool_id="mypy", status="dirty"),
            ],
        )
        assert result.status == "dirty"

    def test_status_error(self) -> None:
        """Status is error when any tool errored."""
        result = LintResult(
            action="check",
            dry_run=False,
            tools_run=[
                ToolResult(tool_id="ruff", status="dirty"),
                ToolResult(tool_id="mypy", status="error"),
            ],
        )
        assert result.status == "error"

    def test_agentic_hint(self) -> None:
        """Agentic hint can be set."""
        result = LintResult(
            action="check",
            dry_run=False,
            agentic_hint="No tools detected. Run: pip install ruff mypy",
        )
        assert result.agentic_hint is not None
        assert "ruff" in result.agentic_hint
