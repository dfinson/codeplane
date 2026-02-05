"""Epoch-stamped pagination cursor utilities.

Provides cursor creation and validation with epoch tracking to detect
when the underlying index has changed during pagination.

Per review-by-category.md §3.3: Cursors are stamped with the epoch
at creation time. If the index epoch advances, cursors are invalidated.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from codeplane.mcp.errors import CursorStaleError

if TYPE_CHECKING:
    from codeplane.index._internal.db import EpochManager


@dataclass
class PaginationCursor:
    """Epoch-stamped pagination cursor.

    Attributes:
        offset: Current offset in result set
        epoch: Index epoch when cursor was created
        query_hash: Hash of query parameters for validation
        tool_name: Name of tool that created cursor
    """

    offset: int
    epoch: int
    query_hash: str
    tool_name: str

    def to_string(self) -> str:
        """Encode cursor as base64 string for transport."""
        data = {
            "o": self.offset,
            "e": self.epoch,
            "h": self.query_hash,
            "t": self.tool_name,
        }
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()

    @classmethod
    def from_string(cls, cursor_str: str) -> PaginationCursor:
        """Decode cursor from base64 string.

        Raises:
            ValueError: If cursor format is invalid
        """
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor_str.encode()))
            return cls(
                offset=data["o"],
                epoch=data["e"],
                query_hash=data["h"],
                tool_name=data["t"],
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(f"Invalid cursor format: {e}") from e


def compute_query_hash(tool_name: str, **params: Any) -> str:
    """Compute hash of query parameters for cursor validation.

    This ensures cursors are only used with the same query parameters.
    """
    # Sort params for deterministic hash
    sorted_params = sorted((k, str(v)) for k, v in params.items() if v is not None)
    hash_input = f"{tool_name}:{sorted_params}"
    return hashlib.sha256(hash_input.encode()).hexdigest()[:16]


def create_cursor(
    epoch_manager: EpochManager,
    tool_name: str,
    offset: int,
    **query_params: Any,
) -> str:
    """Create an epoch-stamped pagination cursor.

    Args:
        epoch_manager: EpochManager to get current epoch
        tool_name: Name of the tool creating the cursor
        offset: Current offset in result set
        **query_params: Query parameters to hash for validation

    Returns:
        Base64-encoded cursor string
    """
    current_epoch = epoch_manager.get_current_epoch()
    query_hash = compute_query_hash(tool_name, **query_params)

    cursor = PaginationCursor(
        offset=offset,
        epoch=current_epoch,
        query_hash=query_hash,
        tool_name=tool_name,
    )
    return cursor.to_string()


def validate_cursor(
    cursor_str: str,
    epoch_manager: EpochManager,
    tool_name: str,
    **query_params: Any,
) -> PaginationCursor:
    """Validate and decode a pagination cursor.

    Args:
        cursor_str: Base64-encoded cursor string
        epoch_manager: EpochManager to check current epoch
        tool_name: Expected tool name
        **query_params: Expected query parameters

    Returns:
        Decoded PaginationCursor if valid

    Raises:
        CursorStaleError: If index epoch has changed since cursor creation.
            This is a clear signal to the agent to RESTART PAGINATION.
        ValueError: If cursor format is invalid or doesn't match query.
    """
    cursor = PaginationCursor.from_string(cursor_str)

    # Check tool name matches
    if cursor.tool_name != tool_name:
        raise ValueError(
            f"Cursor was created by '{cursor.tool_name}', cannot use with '{tool_name}'"
        )

    # Check query hash matches (same query parameters)
    expected_hash = compute_query_hash(tool_name, **query_params)
    if cursor.query_hash != expected_hash:
        raise ValueError(
            "Cursor query parameters don't match. "
            "Use the same query parameters as the original request, or start fresh without a cursor."
        )

    # Check epoch - this is the critical staleness check
    current_epoch = epoch_manager.get_current_epoch()
    if cursor.epoch != current_epoch:
        # Raise specific error with clear remediation instructions
        raise CursorStaleError(
            cursor_epoch=cursor.epoch,
            current_epoch=current_epoch,
        )

    return cursor


def parse_cursor_offset(cursor_str: str | None) -> int:
    """Extract offset from cursor, returning 0 if cursor is None.

    This is a convenience function for simple offset-based pagination
    where epoch validation is handled separately or not needed.
    """
    if cursor_str is None:
        return 0
    try:
        cursor = PaginationCursor.from_string(cursor_str)
        return cursor.offset
    except ValueError:
        return 0
