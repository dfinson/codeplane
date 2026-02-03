"""Tests for MCP errors module."""

from codeplane.mcp.errors import (
    ERROR_CATALOG,
    ContentNotFoundError,
    DryRunExpiredError,
    DryRunRequiredError,
    ErrorResponse,
    HashMismatchError,
    HookFailedError,
    InvalidRangeError,
    MCPError,
    MCPErrorCode,
    MultipleMatchesError,
    get_error_documentation,
)


class TestMCPErrorCode:
    """Tests for MCPErrorCode enum."""

    def test_all_codes_have_unique_values(self):
        """All error codes have unique string values."""
        values = [code.value for code in MCPErrorCode]
        assert len(values) == len(set(values))

    def test_common_codes_exist(self):
        """Common error codes are defined."""
        assert hasattr(MCPErrorCode, "INTERNAL_ERROR")
        assert hasattr(MCPErrorCode, "INVALID_RANGE")
        assert hasattr(MCPErrorCode, "FILE_NOT_FOUND")
        assert hasattr(MCPErrorCode, "PERMISSION_DENIED")

    def test_mutation_codes_exist(self):
        """Mutation-related error codes are defined."""
        assert hasattr(MCPErrorCode, "CONTENT_NOT_FOUND")
        assert hasattr(MCPErrorCode, "MULTIPLE_MATCHES")
        assert hasattr(MCPErrorCode, "HASH_MISMATCH")

    def test_range_code_exists(self):
        """Range-related error code is defined."""
        assert hasattr(MCPErrorCode, "INVALID_RANGE")


class TestErrorResponse:
    """Tests for ErrorResponse dataclass."""

    def test_create_minimal(self):
        """Create ErrorResponse with required fields."""
        resp = ErrorResponse(
            code=MCPErrorCode.INTERNAL_ERROR,
            message="Something went wrong",
            remediation="Try again",
        )
        assert resp.code == MCPErrorCode.INTERNAL_ERROR
        assert resp.message == "Something went wrong"
        assert resp.remediation == "Try again"
        assert resp.context == {}

    def test_create_with_context(self):
        """Create ErrorResponse with context."""
        resp = ErrorResponse(
            code=MCPErrorCode.FILE_NOT_FOUND,
            message="File not found",
            remediation="Check the path",
            context={"path": "missing.py"},
        )
        assert resp.context == {"path": "missing.py"}

    def test_to_dict(self):
        """to_dict produces correct structure."""
        resp = ErrorResponse(
            code=MCPErrorCode.INVALID_RANGE,
            message="Bad range",
            remediation="Fix lines",
            context={"start": 10, "end": 5},
        )
        d = resp.to_dict()
        assert d["code"] == MCPErrorCode.INVALID_RANGE.value
        assert d["message"] == "Bad range"
        assert d["remediation"] == "Fix lines"
        assert d["context"] == {"start": 10, "end": 5}


class TestMCPError:
    """Tests for MCPError base exception."""

    def test_create_basic(self):
        """Create MCPError with code and message."""
        err = MCPError(
            code=MCPErrorCode.INTERNAL_ERROR,
            message="Test error",
            remediation="Fix it",
        )
        assert err.code == MCPErrorCode.INTERNAL_ERROR
        assert err.message == "Test error"
        assert err.remediation == "Fix it"
        assert err.context == {}

    def test_create_with_context(self):
        """Create MCPError with context kwargs."""
        err = MCPError(
            code=MCPErrorCode.FILE_NOT_FOUND,
            message="Resource missing",
            remediation="Check path",
            path="test.py",
            line=42,
        )
        assert err.context == {"line": 42}
        assert err.path == "test.py"

    def test_str_representation(self):
        """String representation includes message."""
        err = MCPError(
            code=MCPErrorCode.INVALID_RANGE,
            message="Bad input",
            remediation="Fix",
        )
        s = str(err)
        assert "Bad input" in s

    def test_to_response(self):
        """to_response creates ErrorResponse."""
        err = MCPError(
            code=MCPErrorCode.PERMISSION_DENIED,
            message="No access",
            remediation="Check permissions",
            path="file.py",
        )
        resp = err.to_response()
        assert isinstance(resp, ErrorResponse)
        assert resp.code == MCPErrorCode.PERMISSION_DENIED
        assert resp.message == "No access"


class TestContentNotFoundError:
    """Tests for ContentNotFoundError."""

    def test_creates_with_correct_code(self):
        """Uses CONTENT_NOT_FOUND error code."""
        err = ContentNotFoundError("test.py", "needle")
        assert err.code == MCPErrorCode.CONTENT_NOT_FOUND

    def test_message_format(self):
        """Message includes path."""
        err = ContentNotFoundError("src/main.py", "def missing_func")
        assert "src/main.py" in err.message

    def test_has_remediation(self):
        """Error has remediation hint."""
        err = ContentNotFoundError("file.py", "old text")
        assert err.remediation is not None
        assert len(err.remediation) > 0


class TestMultipleMatchesError:
    """Tests for MultipleMatchesError."""

    def test_creates_with_correct_code(self):
        """Uses MULTIPLE_MATCHES error code."""
        err = MultipleMatchesError("test.py", count=3, lines=[10, 20, 30])
        assert err.code == MCPErrorCode.MULTIPLE_MATCHES

    def test_context_contain_counts(self):
        """Context includes match count and lines."""
        err = MultipleMatchesError("f.py", count=10, lines=[1, 2, 3])
        assert err.context.get("match_count") == 10
        assert err.context.get("match_lines") == [1, 2, 3]


class TestInvalidRangeError:
    """Tests for InvalidRangeError."""

    def test_creates_with_correct_code(self):
        """Uses INVALID_RANGE error code."""
        err = InvalidRangeError("test.py", start=100, end=50, line_count=200)
        assert err.code == MCPErrorCode.INVALID_RANGE

    def test_has_remediation(self):
        """Error has remediation hint."""
        err = InvalidRangeError("f.py", start=1, end=1000, line_count=100)
        assert err.remediation is not None


class TestHashMismatchError:
    """Tests for HashMismatchError."""

    def test_creates_with_correct_code(self):
        """Uses HASH_MISMATCH error code."""
        err = HashMismatchError("test.py", expected="abc123", actual="def456")
        assert err.code == MCPErrorCode.HASH_MISMATCH

    def test_has_path(self):
        """Error includes path."""
        err = HashMismatchError("f.py", expected="aaa", actual="bbb")
        assert err.path == "f.py"


class TestHookFailedError:
    """Tests for HookFailedError."""

    def test_creates_with_correct_code(self):
        """Uses HOOK_FAILED error code."""
        err = HookFailedError("pre-commit", exit_code=1, stdout="", stderr="lint failed")
        assert err.code == MCPErrorCode.HOOK_FAILED

    def test_context_contain_hook_info(self):
        """Context includes hook type and exit code."""
        err = HookFailedError("post-save", exit_code=2, stdout="", stderr="error msg")
        assert err.context.get("hook_type") == "post-save"
        assert err.context.get("exit_code") == 2


class TestDryRunRequiredError:
    """Tests for DryRunRequiredError."""

    def test_creates_with_correct_code(self):
        """Uses DRY_RUN_REQUIRED error code."""
        err = DryRunRequiredError("test.py")
        assert err.code == MCPErrorCode.DRY_RUN_REQUIRED

    def test_message_includes_path(self):
        """Message includes file path."""
        err = DryRunRequiredError("src/main.py")
        assert "src/main.py" in err.message


class TestDryRunExpiredError:
    """Tests for DryRunExpiredError."""

    def test_creates_with_correct_code(self):
        """Uses DRY_RUN_EXPIRED error code."""
        err = DryRunExpiredError("dry_123", 120.5)
        assert err.code == MCPErrorCode.DRY_RUN_EXPIRED

    def test_message_includes_age(self):
        """Message includes age in seconds."""
        err = DryRunExpiredError("dry_456", 90.0)
        assert "90" in err.message


class TestErrorDocumentation:
    """Tests for error documentation catalog."""

    def test_get_error_documentation_found(self):
        """Returns documentation for known error code."""
        doc = get_error_documentation(MCPErrorCode.CONTENT_NOT_FOUND.value)
        assert doc is not None
        assert doc.code == MCPErrorCode.CONTENT_NOT_FOUND
        assert doc.category == "validation"
        assert len(doc.causes) > 0
        assert len(doc.remediation) > 0

    def test_get_error_documentation_not_found(self):
        """Returns None for unknown error code."""
        doc = get_error_documentation("UNKNOWN_CODE")
        assert doc is None

    def test_catalog_has_common_errors(self):
        """Catalog includes common error types."""
        assert MCPErrorCode.CONTENT_NOT_FOUND.value in ERROR_CATALOG
        assert MCPErrorCode.MULTIPLE_MATCHES.value in ERROR_CATALOG
        assert MCPErrorCode.HASH_MISMATCH.value in ERROR_CATALOG
        assert MCPErrorCode.INVALID_RANGE.value in ERROR_CATALOG
        assert MCPErrorCode.FILE_NOT_FOUND.value in ERROR_CATALOG
        assert MCPErrorCode.HOOK_FAILED.value in ERROR_CATALOG
