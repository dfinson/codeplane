"""Configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _resolve_tower_dir() -> Path:
    """Resolve AGENT_TOWER_HOME from env var, falling back to ~/.tower."""
    env = os.environ.get("AGENT_TOWER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".tower"


TOWER_DIR = _resolve_tower_dir()
DEFAULT_CONFIG_PATH = TOWER_DIR / "config.yaml"
DEFAULT_DB_PATH = TOWER_DIR / "data.db"

# Hardcoded constants — not user-configurable
VOICE_ENABLED = True
VOICE_MAX_AUDIO_SIZE_MB = 10
MCP_ENABLED = True
MCP_PATH = "/mcp"

DEFAULT_CONFIG_YAML = """\
server:
  host: 127.0.0.1
  port: 8080

runtime:
  max_concurrent_jobs: 2
  worktrees_dirname: .tower-worktrees

voice:
  model: base.en

retention:
  artifact_retention_days: 30
  max_artifact_size_mb: 100
  cleanup_on_startup: false

logging:
  level: info
  file: ~/.tower/logs/server.log
  max_file_size_mb: 50
  backup_count: 3

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
    permission_mode: str = "permissive"  # permissive | auto | supervised
    utility_model: str = "gpt-4o-mini"  # cheap/fast model for naming, summaries, etc.


@dataclass
class VoiceConfig:
    model: str = "base.en"


@dataclass
class RetentionConfig:
    artifact_retention_days: int = 30
    max_artifact_size_mb: int = 100
    cleanup_on_startup: bool = False
    auto_archive_days: int = 7


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
class CompletionConfig:
    strategy: str = "auto_merge"  # auto_merge | pr_only | manual
    auto_push: bool = True
    cleanup_worktree: bool = True
    delete_branch_after_merge: bool = True


@dataclass
class PlatformConfig:
    """Per-platform auth and repo binding configuration."""

    auth: str = "cli"  # cli | token
    repos: list[str] = field(default_factory=list)  # repo paths bound to this platform


@dataclass
class TowerConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    rate_limits: RateLimitConfig = field(default_factory=RateLimitConfig)
    completion: CompletionConfig = field(default_factory=CompletionConfig)
    platforms: dict[str, PlatformConfig] = field(default_factory=dict)
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

    # Parse platforms section: platforms.<name>.{auth, repos}
    platforms: dict[str, PlatformConfig] = {}
    raw_platforms = raw.get("platforms", {})
    if isinstance(raw_platforms, dict):
        for pname, pdata in raw_platforms.items():
            if isinstance(pdata, dict):
                platforms[str(pname)] = PlatformConfig(
                    auth=str(pdata.get("auth", "cli")),
                    repos=[str(r) for r in pdata.get("repos", []) if r] if isinstance(pdata.get("repos"), list) else [],
                )

    return TowerConfig(
        server=_parse_section(raw, ServerConfig, "server"),
        runtime=_parse_section(raw, RuntimeConfig, "runtime"),
        voice=_parse_section(raw, VoiceConfig, "voice"),
        retention=_parse_section(raw, RetentionConfig, "retention"),
        logging=_parse_section(raw, LoggingConfig, "logging"),
        rate_limits=_parse_section(raw, RateLimitConfig, "rate_limits"),
        completion=_parse_section(raw, CompletionConfig, "completion"),
        platforms=platforms,
        repos=[str(r) for r in raw.get("repos", []) if r is not None] if isinstance(raw.get("repos", []), list) else [],
    )


def save_config(config: TowerConfig, path: Path | None = None) -> None:
    """Persist the current TowerConfig back to the YAML config file.

    Non-destructive: loads the existing file first and merges changes in,
    preserving any keys or repos that might exist.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH

    # Load existing config to preserve unknown keys
    existing: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            existing = yaml.safe_load(f) or {}

    # Merge our known sections — existing keys not in our model are preserved
    existing["server"] = {"host": config.server.host, "port": config.server.port}
    existing["runtime"] = {
        "max_concurrent_jobs": config.runtime.max_concurrent_jobs,
        "worktrees_dirname": config.runtime.worktrees_dirname,
        "permission_mode": config.runtime.permission_mode,
        "utility_model": config.runtime.utility_model,
    }
    existing["voice"] = {
        "model": config.voice.model,
    }
    existing["retention"] = {
        "artifact_retention_days": config.retention.artifact_retention_days,
        "max_artifact_size_mb": config.retention.max_artifact_size_mb,
        "cleanup_on_startup": config.retention.cleanup_on_startup,
        "auto_archive_days": config.retention.auto_archive_days,
    }
    existing["logging"] = {
        "level": config.logging.level,
        "file": config.logging.file,
        "max_file_size_mb": config.logging.max_file_size_mb,
        "backup_count": config.logging.backup_count,
    }
    existing["rate_limits"] = {
        "max_sse_connections": config.rate_limits.max_sse_connections,
    }
    existing["completion"] = {
        "strategy": config.completion.strategy,
        "auto_push": config.completion.auto_push,
        "cleanup_worktree": config.completion.cleanup_worktree,
        "delete_branch_after_merge": config.completion.delete_branch_after_merge,
    }
    existing["repos"] = config.repos
    if config.platforms:
        existing["platforms"] = {name: {"auth": pc.auth, "repos": pc.repos} for name, pc in config.platforms.items()}

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(existing, f, default_flow_style=False, sort_keys=False)


def register_repo(config: TowerConfig, repo_path: str, config_path: Path | None = None) -> str:
    """Add a repo path to the allowlist if not already present.

    Returns the resolved path that was added.
    """
    resolved = str(Path(repo_path).expanduser().resolve())
    if resolved not in config.repos:
        config.repos.append(resolved)
        save_config(config, config_path)
    return resolved


def unregister_repo(config: TowerConfig, repo_path: str, config_path: Path | None = None) -> str:
    """Remove a repo path from the allowlist.

    Returns the resolved path that was removed.
    Raises ValueError if the repo is not in the allowlist.
    """
    resolved = str(Path(repo_path).expanduser().resolve())
    if resolved in config.repos:
        config.repos.remove(resolved)
        save_config(config, config_path)
        return resolved
    # Also try matching the original string
    if repo_path in config.repos:
        config.repos.remove(repo_path)
        save_config(config, config_path)
        return repo_path
    raise ValueError(f"Repository '{repo_path}' is not in the allowlist.")


def init_config(path: Path | None = None) -> Path:
    """Create the default config file if it doesn't already exist.

    Non-destructive: never overwrites an existing config.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_YAML)
    return path
