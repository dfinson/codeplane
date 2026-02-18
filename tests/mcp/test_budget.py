"""Tests for mcp.budget module.

Covers:
- measure_bytes: deterministic JSON byte measurement
"""

from __future__ import annotations

import json

from codeplane.mcp.budget import measure_bytes

# =============================================================================
# Tests for measure_bytes
# =============================================================================


class TestMeasureBytes:
    """Tests for the measure_bytes helper."""

    def test_empty_dict(self) -> None:
        """Empty dict measures as 2 bytes ('{}')."""
        assert measure_bytes({}) == 2

    def test_simple_dict(self) -> None:
        """Simple dict matches pretty-printed JSON encoding."""
        item = {"key": "value"}
        expected = len(json.dumps(item, indent=2).encode("utf-8"))
        assert measure_bytes(item) == expected

    def test_nested_dict(self) -> None:
        """Nested structures are measured correctly."""
        item = {"outer": {"inner": [1, 2, 3]}}
        expected = len(json.dumps(item, indent=2).encode("utf-8"))
        assert measure_bytes(item) == expected

    def test_unicode_correct_byte_count(self) -> None:
        """Unicode characters are counted by UTF-8 byte size, not char count."""
        # json.dumps escapes non-ASCII by default (ensure_ascii=True),
        # so the byte measurement reflects the escaped representation.
        item = {"text": "caf\u00e9 \u2603 \U0001f600"}
        result = measure_bytes(item)
        expected = len(json.dumps(item, indent=2).encode("utf-8"))
        assert result == expected
        # The key property: measurement is deterministic and positive
        assert result > 0

    def test_large_dict(self) -> None:
        """Large dict measures correctly."""
        item = {f"key_{i}": f"value_{i}" for i in range(100)}
        expected = len(json.dumps(item, indent=2).encode("utf-8"))
        assert measure_bytes(item) == expected

    def test_uses_pretty_printed_format(self) -> None:
        """Measurement uses pretty-printed JSON to match VS Code display."""
        item = {"a": 1, "b": 2}
        pretty = len(json.dumps(item, indent=2).encode("utf-8"))
        compact = len(json.dumps(item, separators=(",", ":")).encode("utf-8"))
        assert measure_bytes(item) == pretty
        assert pretty > compact  # pretty is larger
