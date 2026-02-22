"""Configuration constants.

This module contains truly constant values that should NOT be user-configurable.
These are protocol constraints, API stability limits, and implementation details.

For configurable values, see models.py (TimeoutsConfig, LimitsConfig, etc.).
"""

# ======================================================================# MCP Tool Limits
# ======================================================================# These are hard caps for API stability and security. Users can configure
# defaults below these, but cannot exceed them.
SEARCH_MAX_LIMIT = 100
"""Maximum results for index search queries."""

SEARCH_CONTEXT_LINES_MAX = 25
"""Maximum context lines for line-based search context modes."""

SEARCH_SCOPE_FALLBACK_LINES_DEFAULT = 25
"""Default fallback lines when structural scope resolution fails."""

MAP_DEPTH_MAX = 10
"""Maximum directory tree depth for repo mapping."""

MAP_LIMIT_MAX = 1000
"""Maximum entries for repo mapping."""

FILES_LIST_MAX = 1000
"""Maximum entries for file listing."""

LEXICAL_FALLBACK_MAX = 500
"""Maximum lexical search results for refactor fallback."""

MOVE_LEXICAL_MAX = 200
"""Maximum lexical search results for move refactor."""

DIFF_CHANGES_MAX = 100
"""Maximum structural changes per page for semantic diff."""

# ======================================================================
# Delivery Envelope
# ======================================================================

INLINE_CAP_BYTES = 8_000
"""Default inline cap for delivery envelope. Fits within VS Code's inline display."""

# ======================================================================# Delivery Envelope Constants
# ======================================================================

MAX_SPAN_LINES = 500
"""Maximum lines per span in read_source."""

MAX_INLINE_BYTES_PER_CALL = 20_000
"""Maximum total inline bytes per read_source call."""

MAX_TARGETS_PER_CALL = 20
"""Maximum targets per read_source call."""

SMALL_FILE_THRESHOLD = 1_000
"""Files under this byte count skip two-phase confirmation in read_file_full."""

# ======================================================================# Internal Implementation Constants
# ======================================================================# These are not exposed to users and are implementation details.

EPOCH_POLL_MS = 10
"""Polling interval (ms) for epoch await. Tight loop, not configurable."""

INSPECT_CONTEXT_LINES_DEFAULT = 2
"""Default context lines for refactor inspection."""

# ======================================================================# Protocol/Validation Constants
# ======================================================================
PORT_MIN = 0
PORT_MAX = 65535
"""Valid port range."""
