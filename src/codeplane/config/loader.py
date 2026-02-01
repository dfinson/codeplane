"""Configuration loading with pydantic-settings."""

from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from codeplane.config.models import IndexConfig, LoggingConfig, ServerConfig
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
        """Root config. Env vars: CODEPLANE__LOGGING__LEVEL, CODEPLANE__DAEMON__PORT, etc."""

        model_config = SettingsConfigDict(
            env_prefix="CODEPLANE__",
            env_nested_delimiter="__",
            case_sensitive=False,
        )

        logging: LoggingConfig = LoggingConfig()
        server: ServerConfig = ServerConfig()
        index: IndexConfig = IndexConfig()

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


# For type hints and direct instantiation without YAML
CodePlaneSettings = _make_settings_class({})
CodePlaneConfig = CodePlaneSettings


def load_config(repo_root: Path | None = None, **kwargs: Any) -> BaseSettings:
    """Load config: defaults < global yaml < repo yaml < env vars < kwargs."""
    repo_root = repo_root or Path.cwd()

    # Load and merge YAML files (global first, repo overrides)
    yaml_config = _load_yaml(GLOBAL_CONFIG_PATH)
    yaml_config = _deep_merge(yaml_config, _load_yaml(repo_root / ".codeplane" / "config.yaml"))

    settings_cls = _make_settings_class(yaml_config)
    try:
        return settings_cls(**kwargs)
    except ValidationError as e:
        err = e.errors()[0]
        field = ".".join(str(loc) for loc in err["loc"])
        raise ConfigError.invalid_value(field, err.get("input"), err["msg"]) from e


def get_index_paths(repo_root: Path) -> tuple[Path, Path]:
    """Get db_path and tantivy_path for a repo, respecting config.index.index_path."""
    from codeplane.config.models import CodePlaneConfig

    config = cast(CodePlaneConfig, load_config(repo_root))
    if config.index.index_path:
        index_dir = Path(config.index.index_path)
    else:
        index_dir = repo_root / ".codeplane"
    return index_dir / "index.db", index_dir / "tantivy"
