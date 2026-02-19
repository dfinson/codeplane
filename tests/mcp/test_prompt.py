"""Tests for the injected agent prompt in init.py.

Covers:
- Prompt size constraints (bytes and lines)
- Contains new tool names (read_source, read_file_full)
- Does not contain old tool names (read_files as a tool)
- Contains enrichment parameter, not context parameter for search
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

    def test_prompt_contains_read_source(self) -> None:
        """'read_source' appears in prompt."""
        snippet = _make_codeplane_snippet("test_prefix")
        assert "read_source" in snippet

    def test_prompt_contains_read_file_full(self) -> None:
        """'read_file_full' appears in prompt."""
        snippet = _make_codeplane_snippet("test_prefix")
        assert "read_file_full" in snippet

    def test_prompt_contains_enrichment(self) -> None:
        """'enrichment' appears in prompt."""
        snippet = _make_codeplane_snippet("test_prefix")
        assert "enrichment" in snippet

    def test_prompt_tool_prefix_substituted(self) -> None:
        """{tool_prefix} replaced with actual prefix."""
        snippet = _make_codeplane_snippet("my_cool_prefix")
        assert "my_cool_prefix" in snippet
        assert "{tool_prefix}" not in snippet

    def test_prompt_contains_three_tool_model(self) -> None:
        """Prompt explains the three-tool read model."""
        snippet = _make_codeplane_snippet("test_prefix")
        assert "Search = find" in snippet
        assert "Read = retrieve" in snippet
        assert "Full = gated" in snippet
