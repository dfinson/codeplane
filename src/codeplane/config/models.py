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
    poll_interval_sec: float = 1.0  # File watcher polling interval (cross-filesystem)
    debounce_sec: float = 0.3  # Debounce interval before triggering reindex

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


class TimeoutsConfig(BaseModel):
    """Timeout configuration for daemon components."""

    server_stop_sec: float = 5.0
    force_exit_sec: float = 3.0
    watcher_stop_sec: float = 2.0
    epoch_await_sec: float = 5.0
    session_idle_sec: float = 1800.0  # 30 minutes
    dry_run_ttl_sec: float = 60.0


class IndexerConfig(BaseModel):
    """Background indexer configuration."""

    debounce_sec: float = 0.5
    max_workers: int = 1
    queue_max_size: int = 10000


class LimitsConfig(BaseModel):
    """Pagination and query limit defaults."""

    search_default: int = 20
    map_depth_default: int = 3
    map_limit_default: int = 100
    files_list_default: int = 200
    git_log_default: int = 50
    git_blame_default: int = 100
    indexed_files_max: int = 1000
    operation_records_max: int = 1000


class TestingConfig(BaseModel):
    """Testing subsystem configuration."""

    default_parallelism: int = 4
    default_timeout_sec: int = 300


class CodePlaneConfig(BaseModel):
    """Root configuration."""

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    indexer: IndexerConfig = Field(default_factory=IndexerConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    testing: TestingConfig = Field(default_factory=TestingConfig)
