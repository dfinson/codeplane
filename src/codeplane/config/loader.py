"""Configuration loading with pydantic-settings.

Supports loading configuration from multiple sources with precedence:
1. Direct kwargs (highest priority)
2. Environment variables (CODEPLANE__SECTION__KEY)
3. User config (.codeplane/config.yaml) - minimal user-facing options
4. Runtime state (.codeplane/state.yaml) - auto-generated, not user-editable
5. Built-in defaults (lowest priority)

User-facing config (config.yaml) only contains:
- port: Server port
- max_file_size_mb: Max file size for indexing
- log_level: Logging verbosity

Everything else uses opinionated defaults that shouldn't need changing.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from codeplane.config.models import (
    CodePlaneConfig,
    DatabaseConfig,
    DebugConfig,
    IndexConfig,
    IndexerConfig,
    LimitsConfig,
    LoggingConfig,
    ServerConfig,
    TelemetryConfig,
    TestingConfig,
    TimeoutsConfig,
)
from codeplane.config.user_config import (
    load_runtime_state,
    load_user_config,
)
from codeplane.core.errors import ConfigError

GLOBAL_CONFIG_PATH = Path("~/.config/codeplane/config.yaml").expanduser()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError.parse_error(str(path), str(e)) from e


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class _YamlSource(PydanticBaseSettingsSource):
    """Settings source that reads from pre-loaded YAML config."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_config: dict[str, Any]) -> None:
        super().__init__(settings_cls)
        self._yaml_config = yaml_config

    def get_field_value(
        self,
        field: Any,  # noqa: ARG002
        field_name: str,
    ) -> tuple[Any, str, bool]:
        val = self._yaml_config.get(field_name)
        return val, field_name, val is not None

    def __call__(self) -> dict[str, Any]:
        return self._yaml_config


def _make_settings_class(yaml_config: dict[str, Any]) -> type[BaseSettings]:
    """Create a Settings class with instance-based YAML source (thread-safe)."""

    class CodePlaneSettings(BaseSettings):
        """Root config. Env vars: CODEPLANE__LOGGING__LEVEL, CODEPLANE__SERVER__PORT, etc."""

        model_config = SettingsConfigDict(
            env_prefix="CODEPLANE__",
            env_nested_delimiter="__",
            case_sensitive=False,
        )

        logging: LoggingConfig = LoggingConfig()
        server: ServerConfig = ServerConfig()
        index: IndexConfig = IndexConfig()
        timeouts: TimeoutsConfig = TimeoutsConfig()
        indexer: IndexerConfig = IndexerConfig()
        limits: LimitsConfig = LimitsConfig()
        testing: TestingConfig = TestingConfig()
        telemetry: TelemetryConfig = TelemetryConfig()
        database: DatabaseConfig = DatabaseConfig()
        debug: DebugConfig = DebugConfig()

        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
            file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            # Precedence (first wins): init kwargs > env vars > yaml files
            return (init_settings, env_settings, _YamlSource(settings_cls, yaml_config))

    return CodePlaneSettings


# Alias for backward compatibility - use CodePlaneConfig from models for type hints
CodePlaneSettings = _make_settings_class({})


def load_config(repo_root: Path | None = None, **kwargs: Any) -> CodePlaneConfig:
    """Load config: defaults < user config < state < env vars < kwargs.

    Loads user-facing config from .codeplane/config.yaml and runtime state
    from .codeplane/state.yaml, merging them into the full internal config.

    Args:
        repo_root: Repository root to load config from.
                   Defaults to current working directory.
        **kwargs: Override values (highest precedence).

    Returns:
        Fully resolved configuration object.

    Raises:
        ConfigError: On invalid YAML syntax or validation errors.
    """
    repo_root = repo_root or Path.cwd()
    codeplane_dir = repo_root / ".codeplane"

    # Load user config (minimal fields)
    user_config = load_user_config(codeplane_dir / "config.yaml")

    # Load runtime state (index_path, etc.)
    state = load_runtime_state(codeplane_dir / "state.yaml")

    # Build YAML config dict from user config + state
    # Map user config fields to internal config structure
    yaml_config: dict[str, Any] = {
        "server": {"port": user_config.port},
        "index": {"max_file_size_mb": user_config.max_file_size_mb},
        "logging": {"level": user_config.log_level},
    }

    # Add state (index_path)
    if state:
        yaml_config["index"]["index_path"] = state.index_path

    # Load and merge global config if present
    global_config = _load_yaml(GLOBAL_CONFIG_PATH)
    if global_config:
        yaml_config = _deep_merge(global_config, yaml_config)

    settings_cls = _make_settings_class(yaml_config)
    try:
        return settings_cls(**kwargs)  # type: ignore[return-value]
    except ValidationError as e:
        err = e.errors()[0]
        field = ".".join(str(loc) for loc in err["loc"])
        raise ConfigError.invalid_value(field, err.get("input"), err["msg"]) from e


def get_index_paths(repo_root: Path) -> tuple[Path, Path]:
    """Get db_path and tantivy_path for a repo, respecting config.index.index_path."""
    config = load_config(repo_root)
    if config.index.index_path:
        index_dir = Path(config.index.index_path)
    else:
        index_dir = repo_root / ".codeplane"
    return index_dir / "index.db", index_dir / "tantivy"
