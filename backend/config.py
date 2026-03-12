"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


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


def load_config(path: Path | None = None) -> TowerConfig:
    """Load Tower configuration from a YAML file."""
    if path is None:
        path = Path.home() / ".tower" / "config.yaml"

    if not path.exists():
        return TowerConfig()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    # TODO: full config parsing and validation
    config = TowerConfig()
    if "repos" in raw:
        config.repos = raw["repos"]

    return config
