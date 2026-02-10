"""Pydantic configuration models with env var support.

Configuration Hierarchy (highest to lowest precedence):
1. Direct kwargs to load_config()
2. Environment variables (CODEPLANE__SECTION__KEY)
3. Repo YAML (.codeplane/config.yaml)
4. Global YAML (~/.config/codeplane/config.yaml)
5. Built-in defaults (this file)

Environment Variable Format:
    CODEPLANE__<SECTION>__<KEY>=<VALUE>

Examples:
    CODEPLANE__LOGGING__LEVEL=DEBUG
    CODEPLANE__SERVER__PORT=8080
    CODEPLANE__LIMITS__SEARCH_DEFAULT=50
    CODEPLANE__INDEXER__MAX_WORKERS=4

See .codeplane/config.template.yaml for documented configuration options.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogOutputConfig(BaseModel):
    """Single logging output configuration.

    Env vars: Not directly configurable via env (use YAML for multi-output).
    """

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
    """Logging configuration.

    Env vars:
        CODEPLANE__LOGGING__LEVEL: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """

    level: LogLevel = Field(
        default="INFO",
        description="Root log level. DEBUG is verbose and may impact performance.",
    )
    outputs: list[LogOutputConfig] = Field(default_factory=lambda: [LogOutputConfig()])


class ServerConfig(BaseModel):
    """Server configuration.

    Env vars:
        CODEPLANE__SERVER__HOST: Bind address (default: 127.0.0.1)
        CODEPLANE__SERVER__PORT: Port number (default: 7654)
        CODEPLANE__SERVER__POLL_INTERVAL_SEC: File watcher poll interval
        CODEPLANE__SERVER__DEBOUNCE_SEC: Change debounce window
    """

    host: str = Field(
        default="127.0.0.1",
        description="Bind address. Use 0.0.0.0 for network access (security risk).",
    )
    port: int = Field(
        default=7654,
        description="Server port. Ensure firewall rules if exposing.",
    )
    shutdown_timeout_sec: int = Field(
        default=5,
        description="Graceful shutdown timeout before force-kill.",
    )
    poll_interval_sec: float = Field(
        default=1.0,
        description="File watcher polling interval for cross-filesystem mounts (WSL /mnt/*). "
        "Lower values increase CPU usage but detect changes faster.",
    )
    debounce_sec: float = Field(
        default=0.3,
        description="Debounce window before triggering reindex. "
        "Lower values may cause excessive reindexing during rapid edits.",
    )

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (0 <= v <= 65535):
            raise ValueError(f"Port must be 0-65535, got {v}")
        return v


class IndexConfig(BaseModel):
    """Index configuration.

    Env vars:
        CODEPLANE__INDEX__MAX_FILE_SIZE_MB: Skip files larger than this
        CODEPLANE__INDEX__INDEX_PATH: Override index storage location
    """

    max_file_size_mb: int = Field(
        default=10,
        description="Skip files larger than this (MB). Large files slow indexing. "
        "RISK: Setting too high may cause memory issues with binary files.",
    )
    excluded_extensions: list[str] = Field(
        default_factory=lambda: [".min.js", ".min.css", ".map"],
        description="File extensions to exclude from indexing.",
    )
    index_path: str | None = Field(
        default=None,
        description="Override index storage location. Use for WSL cross-filesystem "
        "performance (store index on native FS). Default: .codeplane/ in repo.",
    )


class TimeoutsConfig(BaseModel):
    """Timeout configuration for daemon components.

    Env vars:
        CODEPLANE__TIMEOUTS__EPOCH_AWAIT_SEC: Max wait for epoch freshness
        CODEPLANE__TIMEOUTS__SESSION_IDLE_SEC: Session idle timeout
    """

    server_stop_sec: float = Field(
        default=5.0,
        description="Server shutdown timeout.",
    )
    force_exit_sec: float = Field(
        default=3.0,
        description="Force exit timeout after graceful shutdown fails.",
    )
    watcher_stop_sec: float = Field(
        default=2.0,
        description="File watcher shutdown timeout.",
    )
    epoch_await_sec: float = Field(
        default=5.0,
        description="Max wait for index freshness after changes. "
        "RISK: Too low may return stale results; too high delays responses.",
    )
    session_idle_sec: float = Field(
        default=1800.0,
        description="Session idle timeout (30 min default). After this, session state is cleared.",
    )
    dry_run_ttl_sec: float = Field(
        default=60.0,
        description="TTL for dry-run refactoring previews.",
    )


class IndexerConfig(BaseModel):
    """Background indexer configuration.

    Env vars:
        CODEPLANE__INDEXER__DEBOUNCE_SEC: Indexer debounce window
        CODEPLANE__INDEXER__MAX_WORKERS: Parallel indexing workers
        CODEPLANE__INDEXER__QUEUE_MAX_SIZE: Max queued paths before dropping
    """

    debounce_sec: float = Field(
        default=0.5,
        description="Indexer-level debounce. Combined with watcher debounce for defense-in-depth.",
    )
    max_workers: int = Field(
        default=1,
        description="Parallel indexing workers. "
        "RISK: >1 may cause SQLite contention; recommended for SSDs only.",
    )
    queue_max_size: int = Field(
        default=10000,
        description="Max queued file paths. Excess paths are dropped (logged). "
        "RISK: Too low loses changes during bulk operations.",
    )


class LimitsConfig(BaseModel):
    """Pagination and query limit defaults.

    These are DEFAULT values - tools may allow per-request overrides up to max.
    See constants.py for hard maximums that cannot be exceeded.

    Env vars:
        CODEPLANE__LIMITS__SEARCH_DEFAULT: Default search results
        CODEPLANE__LIMITS__MAP_DEPTH_DEFAULT: Default repo map depth
        CODEPLANE__LIMITS__MAP_LIMIT_DEFAULT: Default repo map entries
        CODEPLANE__LIMITS__FILES_LIST_DEFAULT: Default file list entries
        CODEPLANE__LIMITS__GIT_LOG_DEFAULT: Default git log entries
        CODEPLANE__LIMITS__GIT_BLAME_DEFAULT: Default git blame lines
    """

    search_default: int = Field(
        default=20,
        description="Default search results. Industry standard: 10-50. "
        "TRADEOFF: Higher values increase response size and latency.",
    )
    map_depth_default: int = Field(
        default=3,
        description="Default repo map tree depth. "
        "TRADEOFF: Deeper = more complete but larger responses.",
    )
    map_limit_default: int = Field(
        default=100,
        description="Default repo map entries. "
        "TRADEOFF: More entries = larger context but may overwhelm agents.",
    )
    files_list_default: int = Field(
        default=200,
        description="Default file listing limit.",
    )
    git_log_default: int = Field(
        default=50,
        description="Default git log commit count.",
    )
    git_blame_default: int = Field(
        default=100,
        description="Default git blame line count.",
    )
    operation_records_max: int = Field(
        default=1000,
        description="Max ledger operation records to return.",
    )


class TestingConfig(BaseModel):
    """Testing subsystem configuration.

    Env vars:
        CODEPLANE__TESTING__DEFAULT_PARALLELISM: Parallel test workers
        CODEPLANE__TESTING__DEFAULT_TIMEOUT_SEC: Test execution timeout
    """

    default_parallelism: int = Field(
        default=4,
        description="Default parallel test workers. Adjust based on CPU cores.",
    )
    default_timeout_sec: int = Field(
        default=300,
        description="Default test timeout (5 min). "
        "RISK: Too low may kill slow integration tests; too high wastes CI time.",
    )


class TelemetryConfig(BaseModel):
    """OpenTelemetry configuration.

    Env vars:
        CODEPLANE__TELEMETRY__ENABLED: Enable/disable telemetry
        CODEPLANE__TELEMETRY__OTLP_ENDPOINT: OTLP collector endpoint
        CODEPLANE__TELEMETRY__SERVICE_NAME: Service name for traces

    Note: Also respects standard OTEL_* env vars when enabled.
    """

    enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry. Set to true and configure endpoint to activate.",
    )
    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP collector endpoint (e.g., http://localhost:4317). "
        "Required when enabled=true.",
    )
    service_name: str = Field(
        default="codeplane",
        description="Service name for traces/metrics.",
    )


class DatabaseConfig(BaseModel):
    """Database connection configuration.

    Env vars:
        CODEPLANE__DATABASE__BUSY_TIMEOUT_MS: SQLite busy timeout
        CODEPLANE__DATABASE__MAX_RETRIES: Max retry attempts for locked DB
        CODEPLANE__DATABASE__POOL_SIZE: Connection pool size
        CODEPLANE__DATABASE__CHECKPOINT_INTERVAL: Transactions between checkpoints
    """

    busy_timeout_ms: int = Field(
        default=30000,
        description="SQLite busy timeout (ms). How long to wait for locks. "
        "RISK: Too low causes failures under contention; too high delays errors.",
    )
    max_retries: int = Field(
        default=3,
        description="Max retry attempts for locked database errors.",
    )
    retry_base_delay_sec: float = Field(
        default=0.1,
        description="Base delay between retries (exponential backoff).",
    )
    pool_size: int = Field(
        default=5,
        description="Connection pool size. Higher values use more memory. "
        "RISK: SQLite handles concurrency differently than client-server DBs.",
    )
    checkpoint_interval: int = Field(
        default=1000,
        description="Transactions between WAL checkpoints. "
        "TRADEOFF: Lower = more frequent I/O; higher = larger WAL files.",
    )


class DebugConfig(BaseModel):
    """Debug and development configuration.

    Env vars:
        CODEPLANE__DEBUG__ENABLED: Enable debug mode
        CODEPLANE__DEBUG__VERBOSE_ERRORS: Include stack traces in error responses
    """

    enabled: bool = Field(
        default=False,
        description="Enable debug mode. Increases logging verbosity significantly.",
    )
    verbose_errors: bool = Field(
        default=False,
        description="Include stack traces in MCP error responses. "
        "SECURITY RISK: May leak sensitive path/code information.",
    )


class CodePlaneConfig(BaseModel):
    """Root configuration for CodePlane.

    All settings can be configured via:
    1. Environment variables: CODEPLANE__SECTION__KEY
    2. YAML config files (repo or global)
    3. Direct kwargs to load_config()

    See .codeplane/config.template.yaml for full documentation.
    """

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    indexer: IndexerConfig = Field(default_factory=IndexerConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    testing: TestingConfig = Field(default_factory=TestingConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
