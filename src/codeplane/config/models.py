"""Configuration models with Pydantic validation."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _validate_log_level(v: str) -> str:
    """Validate and normalize log level."""
    valid = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR"}
    if v.upper() not in valid:
        raise ValueError(f"Invalid log level: {v}. Must be one of {valid}")
    return v.upper()


class LogOutputConfig(BaseModel):
    """Single logging output configuration."""

    format: Literal["json", "console"] = Field(default="console", description="Output format")
    destination: str = Field(default="stderr", description="stderr, stdout, or file path")
    level: str | None = Field(
        default=None, description="Level override (inherits from parent if None)"
    )

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _validate_log_level(v)

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
    """Logging configuration with multi-output support."""

    level: str = Field(default="INFO", description="Default log level")
    outputs: list[LogOutputConfig] = Field(
        default_factory=lambda: [LogOutputConfig()],
        description="Output destinations (default: console to stderr)",
    )

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        return _validate_log_level(v)

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_json_format(cls, data: Any) -> Any:
        """Backwards compatibility: convert json_format=True to outputs config."""
        if isinstance(data, dict) and "json_format" in data:
            json_format = data.pop("json_format")
            if "outputs" not in data:
                fmt = "json" if json_format else "console"
                data["outputs"] = [{"format": fmt}]
        return data


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
    def with_overrides(
        cls, base: "CodePlaneConfig", overrides: dict[str, Any]
    ) -> "CodePlaneConfig":
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
