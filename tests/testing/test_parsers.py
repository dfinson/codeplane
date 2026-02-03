"""Tests for test result parsers."""

from pathlib import Path

from codeplane.testing.parsers import (
    auto_parse,
    parse_go_test_json,
    parse_junit_xml,
    parse_tap,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestJUnitXMLParser:
    """Tests for JUnit XML parsing."""

    def test_given_basic_junit_when_parse_then_correct_counts(self) -> None:
        content = (FIXTURES_DIR / "junit_basic.xml").read_text()
        result = parse_junit_xml(content)

        assert result.total == 5
        assert result.passed == 3
        assert result.failed == 1
        assert result.skipped == 1
        assert result.errors == 0

    def test_given_basic_junit_when_parse_then_test_details_correct(self) -> None:
        content = (FIXTURES_DIR / "junit_basic.xml").read_text()
        result = parse_junit_xml(content)

        test_names = [t.name for t in result.tests]
        assert "test_passing" in test_names
        assert "test_failing" in test_names
        assert "test_skipped" in test_names

        failing = next(t for t in result.tests if t.name == "test_failing")
        assert failing.status == "failed"
        assert "assert 1 == 2" in (failing.message or "")
        assert failing.traceback is not None

    def test_given_junit_with_output_when_parse_then_stdout_stderr_captured(self) -> None:
        content = (FIXTURES_DIR / "junit_basic.xml").read_text()
        result = parse_junit_xml(content)

        with_output = next(t for t in result.tests if t.name == "test_with_output")
        assert with_output.stdout == "some stdout output"
        assert with_output.stderr == "some stderr output"

    def test_given_multiple_suites_when_parse_then_all_tests_collected(self) -> None:
        content = (FIXTURES_DIR / "junit_multiple_suites.xml").read_text()
        result = parse_junit_xml(content)

        assert result.total == 8
        assert result.passed == 4
        assert result.failed == 2
        assert result.skipped == 1
        assert result.errors == 1

    def test_given_invalid_xml_when_parse_then_error_result(self) -> None:
        result = parse_junit_xml("not xml at all")

        assert result.errors == 1
        assert len(result.tests) == 1
        assert result.tests[0].status == "error"


class TestGoTestJSONParser:
    """Tests for Go test JSON parsing."""

    def test_given_go_json_when_parse_then_correct_counts(self) -> None:
        content = (FIXTURES_DIR / "go_test_json.txt").read_text()
        result = parse_go_test_json(content)

        assert result.total == 3
        assert result.passed == 1
        assert result.failed == 1
        assert result.skipped == 1

    def test_given_go_json_when_parse_then_test_names_correct(self) -> None:
        content = (FIXTURES_DIR / "go_test_json.txt").read_text()
        result = parse_go_test_json(content)

        test_names = [t.name for t in result.tests]
        assert "TestExample" in test_names
        assert "TestFailing" in test_names
        assert "TestSkipped" in test_names

    def test_given_go_json_when_parse_then_output_captured(self) -> None:
        content = (FIXTURES_DIR / "go_test_json.txt").read_text()
        result = parse_go_test_json(content)

        failing = next(t for t in result.tests if t.name == "TestFailing")
        assert failing.stdout is not None
        assert "Expected 1, got 2" in failing.stdout


class TestTAPParser:
    """Tests for TAP format parsing."""

    def test_given_tap_when_parse_then_correct_counts(self) -> None:
        content = (FIXTURES_DIR / "tap_basic.txt").read_text()
        result = parse_tap(content)

        assert result.total == 5
        assert result.passed == 2
        assert result.failed == 2
        assert result.skipped == 1

    def test_given_tap_when_parse_then_test_descriptions_preserved(self) -> None:
        content = (FIXTURES_DIR / "tap_basic.txt").read_text()
        result = parse_tap(content)

        test_names = [t.name for t in result.tests]
        assert "test passes" in test_names
        assert "this test fails" in test_names


class TestAutoParser:
    """Tests for auto-detection parsing."""

    def test_given_xml_content_when_auto_parse_then_uses_junit(self) -> None:
        content = (FIXTURES_DIR / "junit_basic.xml").read_text()
        result = auto_parse(content)

        assert result.total == 5
        assert result.name == "pytest"

    def test_given_go_json_when_auto_parse_then_uses_go_parser(self) -> None:
        content = (FIXTURES_DIR / "go_test_json.txt").read_text()
        result = auto_parse(content)

        assert result.total == 3
        assert result.name == "go test"

    def test_given_tap_when_auto_parse_then_uses_tap_parser(self) -> None:
        content = (FIXTURES_DIR / "tap_basic.txt").read_text()
        result = auto_parse(content)

        assert result.total == 5
        assert result.name == "tap"

    def test_given_unknown_format_when_auto_parse_then_returns_error(self) -> None:
        result = auto_parse("random text that is not a test format")

        assert result.errors == 1
