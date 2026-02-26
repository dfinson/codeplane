"""Mutation helpers — EditParam model and fuzzy span matching.

These helpers are used by tests and potentially by edit tools.
The MCP tool handler that was previously in this module (write_source)
has been removed in v2.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# =============================================================================
# Parameter Models
# =============================================================================


class EditParam(BaseModel):
    """A single file edit.

    For create: provide content (full file body).
    For update: provide start_line, end_line, expected_file_sha256, and new_content
                (span-based replacement only).
    For delete: no extra fields needed.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path relative to repo root")
    action: Literal["create", "update", "delete"]

    # create: full file content
    content: str | None = Field(None, description="Full file content (required for create)")

    # update: span-based replacement (all three required for updates)
    start_line: int | None = Field(None, gt=0, description="Start line (1-indexed, inclusive)")
    end_line: int | None = Field(None, gt=0, description="End line (1-indexed, inclusive)")
    expected_file_sha256: str | None = Field(
        None, description="SHA256 of whole file from read_source (required for update)"
    )
    new_content: str | None = Field(
        None, description="Replacement content for the span (required for update)"
    )
    expected_content: str | None = Field(
        None,
        description=(
            "Expected content at the span location (required for update). "
            "The server fuzzy-matches nearby lines if your line numbers are "
            "slightly off, auto-correcting within a few lines."
        ),
    )

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "EditParam":
        if self.action == "create":
            if self.content is None:
                msg = "content is required for action='create'"
                raise ValueError(msg)
        elif self.action == "update":
            missing = []
            if self.start_line is None:
                missing.append("start_line")
            if self.end_line is None:
                missing.append("end_line")
            if self.expected_file_sha256 is None:
                missing.append("expected_file_sha256")
            if self.new_content is None:
                missing.append("new_content")
            if self.expected_content is None:
                missing.append("expected_content")
            if missing:
                msg = f"update requires: {', '.join(missing)}"
                raise ValueError(msg)
            if (self.end_line or 0) < (self.start_line or 0):
                msg = f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
                raise ValueError(msg)
        return self


# =============================================================================
# Fuzzy Span Matching
# =============================================================================

_FUZZY_SEARCH_WINDOW = 5  # Max lines to search in each direction


def _fuzzy_match_span(
    lines: list[str],
    start: int,
    end: int,
    expected_content: str,
) -> tuple[int, int, bool]:
    """Try to find expected_content near the given span, auto-correcting line numbers.

    Args:
        lines: All file lines (with line endings).
        start: 0-indexed start line.
        end: 0-indexed exclusive end line (like slice notation).
        expected_content: The content the agent expects at [start:end].

    Returns:
        (corrected_start, corrected_end, was_corrected) tuple.
        If no match found nearby, returns original values with was_corrected=False.
    """
    expected_lines = expected_content.splitlines(keepends=True)
    # Normalize: ensure trailing newline for comparison
    if expected_lines and not expected_lines[-1].endswith("\n"):
        expected_lines[-1] += "\n"
    search_len = len(expected_lines)
    span_width = end - start

    # First check: does expected_content match at the given position and width?
    actual_at_span = lines[start:end]
    if _lines_match(actual_at_span, expected_lines):
        return start, end, False  # Already correct

    # Width-correction: same position but use expected_content's line count.
    # Catches off-by-one in end_line (agent miscounted span width).
    if search_len != span_width and start >= 0 and start + search_len <= len(lines):
        candidate = lines[start : start + search_len]
        if _lines_match(candidate, expected_lines):
            return start, start + search_len, True

    # Search nearby positions (both offset and width corrected)
    for offset in range(1, _FUZZY_SEARCH_WINDOW + 1):
        for direction in (-1, 1):
            candidate_start = start + (offset * direction)
            candidate_end = candidate_start + search_len
            if candidate_start < 0 or candidate_end > len(lines):
                continue
            candidate = lines[candidate_start:candidate_end]
            if _lines_match(candidate, expected_lines):
                return candidate_start, candidate_end, True

    # No match found — return original (caller verifies content)
    return start, end, False


def _lines_match(actual: list[str], expected: list[str]) -> bool:
    """Compare lines with whitespace-normalized matching."""
    if len(actual) != len(expected):
        return False
    return all(a.rstrip() == e.rstrip() for a, e in zip(actual, expected, strict=True))


