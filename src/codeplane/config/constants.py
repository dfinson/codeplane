"""Configuration constants.

This module contains truly constant values that should NOT be user-configurable.
These are protocol constraints, API stability limits, and implementation details.

For configurable values, see models.py (TimeoutsConfig, LimitsConfig, etc.).
"""

# =============================================================================
# MCP Tool Pagination Maximums
# =============================================================================
# These are hard caps for API stability and security. Users can configure
# defaults below these, but cannot exceed them.

SEARCH_MAX_LIMIT = 100
"""Maximum results for index search queries."""

MAP_DEPTH_MAX = 10
"""Maximum directory tree depth for repo mapping."""

MAP_LIMIT_MAX = 1000
"""Maximum entries for repo mapping."""

FILES_LIST_MAX = 1000
"""Maximum entries for file listing."""

GIT_LOG_MAX = 100
"""Maximum commits for git log queries."""

GIT_BLAME_MAX = 1000
"""Maximum lines for git blame queries."""

GIT_REFS_MAX = 500
"""Maximum refs for git reference queries."""

LEXICAL_FALLBACK_MAX = 500
"""Maximum lexical search results for refactor fallback."""

MOVE_LEXICAL_MAX = 200
"""Maximum lexical search results for move refactor."""

# =============================================================================
# Internal Implementation Constants
# =============================================================================
# These are not exposed to users and are implementation details.

EPOCH_POLL_MS = 10
"""Polling interval (ms) for epoch await. Tight loop, not configurable."""

INSPECT_CONTEXT_LINES_DEFAULT = 2
"""Default context lines for refactor inspection."""

# =============================================================================
# Protocol/Validation Constants
# =============================================================================

PORT_MIN = 0
PORT_MAX = 65535
"""Valid port range."""
