"""Configuration loading with precedence: defaults → global → repo → env → overrides."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from codeplane.config.models import (
    GLOBAL_CONFIG_PATH,
    REPO_CONFIG_PATH,
    CodePlaneConfig,
)
from codeplane.core.errors import ConfigError


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file, returning empty dict if not found."""
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError.parse_error(str(path), str(e)) from e


def _env_overrides() -> dict[str, Any]:
    """Extract config overrides from CODEPLANE_* environment variables.

    Format: CODEPLANE_LOGGING_LEVEL=DEBUG → {'logging.level': 'DEBUG'}
    """
    prefix = "CODEPLANE_"
    overrides: dict[str, Any] = {}
    for key, value in os.environ.items():
        if key.startswith(prefix):
            # Convert CODEPLANE_LOGGING_LEVEL → logging.level
            config_key = key[len(prefix) :].lower().replace("_", ".")
            overrides[config_key] = value
    return overrides


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(
    repo_root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> CodePlaneConfig:
    """Load configuration with full precedence chain.

    Precedence (lowest to highest):
    1. Built-in defaults
    2. Global config (~/.config/codeplane/config.yaml)
    3. Repo config (.codeplane/config.yaml)
    4. Environment variables (CODEPLANE_*)
    5. Explicit overrides

    Args:
        repo_root: Repository root directory. If None, uses cwd.
        overrides: Explicit overrides (highest precedence).

    Returns:
        Validated CodePlaneConfig.

    Raises:
        ConfigError: On parse or validation errors.
    """
    repo_root = repo_root or Path.cwd()

    # Layer 1: defaults (implicit in Pydantic model)
    merged: dict[str, Any] = {}

    # Layer 2: global config
    if GLOBAL_CONFIG_PATH.exists():
        merged = _deep_merge(merged, _load_yaml(GLOBAL_CONFIG_PATH))

    # Layer 3: repo config
    repo_config_path = repo_root / REPO_CONFIG_PATH
    if repo_config_path.exists():
        merged = _deep_merge(merged, _load_yaml(repo_config_path))

    # Layer 4: environment variables
    env_overrides = _env_overrides()

    # Layer 5: explicit overrides
    explicit_overrides = overrides or {}

    # Validate base config first
    try:
        config = CodePlaneConfig.model_validate(merged)
    except ValidationError as e:
        # Extract first error for actionable message
        err = e.errors()[0]
        field = ".".join(str(loc) for loc in err["loc"])
        raise ConfigError.invalid_value(field, err.get("input"), err["msg"]) from e

    # Apply dotted-path overrides
    all_overrides = {**env_overrides, **explicit_overrides}
    if all_overrides:
        try:
            config = CodePlaneConfig.with_overrides(config, all_overrides)
        except ValidationError as e:
            err = e.errors()[0]
            field = ".".join(str(loc) for loc in err["loc"])
            raise ConfigError.invalid_value(field, err.get("input"), err["msg"]) from e

    return config
