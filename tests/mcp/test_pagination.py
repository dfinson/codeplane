"""Tests for mcp.pagination module.

Tests the epoch-stamped pagination cursor utilities.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

from codeplane.mcp.errors import CursorStaleError, MCPErrorCode
from codeplane.mcp.pagination import (
    PaginationCursor,
    compute_query_hash,
    create_cursor,
    parse_cursor_offset,
    validate_cursor,
)

# =============================================================================
# Mock EpochManager for testing
# =============================================================================


def create_mock_epoch_manager(epoch: int = 1) -> MagicMock:
    """Create a mock EpochManager with configurable epoch."""
    manager = MagicMock()
    manager.get_current_epoch.return_value = epoch
    return manager


# =============================================================================
# Tests for PaginationCursor
# =============================================================================


class TestPaginationCursor:
    """Tests for PaginationCursor dataclass."""

    def test_to_string_creates_valid_base64(self) -> None:
        """to_string creates valid base64-encoded JSON."""
        cursor = PaginationCursor(
            offset=10,
            epoch=5,
            query_hash="abc123",
            tool_name="test_tool",
        )
        cursor_str = cursor.to_string()

        # Should be valid base64
        decoded = base64.urlsafe_b64decode(cursor_str.encode())
        data = json.loads(decoded)

        assert data["o"] == 10
        assert data["e"] == 5
        assert data["h"] == "abc123"
        assert data["t"] == "test_tool"

    def test_from_string_decodes_valid_cursor(self) -> None:
        """from_string decodes a valid cursor string."""
        original = PaginationCursor(
            offset=20,
            epoch=3,
            query_hash="def456",
            tool_name="search",
        )
        cursor_str = original.to_string()

        decoded = PaginationCursor.from_string(cursor_str)

        assert decoded.offset == 20
        assert decoded.epoch == 3
        assert decoded.query_hash == "def456"
        assert decoded.tool_name == "search"

    def test_roundtrip(self) -> None:
        """Cursor can be encoded and decoded without loss."""
        cursor = PaginationCursor(
            offset=100,
            epoch=42,
            query_hash="xyz789",
            tool_name="list_files",
        )
        cursor_str = cursor.to_string()
        decoded = PaginationCursor.from_string(cursor_str)

        assert decoded.offset == cursor.offset
        assert decoded.epoch == cursor.epoch
        assert decoded.query_hash == cursor.query_hash
        assert decoded.tool_name == cursor.tool_name

    def test_from_string_invalid_base64_raises(self) -> None:
        """from_string raises ValueError for invalid base64."""
        with pytest.raises(ValueError, match="Invalid cursor format"):
            PaginationCursor.from_string("not-valid-base64!!!")

    def test_from_string_invalid_json_raises(self) -> None:
        """from_string raises ValueError for invalid JSON."""
        # Valid base64 but not JSON
        invalid = base64.urlsafe_b64encode(b"not json").decode()
        with pytest.raises(ValueError, match="Invalid cursor format"):
            PaginationCursor.from_string(invalid)

    def test_from_string_missing_keys_raises(self) -> None:
        """from_string raises ValueError for missing required keys."""
        # Valid JSON but missing keys
        incomplete = base64.urlsafe_b64encode(json.dumps({"o": 1}).encode()).decode()
        with pytest.raises(ValueError, match="Invalid cursor format"):
            PaginationCursor.from_string(incomplete)

    def test_zero_offset(self) -> None:
        """Cursor with offset 0 works correctly."""
        cursor = PaginationCursor(
            offset=0,
            epoch=1,
            query_hash="hash",
            tool_name="tool",
        )
        decoded = PaginationCursor.from_string(cursor.to_string())
        assert decoded.offset == 0

    def test_large_offset(self) -> None:
        """Cursor with large offset works correctly."""
        cursor = PaginationCursor(
            offset=1_000_000,
            epoch=999,
            query_hash="hash",
            tool_name="tool",
        )
        decoded = PaginationCursor.from_string(cursor.to_string())
        assert decoded.offset == 1_000_000
        assert decoded.epoch == 999


# =============================================================================
# Tests for compute_query_hash
# =============================================================================


class TestComputeQueryHash:
    """Tests for compute_query_hash function."""

    def test_deterministic_hash(self) -> None:
        """Same inputs produce same hash."""
        hash1 = compute_query_hash("search", query="test", limit=10)
        hash2 = compute_query_hash("search", query="test", limit=10)
        assert hash1 == hash2

    def test_different_params_different_hash(self) -> None:
        """Different parameters produce different hashes."""
        hash1 = compute_query_hash("search", query="test1")
        hash2 = compute_query_hash("search", query="test2")
        assert hash1 != hash2

    def test_different_tools_different_hash(self) -> None:
        """Different tool names produce different hashes."""
        hash1 = compute_query_hash("search", query="test")
        hash2 = compute_query_hash("list_files", query="test")
        assert hash1 != hash2

    def test_param_order_independent(self) -> None:
        """Parameter order doesn't affect hash."""
        hash1 = compute_query_hash("search", a="1", b="2")
        hash2 = compute_query_hash("search", b="2", a="1")
        assert hash1 == hash2

    def test_none_params_excluded(self) -> None:
        """None-valued parameters are excluded from hash."""
        hash1 = compute_query_hash("search", query="test", filter=None)
        hash2 = compute_query_hash("search", query="test")
        assert hash1 == hash2

    def test_hash_is_16_chars(self) -> None:
        """Hash is truncated to 16 characters."""
        h = compute_query_hash("tool", param="value")
        assert len(h) == 16

    def test_hash_is_hex(self) -> None:
        """Hash is hexadecimal."""
        h = compute_query_hash("tool", param="value")
        int(h, 16)  # Should not raise if valid hex


# =============================================================================
# Tests for create_cursor
# =============================================================================


class TestCreateCursor:
    """Tests for create_cursor function."""

    def test_creates_valid_cursor(self) -> None:
        """create_cursor returns decodable cursor string."""
        epoch_manager = create_mock_epoch_manager(epoch=5)

        cursor_str = create_cursor(
            epoch_manager=epoch_manager,
            tool_name="search",
            offset=10,
            query="test",
        )

        cursor = PaginationCursor.from_string(cursor_str)
        assert cursor.offset == 10
        assert cursor.epoch == 5
        assert cursor.tool_name == "search"

    def test_captures_current_epoch(self) -> None:
        """Cursor captures the current epoch from manager."""
        epoch_manager = create_mock_epoch_manager(epoch=42)

        cursor_str = create_cursor(
            epoch_manager=epoch_manager,
            tool_name="tool",
            offset=0,
        )

        cursor = PaginationCursor.from_string(cursor_str)
        assert cursor.epoch == 42

    def test_includes_query_hash(self) -> None:
        """Cursor includes query parameter hash."""
        epoch_manager = create_mock_epoch_manager()

        cursor_str = create_cursor(
            epoch_manager=epoch_manager,
            tool_name="search",
            offset=0,
            query="test",
            filter="*.py",
        )

        cursor = PaginationCursor.from_string(cursor_str)
        expected_hash = compute_query_hash("search", query="test", filter="*.py")
        assert cursor.query_hash == expected_hash


# =============================================================================
# Tests for validate_cursor
# =============================================================================


class TestValidateCursor:
    """Tests for validate_cursor function."""

    def test_valid_cursor_returns_decoded(self) -> None:
        """Valid cursor is decoded and returned."""
        epoch_manager = create_mock_epoch_manager(epoch=5)

        # Create cursor
        cursor_str = create_cursor(
            epoch_manager=epoch_manager,
            tool_name="search",
            offset=20,
            query="test",
        )

        # Validate with same parameters
        cursor = validate_cursor(
            cursor_str=cursor_str,
            epoch_manager=epoch_manager,
            tool_name="search",
            query="test",
        )

        assert cursor.offset == 20
        assert cursor.epoch == 5

    def test_stale_cursor_raises_cursor_stale_error(self) -> None:
        """Cursor from old epoch raises CursorStaleError."""
        old_epoch_manager = create_mock_epoch_manager(epoch=5)
        new_epoch_manager = create_mock_epoch_manager(epoch=6)

        # Create cursor at epoch 5
        cursor_str = create_cursor(
            epoch_manager=old_epoch_manager,
            tool_name="search",
            offset=10,
            query="test",
        )

        # Validate at epoch 6
        with pytest.raises(CursorStaleError) as exc_info:
            validate_cursor(
                cursor_str=cursor_str,
                epoch_manager=new_epoch_manager,
                tool_name="search",
                query="test",
            )

        # CursorStaleError stores epoch info in context
        assert exc_info.value.code == MCPErrorCode.CURSOR_STALE
        assert "cursor epoch: 5" in exc_info.value.message
        assert "current epoch: 6" in exc_info.value.message

    def test_wrong_tool_name_raises_value_error(self) -> None:
        """Cursor used with different tool raises ValueError."""
        epoch_manager = create_mock_epoch_manager()

        cursor_str = create_cursor(
            epoch_manager=epoch_manager,
            tool_name="search",
            offset=0,
            query="test",
        )

        with pytest.raises(ValueError, match="Cursor was created by 'search'"):
            validate_cursor(
                cursor_str=cursor_str,
                epoch_manager=epoch_manager,
                tool_name="list_files",  # Different tool
                query="test",
            )

    def test_different_query_params_raises_value_error(self) -> None:
        """Cursor used with different query params raises ValueError."""
        epoch_manager = create_mock_epoch_manager()

        cursor_str = create_cursor(
            epoch_manager=epoch_manager,
            tool_name="search",
            offset=0,
            query="original",
        )

        with pytest.raises(ValueError, match="query parameters don't match"):
            validate_cursor(
                cursor_str=cursor_str,
                epoch_manager=epoch_manager,
                tool_name="search",
                query="different",  # Different query
            )

    def test_invalid_cursor_format_raises_value_error(self) -> None:
        """Invalid cursor string raises ValueError."""
        epoch_manager = create_mock_epoch_manager()

        with pytest.raises(ValueError, match="Invalid cursor format"):
            validate_cursor(
                cursor_str="invalid-cursor",
                epoch_manager=epoch_manager,
                tool_name="search",
            )


# =============================================================================
# Tests for parse_cursor_offset
# =============================================================================


class TestParseCursorOffset:
    """Tests for parse_cursor_offset function."""

    def test_none_returns_zero(self) -> None:
        """None cursor returns offset 0."""
        assert parse_cursor_offset(None) == 0

    def test_valid_cursor_returns_offset(self) -> None:
        """Valid cursor returns its offset."""
        cursor = PaginationCursor(
            offset=42,
            epoch=1,
            query_hash="hash",
            tool_name="tool",
        )
        cursor_str = cursor.to_string()

        assert parse_cursor_offset(cursor_str) == 42

    def test_invalid_cursor_returns_zero(self) -> None:
        """Invalid cursor returns 0 (graceful fallback)."""
        assert parse_cursor_offset("invalid-cursor") == 0

    def test_empty_string_returns_zero(self) -> None:
        """Empty string returns 0."""
        assert parse_cursor_offset("") == 0


# =============================================================================
# Tests for cursor staleness scenarios
# =============================================================================


class TestCursorStalenessScenarios:
    """Tests for realistic cursor staleness scenarios."""

    def test_cursor_valid_across_multiple_pages(self) -> None:
        """Cursor remains valid when epoch doesn't change."""
        epoch_manager = create_mock_epoch_manager(epoch=10)

        # Simulate pagination through multiple pages
        for offset in [0, 20, 40, 60]:
            cursor_str = create_cursor(
                epoch_manager=epoch_manager,
                tool_name="search",
                offset=offset,
                query="test",
            )

            # All cursors should validate successfully
            cursor = validate_cursor(
                cursor_str=cursor_str,
                epoch_manager=epoch_manager,
                tool_name="search",
                query="test",
            )
            assert cursor.offset == offset

    def test_index_update_invalidates_all_cursors(self) -> None:
        """Index update (epoch bump) invalidates all existing cursors."""
        epoch_manager = create_mock_epoch_manager(epoch=10)

        # Create cursor at epoch 10
        cursor_str = create_cursor(
            epoch_manager=epoch_manager,
            tool_name="search",
            offset=50,
            query="test",
        )

        # Index updates, epoch bumps
        epoch_manager.get_current_epoch.return_value = 11

        # Old cursor should be stale
        with pytest.raises(CursorStaleError):
            validate_cursor(
                cursor_str=cursor_str,
                epoch_manager=epoch_manager,
                tool_name="search",
                query="test",
            )
