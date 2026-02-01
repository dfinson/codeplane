"""Pydantic configuration models."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogOutputConfig(BaseModel):
    """Single logging output."""

    format: Literal["json", "console"] = "console"
    destination: str = "stderr"  # stderr, stdout, or absolute file path
    level: LogLevel | None = None  # Inherits from parent if None

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, v: str) -> str:
        if v in ("stderr", "stdout"):
            return v
        path = Path(v).expanduser()
        if not path.is_absolute():
            raise ValueError(f"File destination must be absolute path: {v}")
        return str(path)


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: LogLevel = Field(default="INFO")
    outputs: list[LogOutputConfig] = Field(default_factory=lambda: [LogOutputConfig()])


class ServerConfig(BaseModel):
    """Server configuration."""

    host: str = "127.0.0.1"
    port: int = 7654
    shutdown_timeout_sec: int = 5

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (0 <= v <= 65535):
            raise ValueError(f"Port must be 0-65535, got {v}")
        return v


class IndexConfig(BaseModel):
    """Index configuration."""

    max_file_size_mb: int = 10
    excluded_extensions: list[str] = Field(default_factory=lambda: [".min.js", ".min.css", ".map"])
    index_path: str | None = None  # Override index storage location (for WSL/cross-fs)


class CodePlaneConfig(BaseModel):
    """Root configuration."""

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
