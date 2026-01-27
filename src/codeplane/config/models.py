"""Configuration models with Pydantic validation."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(default="INFO", description="Log level")
    json_format: bool = Field(default=False, description="Output JSON lines")

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR"}
        if v.upper() not in valid:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid}")
        return v.upper()


class DaemonConfig(BaseModel):
    """Daemon configuration."""

    host: str = Field(default="127.0.0.1", description="Bind address")
    port: int = Field(default=0, description="Port (0 = auto-assign)")
    shutdown_timeout_sec: int = Field(default=5, description="Graceful shutdown timeout")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (0 <= v <= 65535):
            raise ValueError(f"Port must be 0-65535, got {v}")
        return v


class IndexConfig(BaseModel):
    """Index configuration."""

    max_file_size_mb: int = Field(default=10, description="Max file size to index")
    excluded_extensions: list[str] = Field(
        default_factory=lambda: [".min.js", ".min.css", ".map"],
        description="Extensions to skip",
    )


class CodePlaneConfig(BaseModel):
    """Root configuration model."""

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)

    @classmethod
    def with_overrides(cls, base: "CodePlaneConfig", overrides: dict[str, Any]) -> "CodePlaneConfig":
        """Create config with dotted-path overrides (e.g., 'logging.level': 'DEBUG')."""
        data = base.model_dump()
        for key, value in overrides.items():
            parts = key.split(".")
            target = data
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        return cls.model_validate(data)


# Default repo config path
REPO_CONFIG_PATH = Path(".codeplane/config.yaml")
GLOBAL_CONFIG_PATH = Path("~/.config/codeplane/config.yaml").expanduser()
