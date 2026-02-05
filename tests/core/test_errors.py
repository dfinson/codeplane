"""Tests for error types and codes."""

import pytest

from codeplane.core.errors import (
    CodePlaneError,
    ConfigError,
    InternalError,
    InternalErrorCode,
)


class TestInternalErrorCode:
    """Error code value tests."""

    @pytest.mark.parametrize(
        ("code", "expected_range"),
        [
            (InternalErrorCode.CONFIG_PARSE_ERROR, 2000),
            (InternalErrorCode.CONFIG_INVALID_VALUE, 2000),
            (InternalErrorCode.INTERNAL_ERROR, 9000),
            (InternalErrorCode.INTERNAL_TIMEOUT, 9000),
        ],
    )
    def test_given_error_code_when_checked_then_in_correct_range(
        self, code: InternalErrorCode, expected_range: int
    ) -> None:
        """Error codes fall within their designated numeric range."""
        # Given
        error_code = code

        # When
        value = error_code.value

        # Then
        assert expected_range <= value < expected_range + 1000


class TestCodePlaneError:
    """Base error behavior tests."""

    def test_given_error_when_to_dict_then_serializes_all_fields(self) -> None:
        """Error serializes to dict with all required fields."""
        # Given
        error = CodePlaneError(
            code=InternalErrorCode.CONFIG_PARSE_ERROR,
            message="Test message",
            retryable=True,
            details={"key": "value"},
        )

        # When
        result = error.to_dict()

        # Then
        assert result == {
            "code": 2001,
            "error": "CONFIG_PARSE_ERROR",
            "message": "Test message",
            "retryable": True,
            "details": {"key": "value"},
        }

    def test_given_error_when_str_then_human_readable(self) -> None:
        """Error string representation is human readable."""
        # Given
        error = CodePlaneError(
            code=InternalErrorCode.INTERNAL_ERROR,
            message="Something broke",
        )

        # When
        result = str(error)

        # Then
        assert result == "[9001] INTERNAL_ERROR: Something broke"


class TestConfigError:
    """ConfigError factory method tests."""

    @pytest.mark.parametrize(
        ("factory", "kwargs", "expected_code"),
        [
            (
                "parse_error",
                {"path": "/foo", "reason": "bad yaml"},
                InternalErrorCode.CONFIG_PARSE_ERROR,
            ),
            (
                "invalid_value",
                {"field": "port", "value": -1, "reason": "negative"},
                InternalErrorCode.CONFIG_INVALID_VALUE,
            ),
            ("missing_required", {"field": "api_key"}, InternalErrorCode.CONFIG_MISSING_REQUIRED),
            ("file_not_found", {"path": "/missing"}, InternalErrorCode.CONFIG_FILE_NOT_FOUND),
        ],
    )
    def test_given_factory_when_called_then_correct_code(
        self, factory: str, kwargs: dict[str, object], expected_code: InternalErrorCode
    ) -> None:
        """Factory methods produce errors with correct error codes."""
        # Given
        factory_method = getattr(ConfigError, factory)

        # When
        error = factory_method(**kwargs)

        # Then
        assert error.code == expected_code

    def test_given_parse_error_when_created_then_path_in_details(self) -> None:
        """Parse error includes file path in details."""
        # Given
        path = "/config.yaml"
        reason = "invalid syntax"

        # When
        error = ConfigError.parse_error(path, reason)

        # Then
        assert error.details["path"] == path
        assert reason in error.message


class TestInternalError:
    """InternalError tests."""

    def test_given_unexpected_error_when_created_then_includes_extras(self) -> None:
        """Unexpected error captures arbitrary extra details."""
        # Given
        message = "boom"
        extras = {"foo": "bar", "count": 42}

        # When
        error = InternalError.unexpected(message, **extras)

        # Then
        assert error.details == extras
        assert error.code == InternalErrorCode.INTERNAL_ERROR
