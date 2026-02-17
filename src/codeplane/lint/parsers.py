"""Output parsers for lint tools."""

from __future__ import annotations

import json
import re

from codeplane.lint.models import Diagnostic, ParseResult, Severity


def _severity_from_str(s: str) -> Severity:
    """Convert string to Severity."""
    s = s.lower()
    if s in ("error", "e", "fatal"):
        return Severity.ERROR
    if s in ("warning", "warn", "w"):
        return Severity.WARNING
    if s in ("info", "information", "i", "note"):
        return Severity.INFO
    return Severity.HINT


# =============================================================================
# Generic Parsers
# =============================================================================


def parse_sarif(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse SARIF format (Static Analysis Results Interchange Format)."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for run in data.get("runs", []):
            tool_name = run.get("tool", {}).get("driver", {}).get("name", "unknown")
            run.get("tool", {}).get("driver", {}).get("rules", [])  # available for future use
            for result in run.get("results", []):
                rule_id = result.get("ruleId", "")
                level = result.get("level", "warning")
                message = result.get("message", {}).get("text", "")

                for location in result.get("locations", []):
                    phys = location.get("physicalLocation", {})
                    artifact = phys.get("artifactLocation", {})
                    region = phys.get("region", {})

                    diagnostics.append(
                        Diagnostic(
                            path=artifact.get("uri", ""),
                            line=region.get("startLine", 1),
                            column=region.get("startColumn"),
                            end_line=region.get("endLine"),
                            end_column=region.get("endColumn"),
                            severity=_severity_from_str(level),
                            code=rule_id,
                            message=message,
                            source=tool_name,
                        )
                    )
    except (json.JSONDecodeError, KeyError) as e:
        return ParseResult.error(f"SARIF parse error: {e}")
    return ParseResult.ok(diagnostics)


# =============================================================================
# Python Parsers
# =============================================================================


def parse_ruff(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse ruff JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for item in data:
            diagnostics.append(
                Diagnostic(
                    path=item.get("filename", ""),
                    line=item.get("location", {}).get("row", 1),
                    column=item.get("location", {}).get("column"),
                    end_line=item.get("end_location", {}).get("row"),
                    end_column=item.get("end_location", {}).get("column"),
                    severity=Severity.WARNING,
                    code=item.get("code"),
                    message=item.get("message", ""),
                    source="ruff",
                    fix_applied=item.get("fix") is not None
                    and item.get("fix", {}).get("applicability") == "safe",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Ruff JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_mypy(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse mypy JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        for line in stdout.strip().split("\n"):
            if not line.strip():
                continue
            data = json.loads(line)
            diagnostics.append(
                Diagnostic(
                    path=data.get("file", ""),
                    line=data.get("line", 1),
                    column=data.get("column"),
                    severity=_severity_from_str(data.get("severity", "error")),
                    code=data.get("code"),
                    message=data.get("message", ""),
                    source="mypy",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Mypy JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_pyright(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse pyright JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for diag in data.get("generalDiagnostics", []):
            rng = diag.get("range", {})
            start = rng.get("start", {})
            end = rng.get("end", {})
            diagnostics.append(
                Diagnostic(
                    path=diag.get("file", ""),
                    line=start.get("line", 0) + 1,  # 0-indexed
                    column=start.get("character", 0) + 1,
                    end_line=end.get("line", 0) + 1 if end else None,
                    end_column=end.get("character", 0) + 1 if end else None,
                    severity=_severity_from_str(diag.get("severity", "error")),
                    code=diag.get("rule"),
                    message=diag.get("message", ""),
                    source="pyright",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Pyright JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_bandit(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse bandit JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for result in data.get("results", []):
            sev = result.get("issue_severity", "MEDIUM")
            diagnostics.append(
                Diagnostic(
                    path=result.get("filename", ""),
                    line=result.get("line_number", 1),
                    severity=Severity.ERROR if sev == "HIGH" else Severity.WARNING,
                    code=result.get("test_id"),
                    message=f"{result.get('issue_text', '')} (confidence: {result.get('issue_confidence', 'MEDIUM')})",
                    source="bandit",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Bandit JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_black_check(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse black --check output."""
    diagnostics: list[Diagnostic] = []
    # Black writes to stderr: "would reformat path/to/file.py"
    for line in stderr.strip().split("\n"):
        if line.startswith("would reformat "):
            path = line[len("would reformat ") :]
            diagnostics.append(
                Diagnostic(
                    path=path,
                    line=1,
                    severity=Severity.WARNING,
                    message="File would be reformatted",
                    source="black",
                )
            )
    return ParseResult.ok(diagnostics)


# =============================================================================
# JavaScript/TypeScript Parsers
# =============================================================================


def parse_eslint(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse eslint JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for file_result in data:
            filepath = file_result.get("filePath", "")
            for msg in file_result.get("messages", []):
                diagnostics.append(
                    Diagnostic(
                        path=filepath,
                        line=msg.get("line", 1),
                        column=msg.get("column"),
                        end_line=msg.get("endLine"),
                        end_column=msg.get("endColumn"),
                        severity=Severity.ERROR if msg.get("severity") == 2 else Severity.WARNING,
                        code=msg.get("ruleId"),
                        message=msg.get("message", ""),
                        source="eslint",
                        fix_applied=msg.get("fix") is not None,
                    )
                )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"ESLint JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_tsc(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse tsc output (line-based format)."""
    diagnostics: list[Diagnostic] = []
    # Format: file(line,col): error TSxxxx: message
    pattern = re.compile(r"^(.+?)\((\d+),(\d+)\): (error|warning) (TS\d+): (.+)$")
    for line in stdout.strip().split("\n"):
        match = pattern.match(line)
        if match:
            diagnostics.append(
                Diagnostic(
                    path=match.group(1),
                    line=int(match.group(2)),
                    column=int(match.group(3)),
                    severity=_severity_from_str(match.group(4)),
                    code=match.group(5),
                    message=match.group(6),
                    source="tsc",
                )
            )
    return ParseResult.ok(diagnostics)


def parse_prettier_check(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse prettier --check output."""
    diagnostics: list[Diagnostic] = []
    # Prettier lists files that need formatting, one per line
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("Checking") and not line.startswith("All "):
            diagnostics.append(
                Diagnostic(
                    path=line,
                    line=1,
                    severity=Severity.WARNING,
                    message="File needs formatting",
                    source="prettier",
                )
            )
    return ParseResult.ok(diagnostics)


def parse_biome(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse biome JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for diag in data.get("diagnostics", []):
            location = diag.get("location", {})
            span = location.get("span", [0, 0])
            diagnostics.append(
                Diagnostic(
                    path=location.get("path", {}).get("file", ""),
                    line=diag.get("line", 1),
                    column=span[0] if span else None,
                    severity=_severity_from_str(diag.get("severity", "error")),
                    code=diag.get("category"),
                    message=diag.get("message", {}).get("text", "")
                    if isinstance(diag.get("message"), dict)
                    else str(diag.get("message", "")),
                    source="biome",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Biome JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


# =============================================================================
# Go Parsers
# =============================================================================


def parse_go_vet(stdout: str, stderr: str) -> ParseResult:
    """Parse go vet output."""
    diagnostics: list[Diagnostic] = []
    # Format: file:line:col: message
    pattern = re.compile(r"^(.+?):(\d+):(\d+): (.+)$")
    for line in (stdout + stderr).strip().split("\n"):
        match = pattern.match(line)
        if match:
            diagnostics.append(
                Diagnostic(
                    path=match.group(1),
                    line=int(match.group(2)),
                    column=int(match.group(3)),
                    severity=Severity.WARNING,
                    message=match.group(4),
                    source="go vet",
                )
            )
    return ParseResult.ok(diagnostics)


def parse_staticcheck(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse staticcheck JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        for line in stdout.strip().split("\n"):
            if not line.strip():
                continue
            data = json.loads(line)
            diagnostics.append(
                Diagnostic(
                    path=data.get("location", {}).get("file", ""),
                    line=data.get("location", {}).get("line", 1),
                    column=data.get("location", {}).get("column"),
                    severity=_severity_from_str(data.get("severity", "warning")),
                    code=data.get("code"),
                    message=data.get("message", ""),
                    source="staticcheck",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Staticcheck JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_golangci_lint(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse golangci-lint JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for issue in data.get("Issues", []) or []:
            pos = issue.get("Pos", {})
            diagnostics.append(
                Diagnostic(
                    path=pos.get("Filename", ""),
                    line=pos.get("Line", 1),
                    column=pos.get("Column"),
                    severity=_severity_from_str(issue.get("Severity", "warning")),
                    code=issue.get("FromLinter"),
                    message=issue.get("Text", ""),
                    source="golangci-lint",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"golangci-lint JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_gofmt(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse gofmt -l output (list of files needing formatting)."""
    diagnostics: list[Diagnostic] = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if line:
            diagnostics.append(
                Diagnostic(
                    path=line,
                    line=1,
                    severity=Severity.WARNING,
                    message="File needs formatting",
                    source="gofmt",
                )
            )
    return ParseResult.ok(diagnostics)


def parse_ruff_format(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse ruff format output.

    Fix mode (``ruff format``) prints a summary line like
    ``1 file reformatted, 2 files left unchanged.`` -- no per-file paths.

    Check mode (``ruff format --check --diff``) prints
    ``Would reformat: <path>`` lines plus a summary.
    """
    diagnostics: list[Diagnostic] = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Check mode: "Would reformat: src/foo.py"
        m = re.match(r"Would reformat:\s*(.+)", line)
        if m:
            diagnostics.append(
                Diagnostic(
                    path=m.group(1).strip(),
                    line=1,
                    severity=Severity.WARNING,
                    message="File needs formatting",
                    source="ruff-format",
                )
            )
            continue
        # Fix mode summary: "1 file reformatted, 2 files left unchanged."
        # or diff output lines (--- / +++ / @@ / context) -- skip all of these
    return ParseResult.ok(diagnostics)


# =============================================================================
# Rust Parsers
# =============================================================================


def parse_clippy(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse clippy JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        for line in stdout.strip().split("\n"):
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("reason") != "compiler-message":
                continue
            msg = data.get("message", {})
            spans = msg.get("spans", [])
            primary_span = next(
                (s for s in spans if s.get("is_primary")), spans[0] if spans else {}
            )
            diagnostics.append(
                Diagnostic(
                    path=primary_span.get("file_name", ""),
                    line=primary_span.get("line_start", 1),
                    column=primary_span.get("column_start"),
                    end_line=primary_span.get("line_end"),
                    end_column=primary_span.get("column_end"),
                    severity=_severity_from_str(msg.get("level", "warning")),
                    code=msg.get("code", {}).get("code") if msg.get("code") else None,
                    message=msg.get("message", ""),
                    source="clippy",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"Clippy JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_rustfmt_check(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse rustfmt --check output."""
    diagnostics: list[Diagnostic] = []
    # rustfmt --check outputs diff-style output with file paths
    current_file: str | None = None
    for line in stdout.strip().split("\n"):
        if line.startswith("Diff in "):
            current_file = line[len("Diff in ") :].rstrip(":")
            diagnostics.append(
                Diagnostic(
                    path=current_file,
                    line=1,
                    severity=Severity.WARNING,
                    message="File needs formatting",
                    source="rustfmt",
                )
            )
    return ParseResult.ok(diagnostics)


def parse_cargo_audit(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse cargo audit JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for vuln in data.get("vulnerabilities", {}).get("list", []):
            advisory = vuln.get("advisory", {})
            diagnostics.append(
                Diagnostic(
                    path="Cargo.toml",
                    line=1,
                    severity=Severity.ERROR,
                    code=advisory.get("id"),
                    message=f"{advisory.get('title', 'Vulnerability')}: {advisory.get('description', '')[:100]}",
                    source="cargo-audit",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"cargo-audit JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


# =============================================================================
# Java/Kotlin Parsers
# =============================================================================


def parse_checkstyle(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse checkstyle XML output."""
    import xml.etree.ElementTree as ET

    diagnostics: list[Diagnostic] = []
    try:
        root = ET.fromstring(stdout)
        for file_elem in root.findall(".//file"):
            filepath = file_elem.get("name", "")
            for error in file_elem.findall("error"):
                diagnostics.append(
                    Diagnostic(
                        path=filepath,
                        line=int(error.get("line", 1)),
                        column=int(error.get("column", 1)) if error.get("column") else None,
                        severity=_severity_from_str(error.get("severity", "warning")),
                        code=error.get("source", "").split(".")[-1]
                        if error.get("source")
                        else None,
                        message=error.get("message", ""),
                        source="checkstyle",
                    )
                )
    except ET.ParseError as e:
        return ParseResult.error(f"Checkstyle XML parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_ktlint(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse ktlint JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for file_result in data:
            filepath = file_result.get("file", "")
            for error in file_result.get("errors", []):
                diagnostics.append(
                    Diagnostic(
                        path=filepath,
                        line=error.get("line", 1),
                        column=error.get("column"),
                        severity=Severity.WARNING,
                        code=error.get("rule"),
                        message=error.get("message", ""),
                        source="ktlint",
                    )
                )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"ktlint JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


# =============================================================================
# C#/.NET Parsers
# =============================================================================


def parse_dotnet_format(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse dotnet format output."""
    diagnostics: list[Diagnostic] = []
    # dotnet format outputs file paths that would be formatted
    for line in (stdout + stderr).strip().split("\n"):
        line = line.strip()
        if line.endswith(".cs") or line.endswith(".vb"):
            diagnostics.append(
                Diagnostic(
                    path=line,
                    line=1,
                    severity=Severity.WARNING,
                    message="File needs formatting",
                    source="dotnet-format",
                )
            )
    return ParseResult.ok(diagnostics)


# =============================================================================
# Ruby Parsers
# =============================================================================


def parse_rubocop(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse rubocop JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for file_result in data.get("files", []):
            filepath = file_result.get("path", "")
            for offense in file_result.get("offenses", []):
                loc = offense.get("location", {})
                diagnostics.append(
                    Diagnostic(
                        path=filepath,
                        line=loc.get("start_line", 1),
                        column=loc.get("start_column"),
                        end_line=loc.get("last_line"),
                        end_column=loc.get("last_column"),
                        severity=_severity_from_str(offense.get("severity", "warning")),
                        code=offense.get("cop_name"),
                        message=offense.get("message", ""),
                        source="rubocop",
                        fix_applied=offense.get("corrected", False),
                    )
                )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"rubocop JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


# =============================================================================
# PHP Parsers
# =============================================================================


def parse_phpcs(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse phpcs JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for filepath, file_data in data.get("files", {}).items():
            for msg in file_data.get("messages", []):
                diagnostics.append(
                    Diagnostic(
                        path=filepath,
                        line=msg.get("line", 1),
                        column=msg.get("column"),
                        severity=_severity_from_str(msg.get("type", "WARNING")),
                        code=msg.get("source"),
                        message=msg.get("message", ""),
                        source="phpcs",
                        fix_applied=msg.get("fixable", False),
                    )
                )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"phpcs JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_phpstan(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse phpstan JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for filepath, errors in data.get("files", {}).items():
            for error in errors.get("messages", []):
                diagnostics.append(
                    Diagnostic(
                        path=filepath,
                        line=error.get("line", 1),
                        severity=Severity.ERROR,
                        message=error.get("message", ""),
                        source="phpstan",
                    )
                )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"phpstan JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


# =============================================================================
# Shell Parsers
# =============================================================================


def parse_shellcheck(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse shellcheck JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for item in data:
            level = item.get("level", "warning")
            diagnostics.append(
                Diagnostic(
                    path=item.get("file", ""),
                    line=item.get("line", 1),
                    column=item.get("column"),
                    end_line=item.get("endLine"),
                    end_column=item.get("endColumn"),
                    severity=_severity_from_str(level),
                    code=f"SC{item.get('code', '')}",
                    message=item.get("message", ""),
                    source="shellcheck",
                    fix_applied=item.get("fix") is not None,
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"shellcheck JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_shfmt(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse shfmt -l output."""
    diagnostics: list[Diagnostic] = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if line:
            diagnostics.append(
                Diagnostic(
                    path=line,
                    line=1,
                    severity=Severity.WARNING,
                    message="File needs formatting",
                    source="shfmt",
                )
            )
    return ParseResult.ok(diagnostics)


# =============================================================================
# Misc Parsers
# =============================================================================


def parse_hadolint(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse hadolint JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for item in data:
            diagnostics.append(
                Diagnostic(
                    path=item.get("file", "Dockerfile"),
                    line=item.get("line", 1),
                    column=item.get("column"),
                    severity=_severity_from_str(item.get("level", "warning")),
                    code=item.get("code"),
                    message=item.get("message", ""),
                    source="hadolint",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"hadolint JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_yamllint(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse yamllint parsable output."""
    diagnostics: list[Diagnostic] = []
    # Format: file:line:col: [level] message (rule)
    pattern = re.compile(r"^(.+?):(\d+):(\d+): \[(\w+)\] (.+)$")
    for line in stdout.strip().split("\n"):
        match = pattern.match(line)
        if match:
            diagnostics.append(
                Diagnostic(
                    path=match.group(1),
                    line=int(match.group(2)),
                    column=int(match.group(3)),
                    severity=_severity_from_str(match.group(4)),
                    message=match.group(5),
                    source="yamllint",
                )
            )
    return ParseResult.ok(diagnostics)


def parse_markdownlint(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse markdownlint JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for item in data:
            diagnostics.append(
                Diagnostic(
                    path=item.get("fileName", ""),
                    line=item.get("lineNumber", 1),
                    severity=Severity.WARNING,
                    code=item.get("ruleNames", [""])[0] if item.get("ruleNames") else None,
                    message=item.get("ruleDescription", ""),
                    source="markdownlint",
                )
            )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"markdownlint JSON parse error: {e}")
    return ParseResult.ok(diagnostics)


def parse_sqlfluff(stdout: str, stderr: str) -> ParseResult:  # noqa: ARG001
    """Parse sqlfluff JSON output."""
    diagnostics: list[Diagnostic] = []
    try:
        data = json.loads(stdout)
        for file_result in data:
            filepath = file_result.get("filepath", "")
            for violation in file_result.get("violations", []):
                diagnostics.append(
                    Diagnostic(
                        path=filepath,
                        line=violation.get("start_line_no", 1),
                        column=violation.get("start_line_pos"),
                        end_line=violation.get("end_line_no"),
                        end_column=violation.get("end_line_pos"),
                        severity=Severity.WARNING,
                        code=violation.get("code"),
                        message=violation.get("description", ""),
                        source="sqlfluff",
                    )
                )
    except json.JSONDecodeError as e:
        return ParseResult.error(f"sqlfluff JSON parse error: {e}")
    return ParseResult.ok(diagnostics)
