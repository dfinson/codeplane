"""Byte-size measurement for MCP response fields."""

from __future__ import annotations

import json
from typing import Any


def measure_bytes(item: dict[str, Any], *, nesting_depth: int = 0) -> int:
    """Return the UTF-8 byte size of *item* serialised as pretty-printed JSON.

    Uses indent=2 to match VS Code's display format. This ensures our
    budget calculations match what users actually see, preventing the
    "Large tool result" warnings from VS Code.

    Args:
        item: The dict to measure.
        nesting_depth: How many levels deep this item will be nested in the
            final response. Each level adds 2 extra spaces per line.
            For items in a top-level array, use nesting_depth=1.

    Example:
        measure_bytes({"x": 1})  # standalone object
        measure_bytes({"x": 1}, nesting_depth=1)  # item in an array
    """
    base = json.dumps(item, indent=2)
    if nesting_depth > 0:
        # Add extra indentation to each line
        extra_indent = "  " * nesting_depth
        lines = base.split("\n")
        indented = "\n".join(extra_indent + line for line in lines)
        # Also add 2 bytes for array item separator (",\n")
        return len(indented.encode("utf-8")) + 2
    return len(base.encode("utf-8"))
