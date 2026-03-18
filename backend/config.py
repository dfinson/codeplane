"""Configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _resolve_tower_dir() -> Path:
    """Resolve CODEPLANE_HOME from env var, falling back to ~/.codeplane."""
    env = os.environ.get("CODEPLANE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".codeplane"


CODEPLANE_DIR = _resolve_tower_dir()
DEFAULT_CONFIG_PATH = CODEPLANE_DIR / "config.yaml"
DEFAULT_DB_PATH = CODEPLANE_DIR / "data.db"

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
  worktrees_dirname: .codeplane-worktrees
  default_sdk: copilot

retention:
  artifact_retention_days: 30
  max_artifact_size_mb: 100
  cleanup_on_startup: false

logging:
  level: info
  file: ~/.codeplane/logs/server.log
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
    worktrees_dirname: str = ".codeplane-worktrees"
    permission_mode: str = "auto"  # auto | read_only | approval_required
    utility_model: str = "gpt-4o-mini"  # cheap/fast model for naming, summaries, etc.
    default_sdk: str = "copilot"  # copilot | claude


@dataclass
class RetentionConfig:
    artifact_retention_days: int = 30
    max_artifact_size_mb: int = 100
    cleanup_on_startup: bool = False
    auto_archive_days: int = 7


@dataclass
class LoggingConfig:
    level: str = "info"
    file: str = "~/.codeplane/logs/server.log"
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
class VerificationConfig:
    """Default verification/self-review settings for new jobs."""

    verify: bool = False
    self_review: bool = False
    max_turns: int = 2
    verify_prompt: str = ""
    self_review_prompt: str = ""


@dataclass
class TerminalConfig:
    """Interactive terminal feature configuration."""

    enabled: bool = True
    max_sessions: int = 5
    default_shell: str | None = None  # auto-detect if None
    scrollback_size_kb: int = 500


@dataclass
class PlatformConfig:
    """Per-platform auth and repo binding configuration."""

    auth: str = "cli"  # cli | token
    repos: list[str] = field(default_factory=list)  # repo paths bound to this platform


@dataclass
class CPLConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    rate_limits: RateLimitConfig = field(default_factory=RateLimitConfig)
    completion: CompletionConfig = field(default_factory=CompletionConfig)
    terminal: TerminalConfig = field(default_factory=TerminalConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
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


def load_config(path: Path | None = None) -> CPLConfig:
    """Load CodePlane configuration from a YAML file."""
    if path is None:
        path = DEFAULT_CONFIG_PATH

    if not path.exists():
        return CPLConfig()

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

    return CPLConfig(
        server=_parse_section(raw, ServerConfig, "server"),
        runtime=_parse_section(raw, RuntimeConfig, "runtime"),
        retention=_parse_section(raw, RetentionConfig, "retention"),
        logging=_parse_section(raw, LoggingConfig, "logging"),
        rate_limits=_parse_section(raw, RateLimitConfig, "rate_limits"),
        completion=_parse_section(raw, CompletionConfig, "completion"),
        terminal=_parse_section(raw, TerminalConfig, "terminal"),
        verification=_parse_section(raw, VerificationConfig, "verification"),
        platforms=platforms,
        repos=[str(r) for r in raw.get("repos", []) if r is not None] if isinstance(raw.get("repos", []), list) else [],
    )


def save_config(config: CPLConfig, path: Path | None = None) -> None:
    """Persist the current CPLConfig back to the YAML config file.

    Non-destructive: loads the existing file first and merges changes in,
    preserving any keys that might exist.

    Note: the ``repos`` list is intentionally NOT written here — it is managed
    exclusively by :func:`register_repo` and :func:`unregister_repo`, which
    perform their own targeted read-modify-write.  This prevents a stale
    in-memory ``config.repos`` (loaded earlier in the request lifecycle) from
    silently overwriting repo registrations that happened concurrently.
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
        "permission_mode": str(config.runtime.permission_mode),
        "utility_model": config.runtime.utility_model,
        "default_sdk": config.runtime.default_sdk,
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
    if (
        config.terminal.enabled is not True
        or config.terminal.max_sessions != 5
        or config.terminal.default_shell is not None
        or config.terminal.scrollback_size_kb != 500
    ):
        existing["terminal"] = {
            "enabled": config.terminal.enabled,
            "max_sessions": config.terminal.max_sessions,
            **({"default_shell": config.terminal.default_shell} if config.terminal.default_shell else {}),
            "scrollback_size_kb": config.terminal.scrollback_size_kb,
        }
    # repos is intentionally omitted — managed by register_repo / unregister_repo
    existing["verification"] = {
        "verify": config.verification.verify,
        "self_review": config.verification.self_review,
        "max_turns": config.verification.max_turns,
        "verify_prompt": config.verification.verify_prompt,
        "self_review_prompt": config.verification.self_review_prompt,
    }
    if config.platforms:
        existing["platforms"] = {name: {"auth": pc.auth, "repos": pc.repos} for name, pc in config.platforms.items()}

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(existing, f, default_flow_style=False, sort_keys=False)


def _update_repos_in_file(repos: list[str], path: Path) -> None:
    """Write only the ``repos`` list to the config file, preserving all other keys.

    This is a targeted read-modify-write that always reads the latest file
    state before writing, so it never overwrites concurrent changes to other
    parts of the config.
    """
    existing: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            existing = yaml.safe_load(f) or {}
    existing["repos"] = repos
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(existing, f, default_flow_style=False, sort_keys=False)


def register_repo(config: CPLConfig, repo_path: str, config_path: Path | None = None) -> str:
    """Add a repo path to the allowlist if not already present.

    Always reads the current repos from the config file before appending, so
    concurrent registrations from other requests are not silently lost.

    Returns the resolved path that was added.
    """
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    resolved = str(Path(repo_path).expanduser().resolve())

    # Read the authoritative repos from the file (not from the caller's
    # potentially-stale CPLConfig object).
    file_repos: list[str] = []
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        file_repos = (
            [str(r) for r in raw.get("repos", []) if r is not None] if isinstance(raw.get("repos", []), list) else []
        )

    if resolved not in file_repos:
        file_repos.append(resolved)
        _update_repos_in_file(file_repos, config_path)

    # Keep the caller's in-memory config in sync.
    if resolved not in config.repos:
        config.repos.append(resolved)

    return resolved


def unregister_repo(config: CPLConfig, repo_path: str, config_path: Path | None = None) -> str:
    """Remove a repo path from the allowlist.

    Always reads the current repos from the config file before removing, so
    the removal is applied to the latest state rather than a stale snapshot.

    Returns the resolved path that was removed.
    Raises ValueError if the repo is not in the allowlist.
    """
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    resolved = str(Path(repo_path).expanduser().resolve())

    # Read the authoritative repos from the file.
    file_repos: list[str] = []
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        file_repos = (
            [str(r) for r in raw.get("repos", []) if r is not None] if isinstance(raw.get("repos", []), list) else []
        )

    removed: str | None = None
    if resolved in file_repos:
        file_repos.remove(resolved)
        removed = resolved
    elif repo_path in file_repos:
        file_repos.remove(repo_path)
        removed = repo_path

    if removed is None:
        raise ValueError(f"Repository '{repo_path}' is not in the allowlist.")

    _update_repos_in_file(file_repos, config_path)

    # Keep the caller's in-memory config in sync.
    config.repos = [r for r in config.repos if r not in (resolved, repo_path)]

    return removed


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
