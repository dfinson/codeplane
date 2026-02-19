"""Tests for OS-aware extraction commands in delivery.py."""

from __future__ import annotations

import sys
from unittest.mock import patch

from codeplane.mcp.delivery import (
    _jq_to_powershell,
    _os_extraction_cmds,
)


class TestJqToPowershell:
    """Tests for _jq_to_powershell conversion."""

    def test_simple_property_access(self) -> None:
        """Simple property access: .foo -> .foo"""
        result = _jq_to_powershell("jq '.diff' path.json", "path.json")
        assert result == "(gc path.json | ConvertFrom-Json).diff"

    def test_nested_property_access(self) -> None:
        """Nested property access: .foo.bar -> .foo.bar"""
        result = _jq_to_powershell("jq '.results.count' path.json", "path.json")
        assert result == "(gc path.json | ConvertFrom-Json).results.count"

    def test_length_filter(self) -> None:
        """Length filter: .foo | length -> .foo.Count"""
        result = _jq_to_powershell("jq '.changes | length' path.json", "path.json")
        assert result == "(gc path.json | ConvertFrom-Json).changes.Count"

    def test_object_construction(self) -> None:
        """Object construction: {passed, failed, total} -> Select-Object"""
        result = _jq_to_powershell("jq '{passed, failed, total}' path.json", "path.json")
        assert "Select-Object" in result
        assert "passed" in result

    def test_raw_flag(self) -> None:
        """Raw output flag: jq -r '.diff' -> same result"""
        result = _jq_to_powershell("jq -r '.diff' path.json", "path.json")
        assert result == "(gc path.json | ConvertFrom-Json).diff"

    def test_fallback_for_complex_filter(self) -> None:
        """Complex filter falls back to jq hint."""
        result = _jq_to_powershell(
            "jq '[.results[] | {author, lines: (.end_line - .start_line)}]' path.json",
            "path.json",
        )
        assert "jq" in result.lower() or "Requires" in result


class TestOsExtractionCmds:
    """Tests for _os_extraction_cmds OS-awareness."""

    def test_unix_returns_single_jq_cmd(self) -> None:
        """On Unix, returns only the jq command."""
        with patch.object(sys, "platform", "linux"):
            result = _os_extraction_cmds("jq '.foo' path.json", "path.json")
        assert len(result) == 1
        assert result[0] == "jq '.foo' path.json"

    def test_macos_returns_single_jq_cmd(self) -> None:
        """On macOS, returns only the jq command."""
        with patch.object(sys, "platform", "darwin"):
            result = _os_extraction_cmds("jq '.foo' path.json", "path.json")
        assert len(result) == 1
        assert result[0] == "jq '.foo' path.json"

    def test_windows_returns_both_cmds(self) -> None:
        """On Windows, returns jq and PowerShell commands."""
        with patch.object(sys, "platform", "win32"):
            result = _os_extraction_cmds("jq '.foo' path.json", "path.json")
        assert len(result) == 2
        assert "jq" in result[0]
        assert "gc" in result[1] or "ConvertFrom-Json" in result[1] or "Requires" in result[1]

    def test_windows_adds_hint_for_head_pipe(self) -> None:
        """On Windows, adds a hint when jq command pipes to head."""
        with patch.object(sys, "platform", "win32"):
            result = _os_extraction_cmds("jq '.diff' path.json | head -100", "path.json")
        assert len(result) == 3
        assert "Select-Object -First" in result[2]
