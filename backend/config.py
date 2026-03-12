"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TOWER_DIR = Path.home() / ".tower"
DEFAULT_CONFIG_PATH = TOWER_DIR / "config.yaml"
DEFAULT_DB_PATH = TOWER_DIR / "data.db"

DEFAULT_CONFIG_YAML = """\
server:
  host: 127.0.0.1
  port: 8080

runtime:
  max_concurrent_jobs: 2
  worktrees_dirname: .tower-worktrees

voice:
  enabled: true
  model: base.en
  max_audio_size_mb: 10

retention:
  artifact_retention_days: 30
  max_artifact_size_mb: 100
  cleanup_on_startup: false

logging:
  level: info
  file: ~/.tower/logs/server.log
  max_file_size_mb: 50
  backup_count: 3

rate_limits:
  max_sse_connections: 5

repos: []
"""


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class RuntimeConfig:
    max_concurrent_jobs: int = 2
    worktrees_dirname: str = ".tower-worktrees"


@dataclass
class VoiceConfig:
    enabled: bool = True
    model: str = "base.en"
    max_audio_size_mb: int = 10


@dataclass
class RetentionConfig:
    artifact_retention_days: int = 30
    max_artifact_size_mb: int = 100
    cleanup_on_startup: bool = False


@dataclass
class LoggingConfig:
    level: str = "info"
    file: str = "~/.tower/logs/server.log"
    max_file_size_mb: int = 50
    backup_count: int = 3


@dataclass
class RateLimitConfig:
    max_sse_connections: int = 5


@dataclass
class TowerConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    rate_limits: RateLimitConfig = field(default_factory=RateLimitConfig)
    repos: list[str] = field(default_factory=list)


def _parse_section(raw: dict[str, Any], cls: type, key: str) -> Any:
    """Parse a config section dict into a dataclass instance."""
    section = raw.get(key, {})
    if not isinstance(section, dict):
        return cls()
    # Only pass keys that the dataclass accepts
    valid_keys = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in section.items() if k in valid_keys})


def load_config(path: Path | None = None) -> TowerConfig:
    """Load Tower configuration from a YAML file."""
    if path is None:
        path = DEFAULT_CONFIG_PATH

    if not path.exists():
        return TowerConfig()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    return TowerConfig(
        server=_parse_section(raw, ServerConfig, "server"),
        runtime=_parse_section(raw, RuntimeConfig, "runtime"),
        voice=_parse_section(raw, VoiceConfig, "voice"),
        retention=_parse_section(raw, RetentionConfig, "retention"),
        logging=_parse_section(raw, LoggingConfig, "logging"),
        rate_limits=_parse_section(raw, RateLimitConfig, "rate_limits"),
        repos=raw.get("repos", []),
    )


def init_config(path: Path | None = None) -> Path:
    """Create the default config file. Returns the path written."""
    if path is None:
        path = DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_YAML)
    return path
