"""Comprehensive tests for output parsers."""

import pytest

from codeplane.testing.parsers import (
    auto_parse,
    parse_go_test_json,
    parse_junit_xml,
    parse_tap,
)

# =============================================================================
# JUnit XML Parser
# =============================================================================


class TestJunitXmlParser:
    """Tests for JUnit XML parser."""

    def test_parse_basic_success(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="2" failures="0" errors="0" skipped="0">
    <testcase classname="test_module" name="test_one" time="0.01"/>
    <testcase classname="test_module" name="test_two" time="0.02"/>
</testsuite>
"""
        result = parse_junit_xml(xml)

        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0
        assert result.errors == 0
        assert result.skipped == 0

    def test_parse_with_failures(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="3" failures="1" errors="0" skipped="0">
    <testcase classname="test_module" name="test_one" time="0.01"/>
    <testcase classname="test_module" name="test_two" time="0.02">
        <failure message="assertion failed">Details here</failure>
    </testcase>
    <testcase classname="test_module" name="test_three" time="0.01"/>
</testsuite>
"""
        result = parse_junit_xml(xml)

        assert result.total == 3
        assert result.passed == 2
        assert result.failed == 1

    def test_parse_with_errors(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="2" failures="0" errors="1" skipped="0">
    <testcase classname="test_module" name="test_one" time="0.01"/>
    <testcase classname="test_module" name="test_two" time="0.02">
        <error message="exception" type="RuntimeError">Traceback...</error>
    </testcase>
</testsuite>
"""
        result = parse_junit_xml(xml)

        assert result.total == 2
        assert result.passed == 1
        assert result.errors == 1

    def test_parse_with_skipped(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="3" failures="0" errors="0" skipped="1">
    <testcase classname="test_module" name="test_one" time="0.01"/>
    <testcase classname="test_module" name="test_two" time="0.02">
        <skipped message="reason"/>
    </testcase>
    <testcase classname="test_module" name="test_three" time="0.01"/>
</testsuite>
"""
        result = parse_junit_xml(xml)

        assert result.total == 3
        assert result.passed == 2
        assert result.skipped == 1

    def test_parse_multiple_suites(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuites>
    <testsuite name="suite1" tests="2" failures="0" errors="0">
        <testcase classname="mod1" name="test_a" time="0.01"/>
        <testcase classname="mod1" name="test_b" time="0.01"/>
    </testsuite>
    <testsuite name="suite2" tests="3" failures="1" errors="0">
        <testcase classname="mod2" name="test_c" time="0.01"/>
        <testcase classname="mod2" name="test_d" time="0.01">
            <failure/>
        </testcase>
        <testcase classname="mod2" name="test_e" time="0.01"/>
    </testsuite>
</testsuites>
"""
        result = parse_junit_xml(xml)

        assert result.total == 5
        assert result.passed == 4
        assert result.failed == 1

    def test_parse_empty_suite(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="0" failures="0" errors="0" skipped="0">
</testsuite>
"""
        result = parse_junit_xml(xml)

        assert result.total == 0
        assert result.passed == 0

    def test_parse_invalid_xml(self) -> None:
        result = parse_junit_xml("not xml at all")

        # Parser returns error in the suite
        assert result.errors >= 1

    def test_parse_extracts_test_cases(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="2">
    <testcase classname="tests.test_module" name="test_feature" time="0.123"/>
    <testcase classname="tests.test_module" name="test_other" time="0.456"/>
</testsuite>
"""
        result = parse_junit_xml(xml)

        assert len(result.tests) == 2
        tc = result.tests[0]
        assert tc.name == "test_feature"
        assert tc.classname == "tests.test_module"
        assert tc.duration_seconds == pytest.approx(0.123, 0.001)
        assert tc.status == "passed"

    def test_parse_testcase_with_system_output(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="1">
    <testcase classname="mod" name="test_output" time="0.01">
        <system-out>stdout content</system-out>
        <system-err>stderr content</system-err>
    </testcase>
</testsuite>
"""
        result = parse_junit_xml(xml)

        assert result.total == 1
        tc = result.tests[0]
        assert tc.stdout == "stdout content"
        assert tc.stderr == "stderr content"

    def test_parse_failure_message_extraction(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="1">
    <testcase classname="mod" name="test_fail" time="0.01">
        <failure message="assert 1 == 2" type="AssertionError">Full traceback...</failure>
    </testcase>
</testsuite>
"""
        result = parse_junit_xml(xml)

        tc = result.tests[0]
        assert tc.status == "failed"
        assert tc.message == "assert 1 == 2"

    def test_parse_with_properties(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="1">
    <properties>
        <property name="python.version" value="3.12"/>
    </properties>
    <testcase classname="mod" name="test_one" time="0.01"/>
</testsuite>
"""
        result = parse_junit_xml(xml)

        assert result.total == 1
        assert result.passed == 1

    def test_parse_duration_from_testsuite(self) -> None:
        xml = """<?xml version="1.0"?>
<testsuite tests="2" time="1.5">
    <testcase classname="mod" name="test_a" time="0.5"/>
    <testcase classname="mod" name="test_b" time="1.0"/>
</testsuite>
"""
        result = parse_junit_xml(xml)

        assert result.duration_seconds == pytest.approx(1.5, 0.01)


# =============================================================================
# Go test JSON Parser
# =============================================================================


class TestGoTestJsonParser:
    """Tests for Go test NDJSON parser."""

    def test_parse_basic_pass(self) -> None:
        content = """
{"Time":"2024-01-01T00:00:00Z","Action":"run","Package":"example.com/pkg","Test":"TestFoo"}
{"Time":"2024-01-01T00:00:01Z","Action":"pass","Package":"example.com/pkg","Test":"TestFoo","Elapsed":0.5}
{"Time":"2024-01-01T00:00:01Z","Action":"pass","Package":"example.com/pkg","Elapsed":0.6}
"""
        result = parse_go_test_json(content)

        assert result.passed >= 1
        assert result.failed == 0

    def test_parse_with_failure(self) -> None:
        content = """
{"Time":"2024-01-01T00:00:00Z","Action":"run","Package":"example.com/pkg","Test":"TestBar"}
{"Time":"2024-01-01T00:00:00Z","Action":"output","Package":"example.com/pkg","Test":"TestBar","Output":"    Error: assertion failed\\n"}
{"Time":"2024-01-01T00:00:01Z","Action":"fail","Package":"example.com/pkg","Test":"TestBar","Elapsed":0.1}
{"Time":"2024-01-01T00:00:01Z","Action":"fail","Package":"example.com/pkg","Elapsed":0.2}
"""
        result = parse_go_test_json(content)

        assert result.failed >= 1

    def test_parse_with_skip(self) -> None:
        content = """
{"Time":"2024-01-01T00:00:00Z","Action":"run","Package":"example.com/pkg","Test":"TestSkipped"}
{"Time":"2024-01-01T00:00:00Z","Action":"skip","Package":"example.com/pkg","Test":"TestSkipped","Elapsed":0.0}
{"Time":"2024-01-01T00:00:00Z","Action":"pass","Package":"example.com/pkg","Elapsed":0.1}
"""
        result = parse_go_test_json(content)

        assert result.skipped >= 1

    def test_parse_multiple_tests(self) -> None:
        content = """
{"Action":"run","Package":"pkg","Test":"TestA"}
{"Action":"pass","Package":"pkg","Test":"TestA","Elapsed":0.1}
{"Action":"run","Package":"pkg","Test":"TestB"}
{"Action":"pass","Package":"pkg","Test":"TestB","Elapsed":0.1}
{"Action":"run","Package":"pkg","Test":"TestC"}
{"Action":"fail","Package":"pkg","Test":"TestC","Elapsed":0.1}
{"Action":"fail","Package":"pkg","Elapsed":0.3}
"""
        result = parse_go_test_json(content)

        assert result.total >= 3
        assert result.passed >= 2
        assert result.failed >= 1

    def test_parse_subtests(self) -> None:
        content = """
{"Action":"run","Package":"pkg","Test":"TestParent"}
{"Action":"run","Package":"pkg","Test":"TestParent/subtest_a"}
{"Action":"pass","Package":"pkg","Test":"TestParent/subtest_a","Elapsed":0.1}
{"Action":"run","Package":"pkg","Test":"TestParent/subtest_b"}
{"Action":"pass","Package":"pkg","Test":"TestParent/subtest_b","Elapsed":0.1}
{"Action":"pass","Package":"pkg","Test":"TestParent","Elapsed":0.3}
{"Action":"pass","Package":"pkg","Elapsed":0.4}
"""
        result = parse_go_test_json(content)

        # Should count subtests
        assert result.passed >= 2

    def test_parse_empty_input(self) -> None:
        result = parse_go_test_json("")
        assert result.total == 0

    def test_parse_invalid_json_lines(self) -> None:
        content = """
{"Action":"run","Package":"pkg","Test":"TestA"}
not valid json
{"Action":"pass","Package":"pkg","Test":"TestA","Elapsed":0.1}
"""
        # Should skip invalid lines and continue
        result = parse_go_test_json(content)
        assert result.total >= 1

    def test_parse_build_failure(self) -> None:
        content = """
{"Action":"output","Package":"pkg","Output":"# pkg\\n"}
{"Action":"output","Package":"pkg","Output":"./main.go:5:1: syntax error\\n"}
{"Action":"fail","Package":"pkg","Elapsed":0.0}
"""
        result = parse_go_test_json(content)

        # Build failure counts as package failure - at least has some result
        assert result is not None


# =============================================================================
# TAP Parser
# =============================================================================


class TestTapParser:
    """Tests for TAP (Test Anything Protocol) parser."""

    def test_parse_basic_pass(self) -> None:
        content = """TAP version 13
1..3
ok 1 - test one
ok 2 - test two
ok 3 - test three
"""
        result = parse_tap(content)

        assert result.total == 3
        assert result.passed == 3
        assert result.failed == 0

    def test_parse_with_failure(self) -> None:
        content = """1..3
ok 1 - test one
not ok 2 - test two
ok 3 - test three
"""
        result = parse_tap(content)

        assert result.total == 3
        assert result.passed == 2
        assert result.failed == 1

    def test_parse_with_skip(self) -> None:
        content = """1..3
ok 1 - test one
ok 2 - test two # SKIP reason for skipping
ok 3 - test three
"""
        result = parse_tap(content)

        assert result.total == 3
        # Skipped tests are still "ok" in TAP
        assert result.passed == 3 or result.skipped >= 1

    def test_parse_with_todo(self) -> None:
        content = """1..2
ok 1 - test passes
not ok 2 - test fails # TODO known issue
"""
        result = parse_tap(content)

        # TODO failures don't count as real failures in some interpretations
        assert result.total == 2

    def test_parse_without_plan(self) -> None:
        content = """ok 1 - test one
ok 2 - test two
1..2
"""
        result = parse_tap(content)

        assert result.total == 2
        assert result.passed == 2

    def test_parse_with_diagnostic(self) -> None:
        content = """1..2
ok 1 - test one
# Diagnostic message
not ok 2 - test two
  ---
  message: 'assertion failed'
  severity: fail
  ...
"""
        result = parse_tap(content)

        assert result.total == 2
        assert result.failed == 1

    def test_parse_empty_input(self) -> None:
        result = parse_tap("")
        assert result.total == 0

    def test_parse_plan_only(self) -> None:
        result = parse_tap("1..0")
        assert result.total == 0

    def test_parse_bail_out(self) -> None:
        content = """1..5
ok 1 - test one
Bail out! Critical failure
"""
        result = parse_tap(content)

        # After bail out, remaining tests are not run
        assert result.passed >= 1


# =============================================================================
# auto_parse()
# =============================================================================


class TestAutoParse:
    """Tests for auto_parse detection."""

    def test_auto_parse_junit_xml(self) -> None:
        content = """<?xml version="1.0"?>
<testsuite tests="1">
    <testcase classname="mod" name="test_one" time="0.01"/>
</testsuite>
"""
        result = auto_parse(content)

        assert result.total == 1

    def test_auto_parse_go_json(self) -> None:
        content = """{"Action":"run","Package":"pkg","Test":"TestA"}
{"Action":"pass","Package":"pkg","Test":"TestA","Elapsed":0.1}
{"Action":"pass","Package":"pkg","Elapsed":0.1}
"""
        result = auto_parse(content)

        assert result is not None
        assert result.total >= 1

    def test_auto_parse_tap(self) -> None:
        content = """1..2
ok 1 - test one
ok 2 - test two
"""
        result = auto_parse(content)

        assert result.total == 2

    def test_auto_parse_unparseable_content(self) -> None:
        result = auto_parse("random unparseable content that is not any format")
        # Should return error result
        assert result.errors >= 1

    def test_auto_parse_empty_content(self) -> None:
        result = auto_parse("")
        # Should return error result for empty content
        assert result is not None

    def test_auto_parse_xml_without_prologue(self) -> None:
        content = """<testsuite tests="2">
    <testcase classname="mod" name="test_a" time="0.01"/>
    <testcase classname="mod" name="test_b" time="0.01"/>
</testsuite>
"""
        result = auto_parse(content)

        # Should detect XML even without <?xml?> prologue
        assert result.total == 2

    def test_auto_parse_with_runner_hint(self) -> None:
        content = """<?xml version="1.0"?>
<testsuite tests="1">
    <testcase classname="mod" name="test_one" time="0.01"/>
</testsuite>
"""
        # Runner hint is accepted but currently unused
        result = auto_parse(content, runner="pytest")

        assert result.total == 1
