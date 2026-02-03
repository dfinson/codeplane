"""Test result parsers.

Parses output from various test runners into a common format.
Supports: JUnit XML, pytest JSON, TAP, Go test JSON.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Literal

# Re-export from models for backward compatibility
from codeplane.testing.models import ParsedTestCase, ParsedTestSuite

__all__ = [
    "ParsedTestCase",
    "ParsedTestSuite",
    "parse_junit_xml",
    "parse_pytest_json",
    "parse_go_test_json",
    "parse_tap",
    "auto_parse",
]


def parse_junit_xml(content: str) -> ParsedTestSuite:
    """Parse JUnit XML format (canonical format)."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return ParsedTestSuite(
            name="parse_error",
            errors=1,
            tests=[
                ParsedTestCase(
                    name="parse_error",
                    classname=None,
                    status="error",
                    duration_seconds=0,
                    message=str(e),
                )
            ],
        )

    suites = list(root) if root.tag == "testsuites" else [root]
    all_tests: list[ParsedTestCase] = []
    total_duration = 0.0

    for suite in suites:
        for testcase in suite.findall(".//testcase"):
            name = testcase.get("name", "unknown")
            classname = testcase.get("classname")
            duration = float(testcase.get("time", "0"))
            total_duration += duration

            # Extract file/line from classname if present
            file_path = testcase.get("file")
            line_str = testcase.get("line")
            line_number = int(line_str) if line_str and line_str.isdigit() else None

            failure = testcase.find("failure")
            error = testcase.find("error")
            skipped = testcase.find("skipped")

            if failure is not None:
                status: Literal["passed", "failed", "skipped", "error"] = "failed"
                message = failure.get("message")
                tb = failure.text
            elif error is not None:
                status = "error"
                message = error.get("message")
                tb = error.text
            elif skipped is not None:
                status = "skipped"
                message = skipped.get("message")
                tb = None
            else:
                status = "passed"
                message = None
                tb = None

            stdout_elem = testcase.find("system-out")
            stderr_elem = testcase.find("system-err")

            all_tests.append(
                ParsedTestCase(
                    name=name,
                    classname=classname,
                    status=status,
                    duration_seconds=duration,
                    message=message,
                    traceback=tb,
                    stdout=stdout_elem.text if stdout_elem is not None else None,
                    stderr=stderr_elem.text if stderr_elem is not None else None,
                    file_path=file_path,
                    line_number=line_number,
                )
            )

    passed = sum(1 for t in all_tests if t.status == "passed")
    failed = sum(1 for t in all_tests if t.status == "failed")
    skipped_count = sum(1 for t in all_tests if t.status == "skipped")
    errors = sum(1 for t in all_tests if t.status == "error")

    return ParsedTestSuite(
        name=suites[0].get("name", "testsuite") if suites else "unknown",
        tests=all_tests,
        total=len(all_tests),
        passed=passed,
        failed=failed,
        skipped=skipped_count,
        errors=errors,
        duration_seconds=total_duration,
    )


def parse_pytest_json(content: str) -> ParsedTestSuite:
    """Parse pytest JSON output (pytest-json-report format)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return ParsedTestSuite(
            name="parse_error",
            errors=1,
            tests=[
                ParsedTestCase(
                    name="parse_error",
                    classname=None,
                    status="error",
                    duration_seconds=0,
                    message=str(e),
                )
            ],
        )

    tests: list[ParsedTestCase] = []

    if "tests" in data:
        for test in data["tests"]:
            nodeid = test.get("nodeid", "unknown")
            outcome = test.get("outcome", "unknown")
            duration = test.get("duration", 0)

            status: Literal["passed", "failed", "skipped", "error"]
            if outcome == "passed":
                status = "passed"
            elif outcome == "failed":
                status = "failed"
            elif outcome == "skipped":
                status = "skipped"
            else:
                status = "error"

            call = test.get("call", {})
            message = None
            tb = None
            if call.get("outcome") == "failed":
                crash = call.get("crash", {})
                message = crash.get("message")
                tb = call.get("longrepr")

            # Extract file path from nodeid
            file_path = nodeid.split("::")[0] if "::" in nodeid else None
            line_number = test.get("lineno")

            tests.append(
                ParsedTestCase(
                    name=nodeid.split("::")[-1] if "::" in nodeid else nodeid,
                    classname="::".join(nodeid.split("::")[:-1]) if "::" in nodeid else None,
                    status=status,
                    duration_seconds=duration,
                    message=message,
                    traceback=tb,
                    file_path=file_path,
                    line_number=line_number,
                )
            )

    summary = data.get("summary", {})

    return ParsedTestSuite(
        name="pytest",
        tests=tests,
        total=summary.get("total", len(tests)),
        passed=summary.get("passed", sum(1 for t in tests if t.status == "passed")),
        failed=summary.get("failed", sum(1 for t in tests if t.status == "failed")),
        skipped=summary.get("skipped", sum(1 for t in tests if t.status == "skipped")),
        errors=summary.get("error", sum(1 for t in tests if t.status == "error")),
        duration_seconds=data.get("duration", sum(t.duration_seconds for t in tests)),
    )


def parse_go_test_json(content: str) -> ParsedTestSuite:
    """Parse Go test JSON output (go test -json)."""
    tests: dict[str, ParsedTestCase] = {}
    total_duration = 0.0

    for line in content.strip().split("\n"):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        action = event.get("Action")
        test_name = event.get("Test")
        pkg = event.get("Package", "")
        elapsed = event.get("Elapsed", 0)
        output = event.get("Output", "")

        if not test_name:
            continue

        key = f"{pkg}:{test_name}"

        if key not in tests:
            tests[key] = ParsedTestCase(
                name=test_name,
                classname=pkg,
                status="passed",
                duration_seconds=0,
                stdout="",
            )

        test = tests[key]

        if action == "output":
            test.stdout = (test.stdout or "") + output
        elif action == "pass":
            test.status = "passed"
            test.duration_seconds = elapsed
            total_duration += elapsed
        elif action == "fail":
            test.status = "failed"
            test.duration_seconds = elapsed
            test.message = "Test failed"
            total_duration += elapsed
        elif action == "skip":
            test.status = "skipped"
            test.duration_seconds = elapsed

    test_list = list(tests.values())

    return ParsedTestSuite(
        name="go test",
        tests=test_list,
        total=len(test_list),
        passed=sum(1 for t in test_list if t.status == "passed"),
        failed=sum(1 for t in test_list if t.status == "failed"),
        skipped=sum(1 for t in test_list if t.status == "skipped"),
        errors=0,
        duration_seconds=total_duration,
    )


def parse_tap(content: str) -> ParsedTestSuite:
    """Parse TAP (Test Anything Protocol) format."""
    tests: list[ParsedTestCase] = []
    pattern = re.compile(r"^(ok|not ok)\s+(\d+)\s*-?\s*(.*)$", re.MULTILINE)

    for match in pattern.finditer(content):
        status_str, num, description = match.groups()

        status: Literal["passed", "failed", "skipped", "error"]
        if status_str == "ok":
            if "# SKIP" in description or "# skip" in description:
                status = "skipped"
                description = description.replace("# SKIP", "").replace("# skip", "").strip()
            else:
                status = "passed"
        else:
            status = "failed"

        tests.append(
            ParsedTestCase(
                name=description.strip() or f"test_{num}",
                classname=None,
                status=status,
                duration_seconds=0,
            )
        )

    return ParsedTestSuite(
        name="tap",
        tests=tests,
        total=len(tests),
        passed=sum(1 for t in tests if t.status == "passed"),
        failed=sum(1 for t in tests if t.status == "failed"),
        skipped=sum(1 for t in tests if t.status == "skipped"),
        errors=0,
        duration_seconds=0,
    )


def auto_parse(content: str, runner: str | None = None) -> ParsedTestSuite:  # noqa: ARG001
    """Auto-detect format and parse.

    Prefers JUnit XML as the canonical format.
    """
    content = content.strip()

    # Prefer JUnit XML
    if content.startswith("<?xml") or content.startswith("<"):
        return parse_junit_xml(content)

    # pytest JSON
    if content.startswith("{"):
        try:
            data = json.loads(content)
            if "tests" in data or "summary" in data:
                return parse_pytest_json(content)
        except json.JSONDecodeError:
            pass

    # Go test JSON (NDJSON)
    if "\n{" in content or content.startswith('{"'):
        lines = content.split("\n")
        if all(line.startswith("{") or not line.strip() for line in lines[:5] if line.strip()):
            return parse_go_test_json(content)

    # TAP
    if re.search(r"^(ok|not ok)\s+\d+", content, re.MULTILINE):
        return parse_tap(content)

    # Fallback: try each parser
    for parser in [parse_junit_xml, parse_pytest_json, parse_go_test_json, parse_tap]:
        try:
            result = parser(content)
            if result.total > 0:
                return result
        except Exception:
            continue

    return ParsedTestSuite(
        name="unknown",
        errors=1,
        tests=[
            ParsedTestCase(
                name="parse_error",
                classname=None,
                status="error",
                duration_seconds=0,
                message="Could not parse test output",
            )
        ],
    )
