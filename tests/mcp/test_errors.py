"""Tests for MCP errors module."""

from codeplane.mcp.errors import (
    ERROR_CATALOG,
    BudgetExceededError,
    ConfirmationRequiredError,
    DryRunExpiredError,
    DryRunRequiredError,
    ErrorResponse,
    FileHashMismatchError,
    HashMismatchError,
    HookFailedError,
    InvalidRangeError,
    MCPError,
    MCPErrorCode,
    SpanOverlapError,
    get_error_documentation,
)


class TestMCPErrorCode:
    """Tests for MCPErrorCode enum."""

    def test_all_codes_have_unique_values(self) -> None:
        """All error codes have unique string values."""
        values = [code.value for code in MCPErrorCode]
        assert len(values) == len(set(values))

    def test_common_codes_exist(self) -> None:
        """Common error codes are defined."""
        assert hasattr(MCPErrorCode, "INTERNAL_ERROR")
        assert hasattr(MCPErrorCode, "INVALID_RANGE")
        assert hasattr(MCPErrorCode, "FILE_NOT_FOUND")
        assert hasattr(MCPErrorCode, "PERMISSION_DENIED")

    def test_mutation_codes_exist(self) -> None:
        """Mutation-related error codes are defined."""
        assert hasattr(MCPErrorCode, "HASH_MISMATCH")

    def test_range_code_exists(self) -> None:
        """Range-related error code is defined."""
        assert hasattr(MCPErrorCode, "INVALID_RANGE")


class TestErrorResponse:
    """Tests for ErrorResponse dataclass."""

    def test_create_minimal(self) -> None:
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

    def test_create_with_context(self) -> None:
        """Create ErrorResponse with context."""
        resp = ErrorResponse(
            code=MCPErrorCode.FILE_NOT_FOUND,
            message="File not found",
            remediation="Check the path",
            context={"path": "missing.py"},
        )
        assert resp.context == {"path": "missing.py"}

    def test_to_dict(self) -> None:
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

    def test_create_basic(self) -> None:
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

    def test_create_with_context(self) -> None:
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

    def test_str_representation(self) -> None:
        """String representation includes message."""
        err = MCPError(
            code=MCPErrorCode.INVALID_RANGE,
            message="Bad input",
            remediation="Fix",
        )
        s = str(err)
        assert "Bad input" in s

    def test_to_response(self) -> None:
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


class TestInvalidRangeError:
    """Tests for InvalidRangeError."""

    def test_creates_with_correct_code(self) -> None:
        """Uses INVALID_RANGE error code."""
        err = InvalidRangeError("test.py", start=100, end=50, line_count=200)
        assert err.code == MCPErrorCode.INVALID_RANGE

    def test_has_remediation(self) -> None:
        """Error has remediation hint."""
        err = InvalidRangeError("f.py", start=1, end=1000, line_count=100)
        assert err.remediation is not None


class TestHashMismatchError:
    """Tests for HashMismatchError."""

    def test_creates_with_correct_code(self) -> None:
        """Uses HASH_MISMATCH error code."""
        err = HashMismatchError("test.py", expected="abc123", actual="def456")
        assert err.code == MCPErrorCode.HASH_MISMATCH

    def test_has_path(self) -> None:
        """Error includes path."""
        err = HashMismatchError("f.py", expected="aaa", actual="bbb")
        assert err.path == "f.py"


class TestHookFailedError:
    """Tests for HookFailedError."""

    def test_creates_with_correct_code(self) -> None:
        """Uses HOOK_FAILED error code."""
        err = HookFailedError("pre-commit", exit_code=1, stdout="", stderr="lint failed")
        assert err.code == MCPErrorCode.HOOK_FAILED

    def test_context_contain_hook_info(self) -> None:
        """Context includes hook type and exit code."""
        err = HookFailedError("post-save", exit_code=2, stdout="", stderr="error msg")
        assert err.context.get("hook_type") == "post-save"
        assert err.context.get("exit_code") == 2


class TestDryRunRequiredError:
    """Tests for DryRunRequiredError."""

    def test_creates_with_correct_code(self) -> None:
        """Uses DRY_RUN_REQUIRED error code."""
        err = DryRunRequiredError("test.py")
        assert err.code == MCPErrorCode.DRY_RUN_REQUIRED

    def test_message_includes_path(self) -> None:
        """Message includes file path."""
        err = DryRunRequiredError("src/main.py")
        assert "src/main.py" in err.message


class TestDryRunExpiredError:
    """Tests for DryRunExpiredError."""

    def test_creates_with_correct_code(self) -> None:
        """Uses DRY_RUN_EXPIRED error code."""
        err = DryRunExpiredError("dry_123", 120.5)
        assert err.code == MCPErrorCode.DRY_RUN_EXPIRED

    def test_message_includes_age(self) -> None:
        """Message includes age in seconds."""
        err = DryRunExpiredError("dry_456", 90.0)
        assert "90" in err.message


class TestErrorDocumentation:
    """Tests for error documentation catalog."""

    def test_get_error_documentation_found(self) -> None:
        """Returns documentation for known error code."""
        doc = get_error_documentation(MCPErrorCode.HASH_MISMATCH.value)
        assert doc is not None
        assert doc.code == MCPErrorCode.HASH_MISMATCH
        assert doc.category == "state"
        assert len(doc.causes) > 0
        assert len(doc.remediation) > 0

    def test_get_error_documentation_not_found(self) -> None:
        """Returns None for unknown error code."""
        doc = get_error_documentation("UNKNOWN_CODE")
        assert doc is None

    def test_catalog_has_common_errors(self) -> None:
        """Catalog includes common error types."""
        assert MCPErrorCode.HASH_MISMATCH.value in ERROR_CATALOG
        assert MCPErrorCode.INVALID_RANGE.value in ERROR_CATALOG
        assert MCPErrorCode.FILE_NOT_FOUND.value in ERROR_CATALOG
        assert MCPErrorCode.HOOK_FAILED.value in ERROR_CATALOG
        assert MCPErrorCode.HOOK_FAILED.value in ERROR_CATALOG


class TestBudgetExceededError:
    """Tests for BudgetExceededError."""

    def test_creates_with_correct_code(self) -> None:
        err = BudgetExceededError("scope-1", "reads", "Reduce read volume")
        assert err.code == MCPErrorCode.BUDGET_EXCEEDED

    def test_message_includes_scope_and_counter(self) -> None:
        err = BudgetExceededError("my-scope", "writes", "Write less")
        assert "writes" in err.message
        assert "my-scope" in err.message

    def test_remediation_from_hint(self) -> None:
        err = BudgetExceededError("s", "c", "Do something else")
        assert err.remediation == "Do something else"


class TestSpanOverlapError:
    """Tests for SpanOverlapError."""

    def test_creates_with_correct_code(self) -> None:
        err = SpanOverlapError("file.py", [{"a": 1}])
        assert err.code == MCPErrorCode.SPAN_OVERLAP

    def test_message_includes_path(self) -> None:
        err = SpanOverlapError("src/foo.py", [])
        assert "src/foo.py" in err.message

    def test_context_has_conflicts(self) -> None:
        conflicts = [{"start": 1, "end": 10}]
        err = SpanOverlapError("f.py", conflicts)
        assert err.context.get("conflicts") == conflicts


class TestFileHashMismatchError:
    """Tests for FileHashMismatchError."""

    def test_creates_with_correct_code(self) -> None:
        err = FileHashMismatchError("f.py", expected="aaa", actual="bbb")
        assert err.code == MCPErrorCode.FILE_HASH_MISMATCH

    def test_message_includes_path(self) -> None:
        err = FileHashMismatchError("src/x.py", expected="a", actual="b")
        assert "src/x.py" in err.message

    def test_context_has_hashes(self) -> None:
        err = FileHashMismatchError("f.py", expected="abc", actual="def")
        assert err.context.get("expected_file_sha256") == "abc"
        assert err.context.get("current_file_sha256") == "def"


class TestConfirmationRequiredError:
    """Tests for ConfirmationRequiredError."""

    def test_creates_with_correct_code(self) -> None:
        err = ConfirmationRequiredError("Need confirm", "tok_123")
        assert err.code == MCPErrorCode.CONFIRMATION_REQUIRED

    def test_message_and_token(self) -> None:
        err = ConfirmationRequiredError("Please confirm", "tok_abc")
        assert err.message == "Please confirm"
        assert err.context.get("confirmation_token") == "tok_abc"

    def test_details_merged_into_context(self) -> None:
        err = ConfirmationRequiredError("Confirm it", "tok_1", details={"reason": "destructive"})
        assert err.context.get("reason") == "destructive"

    def test_reserved_keys_filtered_from_details(self) -> None:
        err = ConfirmationRequiredError(
            "Confirm", "tok_2", details={"code": "STEAL", "extra": "ok"}
        )
        # "code" is reserved and should be filtered
        assert err.context.get("extra") == "ok"
        assert err.context.get("code") != "STEAL"

    def test_none_details(self) -> None:
        err = ConfirmationRequiredError("Confirm", "tok_3", details=None)
        assert err.code == MCPErrorCode.CONFIRMATION_REQUIRED
