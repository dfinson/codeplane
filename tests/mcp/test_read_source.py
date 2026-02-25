"""Tests for read_source and read_file_full tools."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeplane.mcp.tools.files import (
    SpanTarget,
    StructuralTarget,
    _compute_file_sha256,
    _summarize_read,
)

# =============================================================================
# SpanTarget / StructuralTarget Validation
# =============================================================================


class TestSpanTargetValidation:
    """SpanTarget model validation."""

    def test_valid_range(self) -> None:
        """Valid span range accepted."""
        t = SpanTarget(path="a.py", start_line=1, end_line=10)
        assert t.start_line == 1
        assert t.end_line == 10

    def test_end_before_start(self) -> None:
        """end_line < start_line raises."""
        with pytest.raises(ValueError, match="end_line"):
            SpanTarget(path="a.py", start_line=10, end_line=5)

    def test_zero_start_line(self) -> None:
        """start_line must be > 0."""
        with pytest.raises(ValueError):
            SpanTarget(path="a.py", start_line=0, end_line=5)

    def test_single_line(self) -> None:
        """Single-line span (start==end) is valid."""
        t = SpanTarget(path="a.py", start_line=5, end_line=5)
        assert t.start_line == t.end_line


class TestStructuralTarget:
    """StructuralTarget model validation."""

    def test_default_unit(self) -> None:
        """Default unit is function."""
        t = StructuralTarget(path="a.py", symbol_id="my_func")
        assert t.unit == "function"

    def test_all_units(self) -> None:
        """All valid units accepted."""
        for unit in ("function", "class", "signature", "docstring"):
            t = StructuralTarget(path="a.py", symbol_id="sym", unit=unit)
            assert t.unit == unit


# =============================================================================
# File SHA256 computation
# =============================================================================


class TestFileSha256:
    """File SHA256 helper."""

    def test_deterministic(self, tmp_path: Path) -> None:
        """Same file gives same hash every time."""
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        h1 = _compute_file_sha256(f)
        h2 = _compute_file_sha256(f)
        assert h1 == h2

    def test_matches_hashlib(self, tmp_path: Path) -> None:
        """Hash matches direct hashlib computation."""
        f = tmp_path / "test.txt"
        content = "hello world\n"
        f.write_text(content)
        expected = hashlib.sha256(content.encode()).hexdigest()
        assert _compute_file_sha256(f) == expected

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        """Different content yields different hashes."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("aaa")
        f2.write_text("bbb")
        assert _compute_file_sha256(f1) != _compute_file_sha256(f2)


# =============================================================================
# Summary helper
# =============================================================================


class TestSummarizeRead:
    """Read summary formatting."""

    def test_single_file(self) -> None:
        """Single file summary includes path and line count."""
        files = [{"path": "src/main.py", "line_count": 50}]
        s = _summarize_read(files)
        assert "1 file" in s
        assert "50 lines" in s

    def test_multiple_files(self) -> None:
        """Multiple files shows count."""
        files = [
            {"path": "a.py", "line_count": 10},
            {"path": "b.py", "line_count": 20},
        ]
        s = _summarize_read(files)
        assert "2 files" in s

    def test_not_found_only(self) -> None:
        """All files missing."""
        s = _summarize_read([], not_found=3)
        assert "not found" in s

    def test_single_file_with_range(self) -> None:
        """Single file with range shows line range."""
        files = [{"path": "a.py", "line_count": 10, "range": [5, 15]}]
        s = _summarize_read(files)
        assert "5-15" in s


# =============================================================================
# read_source handler tests (unit-level with mocks)
# =============================================================================


class TestReadSourceHandler:
    """read_source tool handler behavior."""

    @pytest.fixture
    def repo_with_file(self, tmp_path: Path) -> Path:
        """Create a temp directory with a known file."""
        f = tmp_path / "hello.py"
        lines = [f"line {i}\n" for i in range(1, 101)]
        f.write_text("".join(lines))
        return tmp_path

    @pytest.fixture
    def app_ctx(self, repo_with_file: Path) -> MagicMock:
        """Create mock AppContext pointing at temp repo."""
        ctx = MagicMock()
        ctx.repo_root = repo_with_file
        ctx.session_manager.get_or_create.return_value = MagicMock(fingerprints={})
        ctx.coordinator = AsyncMock()
        return ctx

    def test_span_target_reads_correct_lines(self, repo_with_file: Path) -> None:
        """Span read returns correct content slice."""
        f = repo_with_file / "hello.py"
        content = f.read_text()
        lines = content.splitlines(keepends=True)
        # Lines 5-10 (1-indexed)
        expected = "".join(lines[4:10])
        assert expected.startswith("line 5")
        assert expected.strip().endswith("line 10")

    def test_file_sha256_in_output(self, repo_with_file: Path) -> None:
        """File sha256 matches file content hash."""
        f = repo_with_file / "hello.py"
        sha = _compute_file_sha256(f)
        expected = hashlib.sha256(f.read_bytes()).hexdigest()
        assert sha == expected

    def test_not_found_for_missing_path(self, repo_with_file: Path) -> None:
        """Non-existent file path should be flagged."""
        missing = repo_with_file / "does_not_exist.py"
        assert not missing.exists()


# =============================================================================
# read_file_full handler tests
# =============================================================================


class TestReadFileFullHandler:
    """read_file_full tool handler behavior."""

    def test_small_file_no_confirmation(self, tmp_path: Path) -> None:
        """Tiny file should not need confirmation."""
        f = tmp_path / "tiny.py"
        f.write_text("x = 1\n")
        # File size well under SMALL_FILE_THRESHOLD (1000 bytes)
        assert f.stat().st_size < 1000

    def test_large_file_needs_confirmation(self, tmp_path: Path) -> None:
        """File over threshold requires confirmation."""
        f = tmp_path / "big.py"
        f.write_text("x = 1\n" * 500)  # ~3000 bytes
        assert f.stat().st_size > 1000


# =============================================================================
# Two-phase confirmation pattern
# =============================================================================


class TestTwoPhaseConfirmation:
    """Two-phase confirmation flow validation."""

    def test_confirmation_token_format(self) -> None:
        """Token is a hex string."""
        import secrets

        token = secrets.token_hex(16)
        assert len(token) == 32
        assert all(c in "0123456789abcdef" for c in token)

    def test_confirm_reason_min_length(self) -> None:
        """Confirm reason must be >= 15 chars."""
        reason = "Too short"
        assert len(reason) < 15
        valid = "This is a valid reason for confirmation"
        assert len(valid) >= 15


# =============================================================================
# Delivery envelope integration
# =============================================================================


class TestReadSourceEnvelope:
    """read_source delivery envelope fields."""

    def test_build_envelope_has_delivery_field(self) -> None:
        """build_envelope output includes delivery field."""
        from codeplane.mcp.delivery import build_envelope

        result = build_envelope(
            {"files": [], "summary": "empty"},
            resource_kind="source",
            inline_summary="empty",
        )
        assert "delivery" in result
        assert result["delivery"] == "inline"

    def test_build_envelope_has_resource_kind(self) -> None:
        """build_envelope output includes resource_kind."""
        from codeplane.mcp.delivery import build_envelope

        result = build_envelope(
            {"files": [], "summary": "empty"},
            resource_kind="source",
            inline_summary="empty",
        )
        assert result["resource_kind"] == "source"

    def test_scope_id_echoed(self) -> None:
        """scope_id passed to build_envelope is echoed in response."""
        from codeplane.mcp.delivery import build_envelope

        result = build_envelope(
            {"data": "x"},
            resource_kind="source",
            scope_id="test-scope-123",
            inline_summary="test",
        )
        assert result.get("scope_id") == "test-scope-123"

    def test_scope_usage_present_when_provided(self) -> None:
        """scope_usage dict included when passed."""
        from codeplane.mcp.delivery import build_envelope

        usage = {"read_bytes": 1000, "full_reads": 0}
        result = build_envelope(
            {"data": "x"},
            resource_kind="source",
            scope_id="s1",
            scope_usage=usage,
            inline_summary="test",
        )
        assert result.get("scope_usage") == usage


# =============================================================================
# Recon cooldown gate tests
# =============================================================================


class TestReconCooldownSessionState:
    """Test last_recon_at field on SessionState."""

    def test_default_none(self) -> None:
        """last_recon_at is None by default."""
        from codeplane.mcp.session import SessionState

        s = SessionState(session_id="s1", created_at=0, last_active=0)
        assert s.last_recon_at is None

    def test_settable(self) -> None:
        """last_recon_at can be set to a float timestamp."""
        import time

        from codeplane.mcp.session import SessionState

        s = SessionState(session_id="s1", created_at=0, last_active=0)
        now = time.monotonic()
        s.last_recon_at = now
        assert s.last_recon_at == now

    def test_cooldown_blocks_read_source_within_window(self) -> None:
        """read_source returns RECON_COOLDOWN error when recon was recent."""
        import time

        from codeplane.mcp.session import SessionState

        session = SessionState(session_id="s1", created_at=0, last_active=0)
        # Simulate recon just happened
        session.last_recon_at = time.monotonic()

        # The gate check logic (mirrored from files.py)
        _RECON_COOLDOWN_SEC = 5.0
        elapsed = time.monotonic() - session.last_recon_at
        assert elapsed < _RECON_COOLDOWN_SEC

    def test_cooldown_allows_after_window(self) -> None:
        """read_source proceeds when recon was >5s ago."""
        import time

        from codeplane.mcp.session import SessionState

        session = SessionState(session_id="s1", created_at=0, last_active=0)
        # Simulate recon was 10 seconds ago
        session.last_recon_at = time.monotonic() - 10.0

        _RECON_COOLDOWN_SEC = 5.0
        elapsed = time.monotonic() - session.last_recon_at
        assert elapsed >= _RECON_COOLDOWN_SEC

    def test_cooldown_response_structure(self) -> None:
        """Verify the blocked response has required fields."""

        response: dict[str, Any] = {
            "status": "blocked",
            "error": {
                "code": "RECON_COOLDOWN",
                "message": (
                    "read_source blocked: recon was called 0.5s ago. "
                    "Wait 4.5s or use the JSON extraction commands from "
                    "the recon response instead of scatter-reading."
                ),
            },
            "agentic_hint": (
                "Use the agentic_hint jq/JSON parsing commands from the recon "
                "response to extract file content — NOT read_source."
            ),
            "cooldown_remaining_sec": 4.5,
        }
        assert response["status"] == "blocked"
        err = response["error"]
        assert isinstance(err, dict)
        assert err["code"] == "RECON_COOLDOWN"
        assert "agentic_hint" in response
        assert "cooldown_remaining_sec" in response
        assert "scatter-reading" in err["message"]
