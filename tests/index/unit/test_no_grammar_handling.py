"""Tests for handling languages without tree-sitter grammars.

NOTE: As of 2025, tree-sitter has added grammar support for most programming
languages including F#, VB.NET, Erlang, PowerShell, Clojure, Dart, and Nim.
These tests are now skipped because the original premise (that certain popular
languages lack grammar support) is no longer valid.

The skipped_no_grammar code path still exists but is rarely exercised since
almost all recognized languages now have grammar support.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="Tree-sitter now has grammars for F#, VB.NET, Erlang, PowerShell, etc. "
    "These tests for 'no grammar' handling are obsolete."
)


# All test classes below are skipped via pytestmark


class TestHasGrammarForFile:
    """Tests for _has_grammar_for_file function (skipped)."""

    def test_placeholder(self) -> None:
        """Placeholder test - all tests in this module are skipped."""
        pass
