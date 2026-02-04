"""Tests for lint/parsers.py module.

Covers:
- _severity_from_str() helper
- parse_ruff()
- parse_mypy()
- parse_eslint()
- parse_tsc()
- Various other parsers
"""

from __future__ import annotations

import json

from codeplane.lint.models import Severity
from codeplane.lint.parsers import (
    _severity_from_str,
    parse_black_check,
    parse_eslint,
    parse_go_vet,
    parse_gofmt,
    parse_mypy,
    parse_prettier_check,
    parse_ruff,
    parse_tsc,
)


class TestSeverityFromStr:
    """Tests for _severity_from_str helper."""

    def test_error_variants(self) -> None:
        """Recognizes error variants."""
        assert _severity_from_str("error") == Severity.ERROR
        assert _severity_from_str("ERROR") == Severity.ERROR
        assert _severity_from_str("e") == Severity.ERROR
        assert _severity_from_str("fatal") == Severity.ERROR

    def test_warning_variants(self) -> None:
        """Recognizes warning variants."""
        assert _severity_from_str("warning") == Severity.WARNING
        assert _severity_from_str("warn") == Severity.WARNING
        assert _severity_from_str("w") == Severity.WARNING

    def test_info_variants(self) -> None:
        """Recognizes info variants."""
        assert _severity_from_str("info") == Severity.INFO
        assert _severity_from_str("information") == Severity.INFO
        assert _severity_from_str("note") == Severity.INFO

    def test_unknown_defaults_to_hint(self) -> None:
        """Unknown strings default to HINT."""
        assert _severity_from_str("unknown") == Severity.HINT
        assert _severity_from_str("") == Severity.HINT


class TestParseRuff:
    """Tests for parse_ruff."""

    def test_empty_output(self) -> None:
        """Empty output returns empty list."""
        result = parse_ruff("", "")
        assert result == []

    def test_empty_json_array(self) -> None:
        """Empty JSON array returns empty list."""
        result = parse_ruff("[]", "")
        assert result == []

    def test_single_diagnostic(self) -> None:
        """Parses single diagnostic."""
        data = [
            {
                "filename": "test.py",
                "location": {"row": 10, "column": 5},
                "end_location": {"row": 10, "column": 15},
                "code": "E501",
                "message": "Line too long",
            }
        ]
        result = parse_ruff(json.dumps(data), "")
        assert len(result) == 1
        assert result[0].path == "test.py"
        assert result[0].line == 10
        assert result[0].code == "E501"
        assert result[0].source == "ruff"

    def test_invalid_json(self) -> None:
        """Invalid JSON returns empty list."""
        result = parse_ruff("not json", "")
        assert result == []


class TestParseMypy:
    """Tests for parse_mypy."""

    def test_empty_output(self) -> None:
        """Empty output returns empty list."""
        result = parse_mypy("", "")
        assert result == []

    def test_single_error(self) -> None:
        """Parses single error."""
        data = {
            "file": "test.py",
            "line": 5,
            "severity": "error",
            "code": "arg-type",
            "message": "Incompatible type",
        }
        result = parse_mypy(json.dumps(data), "")
        assert len(result) == 1
        assert result[0].path == "test.py"
        assert result[0].line == 5
        assert result[0].severity == Severity.ERROR
        assert result[0].source == "mypy"

    def test_multiple_lines(self) -> None:
        """Parses multiple JSON lines."""
        lines = [
            {"file": "a.py", "line": 1, "message": "error1"},
            {"file": "b.py", "line": 2, "message": "error2"},
        ]
        stdout = "\n".join(json.dumps(line) for line in lines)
        result = parse_mypy(stdout, "")
        assert len(result) == 2


class TestParseEslint:
    """Tests for parse_eslint."""

    def test_empty_output(self) -> None:
        """Empty output returns empty list."""
        result = parse_eslint("[]", "")
        assert result == []

    def test_single_file_error(self) -> None:
        """Parses single file with error."""
        data = [
            {
                "filePath": "/src/app.js",
                "messages": [
                    {
                        "line": 10,
                        "column": 5,
                        "severity": 2,  # error
                        "ruleId": "no-unused-vars",
                        "message": "Variable is not used",
                    }
                ],
            }
        ]
        result = parse_eslint(json.dumps(data), "")
        assert len(result) == 1
        assert result[0].path == "/src/app.js"
        assert result[0].severity == Severity.ERROR
        assert result[0].code == "no-unused-vars"

    def test_warning_severity(self) -> None:
        """Severity 1 is WARNING."""
        data = [
            {
                "filePath": "app.js",
                "messages": [{"line": 1, "severity": 1, "message": "warn"}],
            }
        ]
        result = parse_eslint(json.dumps(data), "")
        assert result[0].severity == Severity.WARNING


class TestParseTsc:
    """Tests for parse_tsc."""

    def test_empty_output(self) -> None:
        """Empty output returns empty list."""
        result = parse_tsc("", "")
        assert result == []

    def test_single_error(self) -> None:
        """Parses single TypeScript error."""
        stdout = "src/app.ts(10,5): error TS2345: Argument type mismatch"
        result = parse_tsc(stdout, "")
        assert len(result) == 1
        assert result[0].path == "src/app.ts"
        assert result[0].line == 10
        assert result[0].column == 5
        assert result[0].code == "TS2345"
        assert result[0].severity == Severity.ERROR

    def test_warning(self) -> None:
        """Parses warning."""
        stdout = "src/app.ts(1,1): warning TS6385: Some warning"
        result = parse_tsc(stdout, "")
        assert result[0].severity == Severity.WARNING


class TestParseBlackCheck:
    """Tests for parse_black_check."""

    def test_empty_output(self) -> None:
        """Empty output returns empty list."""
        result = parse_black_check("", "")
        assert result == []

    def test_would_reformat(self) -> None:
        """Parses 'would reformat' messages."""
        stderr = "would reformat src/app.py\nwould reformat tests/test_app.py"
        result = parse_black_check("", stderr)
        assert len(result) == 2
        assert result[0].path == "src/app.py"
        assert result[0].source == "black"


class TestParsePrettierCheck:
    """Tests for parse_prettier_check."""

    def test_empty_output(self) -> None:
        """Empty output returns empty list."""
        result = parse_prettier_check("", "")
        assert result == []

    def test_files_needing_format(self) -> None:
        """Parses files needing formatting."""
        stdout = "Checking formatting...\nsrc/app.js\ntests/app.test.js"
        result = parse_prettier_check(stdout, "")
        assert len(result) == 2
        assert result[0].path == "src/app.js"


class TestParseGoVet:
    """Tests for parse_go_vet."""

    def test_empty_output(self) -> None:
        """Empty output returns empty list."""
        result = parse_go_vet("", "")
        assert result == []

    def test_single_error(self) -> None:
        """Parses single go vet error."""
        stdout = "main.go:10:5: unreachable code"
        result = parse_go_vet(stdout, "")
        assert len(result) == 1
        assert result[0].path == "main.go"
        assert result[0].line == 10
        assert result[0].column == 5
        assert result[0].source == "go vet"


class TestParseGofmt:
    """Tests for parse_gofmt."""

    def test_empty_output(self) -> None:
        """Empty output returns empty list."""
        result = parse_gofmt("", "")
        assert result == []

    def test_files_needing_format(self) -> None:
        """Parses files needing formatting."""
        stdout = "main.go\npkg/util.go"
        result = parse_gofmt(stdout, "")
        assert len(result) == 2
        assert result[0].path == "main.go"
        assert result[0].source == "gofmt"
