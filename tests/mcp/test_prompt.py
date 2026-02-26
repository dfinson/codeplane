"""Tests for the injected agent prompt in init.py.

Covers:
- Prompt size constraints (bytes and lines)
- Contains v2 tool names (recon, recon_resolve, refactor_edit, checkpoint)
- Does not contain dead tool names
- Tool prefix substitution
"""

from __future__ import annotations

from codeplane.cli.init import _make_codeplane_snippet


class TestPromptSize:
    """Tests for prompt size constraints."""

    def test_prompt_byte_size(self) -> None:
        """Prompt output <= 7500 bytes."""
        snippet = _make_codeplane_snippet("test_prefix")
        size = len(snippet.encode("utf-8"))
        assert size <= 7500, f"Prompt is {size} bytes, expected <= 7500"

    def test_prompt_line_count(self) -> None:
        """Prompt output <= 150 lines."""
        snippet = _make_codeplane_snippet("test_prefix")
        lines = snippet.strip().split("\n")
        assert len(lines) <= 150, f"Prompt is {len(lines)} lines, expected <= 150"


class TestPromptContent:
    """Tests for prompt content correctness."""

    def test_prompt_contains_recon(self) -> None:
        """'recon' appears as the primary entry point."""
        snippet = _make_codeplane_snippet("test_prefix")
        assert "recon" in snippet

    def test_prompt_contains_recon_resolve(self) -> None:
        """'recon_resolve' appears in prompt."""
        snippet = _make_codeplane_snippet("test_prefix")
        assert "recon_resolve" in snippet

    def test_prompt_contains_refactor_edit(self) -> None:
        """'refactor_edit' appears in prompt."""
        snippet = _make_codeplane_snippet("test_prefix")
        assert "refactor_edit" in snippet

    def test_prompt_contains_checkpoint(self) -> None:
        """'checkpoint' appears in prompt."""
        snippet = _make_codeplane_snippet("test_prefix")
        assert "checkpoint" in snippet

    def test_prompt_tool_prefix_substituted(self) -> None:
        """{tool_prefix} replaced with actual prefix."""
        snippet = _make_codeplane_snippet("my_cool_prefix")
        assert "my_cool_prefix" in snippet
        assert "{tool_prefix}" not in snippet

    def test_prompt_no_dead_tool_names(self) -> None:
        """Dead v1 tool names should not appear."""
        snippet = _make_codeplane_snippet("test_prefix")
        for dead in ["write_source", "read_file_full", "reset_budget"]:
            assert dead not in snippet, f"Dead tool '{dead}' found in snippet"

    def test_prompt_contains_scaffold_lite_tiers(self) -> None:
        """Prompt mentions SCAFFOLD and LITE tiers."""
        snippet = _make_codeplane_snippet("test_prefix")
        assert "SCAFFOLD" in snippet
        assert "LITE" in snippet
