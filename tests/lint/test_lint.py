"""Tests for lint module."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeplane.lint import (
    Diagnostic,
    LintOps,
    LintResult,
    Severity,
    ToolCategory,
    ToolResult,
    parsers,
    registry,
)


def create_mock_coordinator() -> MagicMock:
    """Create a mock IndexCoordinator for testing."""
    coordinator = MagicMock()
    coordinator.get_file_stats = AsyncMock(return_value={"python": 10})
    coordinator.get_indexed_file_count = AsyncMock(return_value=10)
    coordinator.get_indexed_files = AsyncMock(return_value=["src/foo.py", "src/bar.py"])
    coordinator.get_contexts = AsyncMock(return_value=[])
    return coordinator


class TestModels:
    def test_diagnostic_creation(self) -> None:
        d = Diagnostic(
            path="src/foo.py",
            line=42,
            message="Unused import",
            source="ruff",
            severity=Severity.WARNING,
            code="F401",
        )
        assert d.path == "src/foo.py"
        assert d.line == 42
        assert d.severity == Severity.WARNING
        assert d.code == "F401"

    def test_tool_result_status(self) -> None:
        result = ToolResult(
            tool_id="ruff",
            status="clean",
            files_checked=10,
        )
        assert result.status == "clean"
        assert result.diagnostics == []

    def test_lint_result_aggregation(self) -> None:
        result = LintResult(
            action="fix",
            dry_run=False,
            tools_run=[
                ToolResult(
                    tool_id="ruff",
                    status="dirty",
                    diagnostics=[
                        Diagnostic(path="a.py", line=1, message="m1", source="ruff"),
                        Diagnostic(path="b.py", line=2, message="m2", source="ruff"),
                    ],
                    files_modified=1,
                ),
                ToolResult(
                    tool_id="mypy",
                    status="clean",
                    diagnostics=[],
                ),
            ],
        )
        assert result.total_diagnostics == 2
        assert result.total_files_modified == 1
        assert result.status == "dirty"
        assert not result.has_errors

    def test_lint_result_has_errors(self) -> None:
        result = LintResult(
            action="check",
            dry_run=True,
            tools_run=[
                ToolResult(
                    tool_id="mypy",
                    status="dirty",
                    diagnostics=[
                        Diagnostic(
                            path="a.py",
                            line=1,
                            message="type error",
                            source="mypy",
                            severity=Severity.ERROR,
                        ),
                    ],
                ),
            ],
        )
        assert result.has_errors


class TestRegistry:
    def test_registry_has_tools(self) -> None:
        tools = registry.all()
        assert len(tools) >= 30  # We registered 32

    def test_registry_get_tool(self) -> None:
        tool = registry.get("python.ruff")
        assert tool is not None
        assert tool.name == "Ruff"
        assert "python" in tool.languages

    def test_registry_for_language(self) -> None:
        python_tools = registry.for_language("python")
        assert len(python_tools) >= 6  # ruff, ruff-format, mypy, pyright, bandit, black, isort
        assert all("python" in t.languages for t in python_tools)

    def test_registry_for_category(self) -> None:
        lint_tools = registry.for_category(ToolCategory.LINT)
        assert len(lint_tools) >= 10
        assert all(t.category == ToolCategory.LINT for t in lint_tools)

    def test_registry_detect(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create a pyproject.toml to trigger ruff detection
            (root / "pyproject.toml").write_text("[tool.ruff]\n")

            detected = registry.detect(root)
            tool_ids = [t.tool_id for t in detected]
            assert "python.ruff" in tool_ids

    def test_registry_detect_empty(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            detected = registry.detect(root)
            assert detected == []


class TestParsers:
    def test_parse_ruff(self) -> None:
        output = """[
            {
                "code": "F401",
                "filename": "src/foo.py",
                "location": {"row": 1, "column": 8},
                "end_location": {"row": 1, "column": 10},
                "message": "os imported but unused"
            }
        ]"""
        diagnostics = parsers.parse_ruff(output, "")
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/foo.py"
        assert diagnostics[0].line == 1
        assert diagnostics[0].code == "F401"

    def test_parse_mypy(self) -> None:
        output = """{"file": "src/bar.py", "line": 10, "column": 5, "severity": "error", "code": "arg-type", "message": "Argument 1 has incompatible type"}"""
        diagnostics = parsers.parse_mypy(output, "")
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/bar.py"
        assert diagnostics[0].severity == Severity.ERROR

    def test_parse_eslint(self) -> None:
        output = """[
            {
                "filePath": "/repo/src/app.js",
                "messages": [
                    {"line": 5, "column": 1, "severity": 2, "ruleId": "no-unused-vars", "message": "'x' is defined but never used"}
                ]
            }
        ]"""
        diagnostics = parsers.parse_eslint(output, "")
        assert len(diagnostics) == 1
        assert diagnostics[0].severity == Severity.ERROR  # severity 2 = error
        assert diagnostics[0].code == "no-unused-vars"

    def test_parse_tsc(self) -> None:
        output = """src/index.ts(10,5): error TS2345: Argument of type 'string' is not assignable to parameter of type 'number'."""
        diagnostics = parsers.parse_tsc(output, "")
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/index.ts"
        assert diagnostics[0].line == 10
        assert diagnostics[0].column == 5
        assert diagnostics[0].code == "TS2345"

    def test_parse_go_vet(self) -> None:
        output = "main.go:15:2: printf: Printf format %d has arg of wrong type"
        diagnostics = parsers.parse_go_vet("", output)
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "main.go"
        assert diagnostics[0].line == 15

    def test_parse_clippy(self) -> None:
        output = """{"reason":"compiler-message","message":{"level":"warning","code":{"code":"clippy::needless_return"},"message":"unneeded `return` statement","spans":[{"file_name":"src/main.rs","line_start":10,"line_end":10,"column_start":5,"column_end":15,"is_primary":true}]}}"""
        diagnostics = parsers.parse_clippy(output, "")
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/main.rs"
        assert diagnostics[0].code == "clippy::needless_return"

    def test_parse_gofmt(self) -> None:
        output = "main.go\npkg/utils.go\n"
        diagnostics = parsers.parse_gofmt(output, "")
        assert len(diagnostics) == 2
        assert diagnostics[0].path == "main.go"
        assert diagnostics[1].path == "pkg/utils.go"

    def test_parse_shellcheck(self) -> None:
        output = """[
            {"file": "script.sh", "line": 5, "column": 1, "level": "warning", "code": 2086, "message": "Double quote to prevent globbing"}
        ]"""
        diagnostics = parsers.parse_shellcheck(output, "")
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "SC2086"

    def test_parse_rubocop(self) -> None:
        output = """{
            "files": [
                {
                    "path": "lib/foo.rb",
                    "offenses": [
                        {"severity": "convention", "cop_name": "Style/StringLiterals", "message": "Prefer double-quoted strings", "location": {"start_line": 3, "start_column": 5}}
                    ]
                }
            ]
        }"""
        diagnostics = parsers.parse_rubocop(output, "")
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "lib/foo.rb"
        assert diagnostics[0].code == "Style/StringLiterals"

    def test_parse_empty_output(self) -> None:
        assert parsers.parse_ruff("", "") == []
        assert parsers.parse_mypy("", "") == []
        assert parsers.parse_eslint("", "") == []

    def test_parse_invalid_json(self) -> None:
        assert parsers.parse_ruff("not json", "") == []
        assert parsers.parse_eslint("{invalid}", "") == []


class TestLintOps:
    @pytest.mark.asyncio
    async def test_check_returns_lint_result(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            # No tools configured, should return empty result with agentic hint
            result = await ops.check()

            assert isinstance(result, LintResult)
            assert result.action == "fix"  # default is fix mode
            assert result.dry_run is False
            assert result.agentic_hint is not None  # Should have hint when no tools

    @pytest.mark.asyncio
    async def test_check_dry_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            result = await ops.check(dry_run=True)

            assert result.action == "check"
            assert result.dry_run is True

    @pytest.mark.asyncio
    async def test_check_skips_missing_executable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            # Create config for a tool with non-existent executable
            (root / "pyproject.toml").write_text("[tool.ruff]\n")

            ops = LintOps(root, coordinator)
            # Force a non-existent tool
            result = await ops.check(tools=["python.nonexistent"])

            # Should complete without error (no such tool)
            assert result is not None
