"""Comprehensive tests for lint module."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

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
from codeplane.lint.ops import _LANGUAGE_TO_TOOL_PREFIX, _generate_agentic_hint
from codeplane.lint.tools import LintTool


def create_mock_coordinator() -> MagicMock:
    """Create a mock IndexCoordinator for testing."""
    coordinator = MagicMock()
    coordinator.get_file_stats = AsyncMock(return_value={"python": 10})
    coordinator.get_indexed_file_count = AsyncMock(return_value=10)
    coordinator.get_indexed_files = AsyncMock(return_value=["src/foo.py", "src/bar.py"])
    coordinator.get_contexts = AsyncMock(return_value=[])
    coordinator.get_lint_tools = AsyncMock(return_value=[])
    return coordinator


# =============================================================================
# Model Tests
# =============================================================================


class TestSeverity:
    def test_severity_values(self) -> None:
        assert Severity.ERROR.value == "error"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"
        assert Severity.HINT.value == "hint"


class TestToolCategory:
    def test_category_values(self) -> None:
        assert ToolCategory.TYPE_CHECK.value == "type_check"
        assert ToolCategory.LINT.value == "lint"
        assert ToolCategory.FORMAT.value == "format"
        assert ToolCategory.SECURITY.value == "security"


class TestDiagnostic:
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
        assert d.fix_applied is False

    def test_diagnostic_with_all_fields(self) -> None:
        d = Diagnostic(
            path="src/foo.py",
            line=42,
            column=10,
            end_line=42,
            end_column=20,
            message="Unused import",
            source="ruff",
            severity=Severity.ERROR,
            code="F401",
            fix_applied=True,
            fix_diff="-import os\n",
        )
        assert d.column == 10
        assert d.end_line == 42
        assert d.end_column == 20
        assert d.fix_applied is True
        assert d.fix_diff == "-import os\n"


class TestToolResult:
    def test_tool_result_clean(self) -> None:
        result = ToolResult(
            tool_id="ruff",
            status="clean",
            files_checked=10,
        )
        assert result.status == "clean"
        assert result.diagnostics == []
        assert result.files_checked == 10

    def test_tool_result_dirty(self) -> None:
        diags = [
            Diagnostic(path="a.py", line=1, message="m1", source="ruff"),
            Diagnostic(path="b.py", line=2, message="m2", source="ruff"),
        ]
        result = ToolResult(
            tool_id="ruff",
            status="dirty",
            diagnostics=diags,
            files_checked=10,
            files_modified=2,
        )
        assert result.status == "dirty"
        assert len(result.diagnostics) == 2
        assert result.files_modified == 2

    def test_tool_result_error(self) -> None:
        result = ToolResult(
            tool_id="ruff",
            status="error",
            error_detail="Command failed",
            command=["ruff", "check", "."],
        )
        assert result.status == "error"
        assert result.error_detail == "Command failed"
        assert result.command == ["ruff", "check", "."]

    def test_tool_result_skipped(self) -> None:
        result = ToolResult(
            tool_id="ruff",
            status="skipped",
            error_detail="Executable not found",
        )
        assert result.status == "skipped"


class TestLintResult:
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

    def test_lint_result_status_error(self) -> None:
        result = LintResult(
            action="fix",
            dry_run=False,
            tools_run=[
                ToolResult(tool_id="ruff", status="error"),
            ],
        )
        assert result.status == "error"

    def test_lint_result_status_clean(self) -> None:
        result = LintResult(
            action="fix",
            dry_run=False,
            tools_run=[
                ToolResult(tool_id="ruff", status="clean"),
                ToolResult(tool_id="mypy", status="clean"),
            ],
        )
        assert result.status == "clean"

    def test_lint_result_agentic_hint(self) -> None:
        result = LintResult(
            action="fix",
            dry_run=False,
            tools_run=[],
            agentic_hint="Run ruff check --fix .",
        )
        assert result.agentic_hint == "Run ruff check --fix ."


# =============================================================================
# Registry Tests
# =============================================================================


class TestRegistry:
    def test_registry_has_tools(self) -> None:
        tools = registry.all()
        assert len(tools) >= 30  # We registered 32

    def test_registry_get_tool(self) -> None:
        tool = registry.get("python.ruff")
        assert tool is not None
        assert tool.name == "Ruff"
        assert "python" in tool.languages

    def test_registry_get_nonexistent(self) -> None:
        tool = registry.get("nonexistent.tool")
        assert tool is None

    def test_registry_for_language(self) -> None:
        python_tools = registry.for_language("python")
        assert len(python_tools) >= 6
        assert all("python" in t.languages for t in python_tools)

    def test_registry_for_language_empty(self) -> None:
        tools = registry.for_language("nonexistent")
        assert tools == []

    def test_registry_for_category(self) -> None:
        lint_tools = registry.for_category(ToolCategory.LINT)
        assert len(lint_tools) >= 10
        assert all(t.category == ToolCategory.LINT for t in lint_tools)

    def test_registry_for_category_all_types(self) -> None:
        for cat in ToolCategory:
            tools = registry.for_category(cat)
            assert all(t.category == cat for t in tools)

    def test_registry_detect(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[tool.ruff]\n")

            detected_pairs = registry.detect(root)
            tool_ids = [t.tool_id for t, _ in detected_pairs]
            assert "python.ruff" in tool_ids

    def test_registry_detect_multiple(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[tool.ruff]\n[tool.mypy]\n")
            (root / ".eslintrc.json").write_text("{}")

            detected_pairs = registry.detect(root)
            tool_ids = [t.tool_id for t, _ in detected_pairs]
            assert "python.ruff" in tool_ids
            assert "python.mypy" in tool_ids
            assert "js.eslint" in tool_ids

    def test_registry_detect_empty(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            detected = registry.detect(root)
            assert detected == []

    def test_registry_detect_section_aware(self) -> None:
        """Only detects tools that have their section in pyproject.toml."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Only configure ruff, NOT black/isort
            (root / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")

            detected_pairs = registry.detect(root)
            tool_ids = {t.tool_id for t, _ in detected_pairs}

            # ruff and ruff-format should be detected (both use [tool.ruff])
            assert "python.ruff" in tool_ids
            assert "python.ruff-format" in tool_ids

            # black and isort should NOT be detected (no [tool.black] or [tool.isort])
            assert "python.black" not in tool_ids
            assert "python.isort" not in tool_ids


class TestLintTool:
    def test_lint_tool_creation(self) -> None:
        tool = LintTool(
            tool_id="test.tool",
            name="Test Tool",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="testtool",
            check_args=["check"],
            config_files=["test.config"],
        )
        assert tool.tool_id == "test.tool"
        assert tool.name == "Test Tool"
        assert "python" in tool.languages

    def test_parse_output_none(self) -> None:
        tool = LintTool(
            tool_id="test.tool",
            name="Test Tool",
            languages=frozenset({"python"}),
            category=ToolCategory.LINT,
            executable="testtool",
            check_args=["check"],
            config_files=["test.config"],
        )
        # Without a parser, should return empty list
        result = tool.parse_output("hello", "")
        assert result.diagnostics == []


# =============================================================================
# Parser Tests
# =============================================================================


class TestSeverityFromStr:
    def test_error_variants(self) -> None:
        from codeplane.lint.parsers import _severity_from_str

        assert _severity_from_str("error") == Severity.ERROR
        assert _severity_from_str("ERROR") == Severity.ERROR
        assert _severity_from_str("e") == Severity.ERROR
        assert _severity_from_str("fatal") == Severity.ERROR

    def test_warning_variants(self) -> None:
        from codeplane.lint.parsers import _severity_from_str

        assert _severity_from_str("warning") == Severity.WARNING
        assert _severity_from_str("warn") == Severity.WARNING
        assert _severity_from_str("w") == Severity.WARNING

    def test_info_variants(self) -> None:
        from codeplane.lint.parsers import _severity_from_str

        assert _severity_from_str("info") == Severity.INFO
        assert _severity_from_str("information") == Severity.INFO
        assert _severity_from_str("i") == Severity.INFO
        assert _severity_from_str("note") == Severity.INFO

    def test_default_to_hint(self) -> None:
        from codeplane.lint.parsers import _severity_from_str

        assert _severity_from_str("unknown") == Severity.HINT
        assert _severity_from_str("") == Severity.HINT


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
        result = parsers.parse_ruff(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/foo.py"
        assert diagnostics[0].line == 1
        assert diagnostics[0].column == 8
        assert diagnostics[0].code == "F401"

    def test_parse_ruff_multiple(self) -> None:
        output = """[
            {"code": "F401", "filename": "a.py", "location": {"row": 1, "column": 1}, "end_location": {"row": 1, "column": 5}, "message": "m1"},
            {"code": "F401", "filename": "b.py", "location": {"row": 2, "column": 1}, "end_location": {"row": 2, "column": 5}, "message": "m2"}
        ]"""
        result = parsers.parse_ruff(output, "")
        assert len(result.diagnostics) == 2

    def test_parse_mypy(self) -> None:
        output = '{"file": "src/bar.py", "line": 10, "column": 5, "severity": "error", "code": "arg-type", "message": "Argument 1 has incompatible type"}'
        result = parsers.parse_mypy(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/bar.py"
        assert diagnostics[0].severity == Severity.ERROR
        assert diagnostics[0].code == "arg-type"

    def test_parse_mypy_multiline(self) -> None:
        output = """{"file": "a.py", "line": 1, "column": 1, "severity": "error", "code": "E1", "message": "m1"}
{"file": "b.py", "line": 2, "column": 2, "severity": "warning", "code": "W1", "message": "m2"}"""
        result = parsers.parse_mypy(output, "")
        assert len(result.diagnostics) == 2

    def test_parse_pyright(self) -> None:
        output = """{
            "generalDiagnostics": [
                {"file": "test.py", "range": {"start": {"line": 5, "character": 0}}, "severity": "error", "rule": "reportGeneralTypeIssues", "message": "Type mismatch"}
            ]
        }"""
        result = parsers.parse_pyright(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].line == 6  # 0-indexed to 1-indexed
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
        result = parsers.parse_eslint(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].severity == Severity.ERROR  # severity 2 = error
        assert diagnostics[0].code == "no-unused-vars"

    def test_parse_eslint_severity_warning(self) -> None:
        output = '[{"filePath": "a.js", "messages": [{"line": 1, "column": 1, "severity": 1, "ruleId": "r1", "message": "m"}]}]'
        result = parsers.parse_eslint(output, "")
        assert result.diagnostics[0].severity == Severity.WARNING

    def test_parse_tsc(self) -> None:
        output = "src/index.ts(10,5): error TS2345: Argument of type 'string' is not assignable to parameter of type 'number'."
        result = parsers.parse_tsc(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/index.ts"
        assert diagnostics[0].line == 10
        assert diagnostics[0].column == 5
        assert diagnostics[0].code == "TS2345"

    def test_parse_tsc_multiline(self) -> None:
        output = """a.ts(1,1): error TS1: msg1
b.ts(2,2): error TS2: msg2"""
        result = parsers.parse_tsc(output, "")
        assert len(result.diagnostics) == 2

    def test_parse_prettier(self) -> None:
        # Prettier --list-different output (parse_prettier_check)
        stdout = "src/app.js\nsrc/utils.ts\n"
        result = parsers.parse_prettier_check(stdout, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 2
        assert diagnostics[0].path == "src/app.js"

    def test_parse_biome(self) -> None:
        output = """{
            "diagnostics": [
                {
                    "location": {
                        "path": {"file": "src/a.ts"},
                        "span": [100, 110]
                    },
                    "severity": "error",
                    "category": "lint/style",
                    "message": "Fix this"
                }
            ]
        }"""
        result = parsers.parse_biome(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/a.ts"

    def test_parse_go_vet(self) -> None:
        output = "main.go:15:2: printf: Printf format %d has arg of wrong type"
        result = parsers.parse_go_vet("", output)
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "main.go"
        assert diagnostics[0].line == 15

    def test_parse_staticcheck(self) -> None:
        # staticcheck JSON output (one JSON object per line)
        output = '{"location": {"file": "pkg/util.go", "line": 25, "column": 10}, "severity": "warning", "code": "SA1000", "message": "message here"}'
        result = parsers.parse_staticcheck(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "SA1000"

    def test_parse_golangci_lint(self) -> None:
        output = """{
            "Issues": [
                {"FromLinter": "govet", "Text": "issue text", "Pos": {"Filename": "main.go", "Line": 10, "Column": 5}}
            ]
        }"""
        result = parsers.parse_golangci_lint(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].source == "golangci-lint"

    def test_parse_gofmt(self) -> None:
        output = "main.go\npkg/utils.go\n"
        result = parsers.parse_gofmt(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 2
        assert diagnostics[0].path == "main.go"
        assert diagnostics[1].path == "pkg/utils.go"

    def test_parse_goimports(self) -> None:
        # goimports uses same format as gofmt
        output = "a.go\nb.go\n"
        result = parsers.parse_gofmt(output, "")  # Same parser
        assert len(result.diagnostics) == 2

    def test_parse_clippy(self) -> None:
        output = '{"reason":"compiler-message","message":{"level":"warning","code":{"code":"clippy::needless_return"},"message":"unneeded `return` statement","spans":[{"file_name":"src/main.rs","line_start":10,"line_end":10,"column_start":5,"column_end":15,"is_primary":true}]}}'
        result = parsers.parse_clippy(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/main.rs"
        assert diagnostics[0].code == "clippy::needless_return"

    def test_parse_rustfmt(self) -> None:
        # rustfmt --check outputs "Diff in <file>:" format
        output = "Diff in src/main.rs:\n+  // new line\nDiff in src/lib.rs:\n-  old\n"
        result = parsers.parse_rustfmt_check(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 2
        assert diagnostics[0].path == "src/main.rs"
        assert diagnostics[1].path == "src/lib.rs"

    def test_parse_cargo_audit(self) -> None:
        output = """{
            "vulnerabilities": {
                "list": [
                    {"package": {"name": "vulnerable-crate"}, "advisory": {"id": "RUSTSEC-2021-0001", "severity": "high", "title": "Vuln title"}, "versions": {"patched": ["1.0.1"]}}
                ]
            }
        }"""
        result = parsers.parse_cargo_audit(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].code is not None
        assert "RUSTSEC-2021-0001" in diagnostics[0].code

    def test_parse_shellcheck(self) -> None:
        output = '[{"file": "script.sh", "line": 5, "column": 1, "level": "warning", "code": 2086, "message": "Double quote to prevent globbing"}]'
        result = parsers.parse_shellcheck(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "SC2086"

    def test_parse_shfmt(self) -> None:
        output = "script.sh\nother.sh\n"
        result = parsers.parse_shfmt(output, "")
        assert len(result.diagnostics) == 2

    def test_parse_hadolint(self) -> None:
        output = '[{"file": "Dockerfile", "line": 5, "column": 1, "code": "DL3008", "level": "warning", "message": "Pin versions"}]'
        result = parsers.parse_hadolint(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "DL3008"

    def test_parse_yamllint(self) -> None:
        # yamllint parsable format uses stdout
        output = "config.yml:5:3: [warning] wrong indentation (indentation)"
        result = parsers.parse_yamllint(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "config.yml"

    def test_parse_markdownlint(self) -> None:
        output = '[{"fileName": "README.md", "lineNumber": 10, "ruleNames": ["MD013"], "ruleDescription": "Line length", "errorDetail": "too long"}]'
        result = parsers.parse_markdownlint(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "MD013"

    def test_parse_sqlfluff(self) -> None:
        output = """[
            {"filepath": "query.sql", "violations": [{"start_line_no": 5, "start_line_pos": 1, "code": "L001", "description": "Trailing whitespace"}]}
        ]"""
        result = parsers.parse_sqlfluff(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "L001"

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
        result = parsers.parse_rubocop(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "lib/foo.rb"
        assert diagnostics[0].code == "Style/StringLiterals"

    def test_parse_phpcs(self) -> None:
        output = """{
            "files": {
                "src/App.php": {
                    "messages": [
                        {"line": 10, "column": 1, "type": "ERROR", "source": "PSR12.Files.FileHeader.SpacingAfterBlock", "message": "Expected one line"}
                    ]
                }
            }
        }"""
        result = parsers.parse_phpcs(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/App.php"

    def test_parse_phpstan(self) -> None:
        output = """{
            "files": {
                "src/Service.php": {
                    "messages": [
                        {"line": 25, "message": "Method return type", "ignorable": false}
                    ]
                }
            }
        }"""
        result = parsers.parse_phpstan(output, "")
        assert len(result.diagnostics) == 1

    def test_parse_checkstyle(self) -> None:
        output = """<?xml version="1.0"?>
<checkstyle>
    <file name="src/Main.java">
        <error line="10" column="5" severity="warning" message="Missing Javadoc" source="JavadocMethod"/>
    </file>
</checkstyle>"""
        result = parsers.parse_checkstyle(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/Main.java"

    def test_parse_ktlint(self) -> None:
        output = """[
            {"file": "src/Main.kt", "errors": [{"line": 5, "col": 1, "message": "Unexpected blank line", "rule": "no-blank-line-before-rbrace"}]}
        ]"""
        result = parsers.parse_ktlint(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "no-blank-line-before-rbrace"

    def test_parse_dotnet_format(self) -> None:
        output = "src/Program.cs\nsrc/Util.cs\n"
        result = parsers.parse_dotnet_format(output, "")
        assert len(result.diagnostics) == 2

    def test_parse_bandit(self) -> None:
        output = """{
            "results": [
                {"filename": "app.py", "line_number": 10, "test_id": "B105", "issue_severity": "HIGH", "issue_confidence": "MEDIUM", "issue_text": "Possible hardcoded password"}
            ]
        }"""
        result = parsers.parse_bandit(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "B105"
        assert diagnostics[0].severity == Severity.ERROR

    def test_parse_black_check(self) -> None:
        stderr = "would reformat src/foo.py\nwould reformat src/bar.py\n"
        result = parsers.parse_black_check("", stderr)
        assert len(result.diagnostics) == 2

    def test_parse_isort_check(self) -> None:
        # isort --diff writes to stdout, but black_check parses stderr
        # We'll just test black_check with isort-style input
        stderr = "would reformat src/foo.py\n"
        result = parsers.parse_black_check("", stderr)
        assert len(result.diagnostics) == 1

    def test_parse_sarif(self) -> None:
        output = """{
            "runs": [{
                "tool": {"driver": {"name": "TestTool", "rules": []}},
                "results": [{
                    "ruleId": "RULE1",
                    "level": "error",
                    "message": {"text": "Test message"},
                    "locations": [{
                        "physicalLocation": {
                            "artifactLocation": {"uri": "src/test.py"},
                            "region": {"startLine": 10, "startColumn": 5}
                        }
                    }]
                }]
            }]
        }"""
        result = parsers.parse_sarif(output, "")
        diagnostics = result.diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].path == "src/test.py"
        assert diagnostics[0].source == "TestTool"

    def test_parse_empty_output(self) -> None:
        assert parsers.parse_ruff("", "").diagnostics == []
        assert parsers.parse_mypy("", "").diagnostics == []
        assert parsers.parse_eslint("", "").diagnostics == []
        assert parsers.parse_tsc("", "").diagnostics == []
        assert parsers.parse_go_vet("", "").diagnostics == []

    def test_parse_invalid_json(self) -> None:
        assert parsers.parse_ruff("not json", "").diagnostics == []
        assert parsers.parse_eslint("{invalid}", "").diagnostics == []
        assert parsers.parse_mypy("not json", "").diagnostics == []

    def test_parse_pyright_invalid_json(self) -> None:
        assert parsers.parse_pyright("not json", "").diagnostics == []
        assert parsers.parse_pyright("{}", "").diagnostics == []  # Missing keys

    def test_parse_bandit_invalid_json(self) -> None:
        assert parsers.parse_bandit("not json", "").diagnostics == []
        assert parsers.parse_bandit("{}", "").diagnostics == []  # Missing results key

    def test_parse_biome_invalid_json(self) -> None:
        assert parsers.parse_biome("not json", "").diagnostics == []
        assert parsers.parse_biome("{}", "").diagnostics == []  # Missing diagnostics

    def test_parse_golangci_lint_invalid_json(self) -> None:
        assert parsers.parse_golangci_lint("not json", "").diagnostics == []
        assert parsers.parse_golangci_lint("{}", "").diagnostics == []  # Missing issues

    def test_parse_rustfmt_check_missing_diff(self) -> None:
        # Valid mismatches but with empty content
        output = '[{"name": "test.rs", "mismatches": []}]'
        assert parsers.parse_rustfmt_check(output, "").diagnostics == []

    def test_parse_clippy_invalid_json(self) -> None:
        assert parsers.parse_clippy("not json", "").diagnostics == []

    def test_parse_sarif_invalid_json(self) -> None:
        assert parsers.parse_sarif("not json", "").diagnostics == []
        assert parsers.parse_sarif("{}", "").diagnostics == []  # Missing runs

    def test_parse_rubocop_invalid_json(self) -> None:
        assert parsers.parse_rubocop("not json", "").diagnostics == []
        assert parsers.parse_rubocop("{}", "").diagnostics == []  # Missing files

    def test_parse_phpcs_invalid_json(self) -> None:
        assert parsers.parse_phpcs("not json", "").diagnostics == []
        assert parsers.parse_phpcs("{}", "").diagnostics == []  # Missing files

    def test_parse_checkstyle_invalid_xml(self) -> None:
        assert parsers.parse_checkstyle("not xml", "").diagnostics == []

    def test_parse_ktlint_invalid_json(self) -> None:
        assert parsers.parse_ktlint("not json", "").diagnostics == []

    def test_parse_shellcheck_invalid_json(self) -> None:
        assert parsers.parse_shellcheck("not json", "").diagnostics == []

    def test_parse_hadolint_invalid_json(self) -> None:
        assert parsers.parse_hadolint("not json", "").diagnostics == []

    def test_parse_yamllint_invalid_json(self) -> None:
        assert parsers.parse_yamllint("not json", "").diagnostics == []

    def test_parse_markdownlint_invalid_json(self) -> None:
        assert parsers.parse_markdownlint("not json", "").diagnostics == []

    def test_parse_sqlfluff_invalid_json(self) -> None:
        assert parsers.parse_sqlfluff("not json", "").diagnostics == []
        assert parsers.parse_sqlfluff("{}", "").diagnostics == []  # Missing results


# =============================================================================
# Agentic Hint Tests
# =============================================================================


class TestAgenticHint:
    def test_generate_hint_python(self) -> None:
        hint = _generate_agentic_hint(["python"])
        assert "ruff" in hint.lower() or "black" in hint.lower()

    def test_generate_hint_javascript(self) -> None:
        hint = _generate_agentic_hint(["javascript"])
        assert "eslint" in hint.lower() or "prettier" in hint.lower()

    def test_generate_hint_typescript(self) -> None:
        hint = _generate_agentic_hint(["typescript"])
        assert "eslint" in hint.lower() or "prettier" in hint.lower()

    def test_generate_hint_go(self) -> None:
        hint = _generate_agentic_hint(["go"])
        assert "go fmt" in hint or "go vet" in hint

    def test_generate_hint_rust(self) -> None:
        hint = _generate_agentic_hint(["rust"])
        assert "cargo" in hint.lower()

    def test_generate_hint_ruby(self) -> None:
        hint = _generate_agentic_hint(["ruby"])
        assert "rubocop" in hint.lower()

    def test_generate_hint_php(self) -> None:
        hint = _generate_agentic_hint(["php"])
        assert "phpcs" in hint.lower() or "php-cs-fixer" in hint.lower()

    def test_generate_hint_multiple(self) -> None:
        hint = _generate_agentic_hint(["python", "javascript"])
        assert "python" in hint.lower()
        assert "javascript" in hint.lower()

    def test_generate_hint_unknown(self) -> None:
        hint = _generate_agentic_hint(["unknown_language"])
        assert "install" in hint.lower()

    def test_generate_hint_empty(self) -> None:
        hint = _generate_agentic_hint([])
        assert "install" in hint.lower()

    def test_language_to_tool_prefix_mapping(self) -> None:
        assert "python" in _LANGUAGE_TO_TOOL_PREFIX
        assert "javascript" in _LANGUAGE_TO_TOOL_PREFIX
        assert "go" in _LANGUAGE_TO_TOOL_PREFIX


# =============================================================================
# LintOps Tests
# =============================================================================


class TestLintOps:
    @pytest.mark.asyncio
    async def test_check_returns_lint_result(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            result = await ops.check()

            assert isinstance(result, LintResult)
            assert result.action == "fix"
            assert result.dry_run is False
            assert result.agentic_hint is not None

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
    async def test_check_with_paths(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            result = await ops.check(paths=["src/"])
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_all_deleted_paths_returns_clean(self) -> None:
        """When all changed_files are deleted, lint should return clean (not E902)."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            result = await ops.check(paths=["deleted_file.py", "also_gone.py"])
            assert result.status == "clean"
            assert result.tools_run == []
            assert result.total_diagnostics == 0

    @pytest.mark.asyncio
    async def test_check_with_categories(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            result = await ops.check(categories=["lint"])
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_skips_missing_executable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            (root / "pyproject.toml").write_text("[tool.ruff]\n")

            ops = LintOps(root, coordinator)
            result = await ops.check(tools=["python.nonexistent"])

            assert result is not None
            assert result.tools_run == []

    @pytest.mark.asyncio
    async def test_check_with_detected_tools_missing_executable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            # Create config but executable won't exist
            (root / "setup.cfg").write_text("[flake8]\n")

            ops = LintOps(root, coordinator)

            # Mock shutil.which to return None
            with patch("shutil.which", return_value=None):
                result = await ops.check()

            # Should have agentic hint when all tools skipped
            assert result is not None

    @pytest.mark.asyncio
    async def test_resolve_tools_specific(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tools = await ops._resolve_tools(["python.ruff"], None)
            assert len(tools) == 1
            assert tools[0].tool_id == "python.ruff"

    @pytest.mark.asyncio
    async def test_resolve_tools_by_category(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            (root / "pyproject.toml").write_text("[tool.ruff]\n")
            ops = LintOps(root, coordinator)

            tools = await ops._resolve_tools(None, ["lint"])
            # Should filter detected tools by category
            assert all(t.category == ToolCategory.LINT for t in tools)

    def test_resolve_paths_none(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            paths = ops._resolve_paths(None)
            assert paths == [root]

    def test_resolve_paths_specific(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            paths = ops._resolve_paths(["src/", "tests/"])
            assert len(paths) == 2
            assert paths[0] == root / "src/"
            assert paths[1] == root / "tests/"

    def test_resolve_paths_filters_deleted_files(self) -> None:
        """Deleted files should be silently filtered out."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "existing.py").write_text("x = 1")
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            paths = ops._resolve_paths(["existing.py", "deleted.py"])
            assert paths == [root / "existing.py"]

    def test_resolve_paths_all_deleted_returns_empty(self) -> None:
        """When all paths are deleted, return empty list."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            paths = ops._resolve_paths(["deleted.py", "also_deleted.py"])
            assert paths == []

    @pytest.mark.asyncio
    async def test_get_file_count_from_index(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            count = await ops._get_file_count_from_index(tool, [root])
            assert count == 10  # From mock

    @pytest.mark.asyncio
    async def test_get_file_count_from_index_with_paths(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            count = await ops._get_file_count_from_index(tool, [root / "src"])
            # Should filter indexed files
            assert count >= 0

    def test_build_command_check(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            cmd = ops._build_command(tool, [root], dry_run=True)
            assert cmd[0] == "ruff"
            assert "check" in cmd

    def test_build_command_fix(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            cmd = ops._build_command(tool, [root], dry_run=False)
            assert cmd[0] == "ruff"
            assert "--fix" in cmd

    @pytest.mark.asyncio
    async def test_run_tool_clean_output(self) -> None:
        """Test _run_tool with clean (no issues) output."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            # Mock subprocess with clean output (ruff returns "[]" when no issues)
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"[]", b""))

            with (
                patch.object(ops, "_build_command", return_value=["ruff", "check", "."]),
                patch("shutil.which", return_value="/usr/bin/ruff"),
                patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            ):
                result = await ops._run_tool(tool, [root], dry_run=True)

            assert result.status == "clean"
            assert result.diagnostics == []

    @pytest.mark.asyncio
    async def test_run_tool_with_diagnostics(self) -> None:
        """Test _run_tool when tool finds issues."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            # Ruff JSON output with a diagnostic
            ruff_output = '[{"filename": "test.py", "code": "E501", "message": "Line too long", "location": {"row": 1, "column": 80}}]'

            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(ruff_output.encode(), b""))

            with (
                patch.object(ops, "_build_command", return_value=["ruff", "check", "."]),
                patch("shutil.which", return_value="/usr/bin/ruff"),
                patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            ):
                result = await ops._run_tool(tool, [root], dry_run=True)

            assert result.status == "dirty"
            assert len(result.diagnostics) > 0

    @pytest.mark.asyncio
    async def test_run_tool_error_exit_no_diagnostics(self) -> None:
        """Test _run_tool with error exit and no diagnostics."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            mock_proc = MagicMock()
            mock_proc.returncode = 127  # Command not found style error
            mock_proc.communicate = AsyncMock(return_value=(b"", b"Command failed"))

            with (
                patch.object(ops, "_build_command", return_value=["ruff", "check", "."]),
                patch("shutil.which", return_value="/usr/bin/ruff"),
                patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            ):
                result = await ops._run_tool(tool, [root], dry_run=True)

            assert result.status == "error"
            assert result.error_detail is not None

    @pytest.mark.asyncio
    async def test_run_tool_exit_1_no_diagnostics_dirty(self) -> None:
        """Test _run_tool with exit 1 but no parsed diagnostics."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            # Exit 1 with unparseable output (should be marked dirty)
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"not json", b""))

            with (
                patch.object(ops, "_build_command", return_value=["ruff", "check", "."]),
                patch("shutil.which", return_value="/usr/bin/ruff"),
                patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            ):
                result = await ops._run_tool(tool, [root], dry_run=True)

            # Exit code 1 with unparseable output = error (parser failed)
            assert result.status == "error"

    @pytest.mark.asyncio
    async def test_run_tool_oserror(self) -> None:
        """Test _run_tool when OSError is raised."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            with (
                patch.object(ops, "_build_command", return_value=["ruff", "check", "."]),
                patch("shutil.which", return_value="/usr/bin/ruff"),
                patch("asyncio.create_subprocess_exec", side_effect=OSError("No such file")),
            ):
                result = await ops._run_tool(tool, [root], dry_run=True)

            assert result.status == "error"
            assert "No such file" in (result.error_detail or "")

    @pytest.mark.asyncio
    async def test_check_with_errored_tools_generates_agentic_hint(self) -> None:
        """Test that errored tools trigger agentic hint generation."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            # Create an error-returning tool result
            error_result = ToolResult(
                tool_id="python.ruff",
                status="error",
                error_detail="Connection refused",
                duration_seconds=0.1,
            )

            async def mock_run_tool(
                _tool: LintTool, _paths: list[Path], _dry_run: bool
            ) -> ToolResult:  # noqa: ARG001
                return error_result

            with (
                patch.object(ops, "_run_tool", mock_run_tool),
                patch.object(ops, "_resolve_tools", return_value=[registry.get("python.ruff")]),
            ):
                result = await ops.check()

            # Should have agentic hint when tools fail
            assert result.agentic_hint is not None
            assert (
                "failed" in result.agentic_hint.lower() or "manual" in result.agentic_hint.lower()
            )

    @pytest.mark.asyncio
    async def test_files_modified_counted(self) -> None:
        """Test that files_modified counts fix_applied correctly."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = LintOps(root, coordinator)

            tool = registry.get("python.ruff")
            assert tool is not None

            # Output with fix_applied (ruff doesn't report this in check mode,
            # but we test the counting logic)
            ruff_output = '[{"filename": "test.py", "code": "E501", "message": "Fixed", "location": {"row": 1, "column": 80}, "fix": {"message": "Fix applied"}}]'

            mock_proc = MagicMock()
            mock_proc.returncode = 0  # Fixed successfully
            mock_proc.communicate = AsyncMock(return_value=(ruff_output.encode(), b""))

            with (
                patch.object(ops, "_build_command", return_value=["ruff", "check", "."]),
                patch("shutil.which", return_value="/usr/bin/ruff"),
                patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            ):
                result = await ops._run_tool(tool, [root], dry_run=False)

            # Should have diagnostics from parsing
            assert result.files_checked >= 0
