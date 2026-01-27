"""CodePlane error types with typed error codes.

Error code ranges (from SPEC.md §4.2):
- 1xxx: Auth
- 2xxx: Config
- 3xxx: Index
- 4xxx: Refactor
- 5xxx: Mutation
- 6xxx: Task
- 7xxx: Test
- 8xxx: LSP
- 9xxx: Internal
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    """Typed error codes for programmatic handling."""

    # Config (2xxx)
    CONFIG_PARSE_ERROR = 2001
    CONFIG_INVALID_VALUE = 2002
    CONFIG_MISSING_REQUIRED = 2003
    CONFIG_FILE_NOT_FOUND = 2004

    # Internal (9xxx)
    INTERNAL_ERROR = 9001
    INTERNAL_TIMEOUT = 9002


@dataclass(frozen=True, slots=True)
class CodePlaneError(Exception):
    """Base error with structured context for MCP responses."""

    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def error_name(self) -> str:
        """String identifier for logging (e.g., 'CONFIG_PARSE_ERROR')."""
        return self.code.name

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON/MCP responses."""
        return {
            "code": self.code.value,
            "error": self.error_name,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.error_name}: {self.message}"


class ConfigError(CodePlaneError):
    """Configuration-related errors."""

    @classmethod
    def parse_error(cls, path: str, reason: str) -> "ConfigError":
        return cls(
            code=ErrorCode.CONFIG_PARSE_ERROR,
            message=f"Failed to parse config at {path}: {reason}",
            details={"path": path, "reason": reason},
        )

    @classmethod
    def invalid_value(cls, field: str, value: Any, reason: str) -> "ConfigError":
        return cls(
            code=ErrorCode.CONFIG_INVALID_VALUE,
            message=f"Invalid value for '{field}': {reason}",
            details={"field": field, "value": str(value), "reason": reason},
        )

    @classmethod
    def missing_required(cls, field: str) -> "ConfigError":
        return cls(
            code=ErrorCode.CONFIG_MISSING_REQUIRED,
            message=f"Missing required config field: {field}",
            details={"field": field},
        )

    @classmethod
    def file_not_found(cls, path: str) -> "ConfigError":
        return cls(
            code=ErrorCode.CONFIG_FILE_NOT_FOUND,
            message=f"Config file not found: {path}",
            details={"path": path},
        )


class InternalError(CodePlaneError):
    """Internal/unexpected errors."""

    @classmethod
    def unexpected(cls, reason: str, **details: Any) -> "InternalError":
        return cls(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"Internal error: {reason}",
            details=details,
        )
