"""Tests for config/constants.py module.

Covers:
- MCP tool pagination maximums
- Internal implementation constants
- Protocol/validation constants
"""

from __future__ import annotations

from codeplane.config.constants import (
    EPOCH_POLL_MS,
    FILES_LIST_MAX,
    INSPECT_CONTEXT_LINES_DEFAULT,
    LEXICAL_FALLBACK_MAX,
    MAP_DEPTH_MAX,
    MAP_LIMIT_MAX,
    MOVE_LEXICAL_MAX,
    PORT_MAX,
    PORT_MIN,
    SEARCH_MAX_LIMIT,
)


class TestMCPToolMaximums:
    """Tests for MCP tool pagination maximums."""

    def test_search_max_limit(self) -> None:
        """Search max is 100."""
        assert SEARCH_MAX_LIMIT == 100

    def test_map_depth_max(self) -> None:
        """Map depth max is 10."""
        assert MAP_DEPTH_MAX == 10

    def test_map_limit_max(self) -> None:
        """Map limit max is 1000."""
        assert MAP_LIMIT_MAX == 1000

    def test_files_list_max(self) -> None:
        """Files list max is 1000."""
        assert FILES_LIST_MAX == 1000

    def test_lexical_fallback_max(self) -> None:
        """Lexical fallback max is 500."""
        assert LEXICAL_FALLBACK_MAX == 500

    def test_move_lexical_max(self) -> None:
        """Move lexical max is 200."""
        assert MOVE_LEXICAL_MAX == 200


class TestInternalConstants:
    """Tests for internal implementation constants."""

    def test_epoch_poll_ms(self) -> None:
        """Epoch poll is small (tight loop)."""
        assert EPOCH_POLL_MS == 10
        assert EPOCH_POLL_MS > 0

    def test_inspect_context_lines_default(self) -> None:
        """Inspect context lines default is 2."""
        assert INSPECT_CONTEXT_LINES_DEFAULT == 2


class TestPortConstants:
    """Tests for port validation constants."""

    def test_port_min_is_zero(self) -> None:
        """Port min is 0."""
        assert PORT_MIN == 0

    def test_port_max_is_65535(self) -> None:
        """Port max is 65535."""
        assert PORT_MAX == 65535

    def test_port_range_is_valid(self) -> None:
        """Port range covers standard ports."""
        assert PORT_MIN <= 80 <= PORT_MAX  # HTTP
        assert PORT_MIN <= 443 <= PORT_MAX  # HTTPS
        assert PORT_MIN <= 7654 <= PORT_MAX  # Default daemon port
