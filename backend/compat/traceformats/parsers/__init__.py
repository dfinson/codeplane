# Derived from traceforge-toolkit 0.1.5. MIT license: backend/compat/traceforge.LICENSE.
"""Pre-parsers that convert non-JSONL agent logs into structured event dicts."""

from backend.compat.traceformats.parsers.aider import AiderPreParser
from backend.compat.traceformats.parsers.base import (
    Block,
    MarkdownPreParser,
    node_text,
    strip_blockquote_markers,
    try_parse_json,
)
from backend.compat.traceformats.parsers.copilot import CopilotPreParser

__all__ = [
    "AiderPreParser",
    "Block",
    "CopilotPreParser",
    "MarkdownPreParser",
    "node_text",
    "strip_blockquote_markers",
    "try_parse_json",
]
