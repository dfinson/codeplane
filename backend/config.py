"""Configuration loading and validation.

Sections
--------
- **Path helpers** — ``get_codeplane_dir()``, lazy module-level constants
  (``CODEPLANE_DIR``, ``DEFAULT_CONFIG_PATH``, ``DEFAULT_DB_PATH``).
- **Feature flags** — compile-time constants (``VOICE_ENABLED``, ``MCP_ENABLED``, …).
- **Dataclasses** — ``ServerConfig``, ``RuntimeConfig``, ``RetentionConfig``,
  ``LoggingConfig``, ``RateLimitConfig``, ``CompletionConfig``,
  ``VerificationConfig``, ``TerminalConfig``, ``PlatformConfig``, and the
  root ``CPLConfig`` that aggregates them.
- **YAML I/O** — ``load_config()``, ``save_config()``, ``init_config()``.
- **Repo management** — ``register_repo()`` / ``unregister_repo()`` (file-level
  read-modify-write to prevent concurrent overwrites).
"""

from __future__ import annotations

import enum
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml

if TYPE_CHECKING:
    from backend.models.domain import Job, SessionConfig

log = structlog.get_logger()


def _resolve_codeplane_dir() -> Path:
    """Resolve CODEPLANE_HOME from env var, falling back to ~/.codeplane."""
    env = os.environ.get("CODEPLANE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".codeplane"


_codeplane_dir: Path | None = None


def get_codeplane_dir() -> Path:
    """Return the resolved CodePlane home directory (lazy, cached).

    Prefer this over importing ``CODEPLANE_DIR`` directly — the env-var read
    is deferred until first call instead of happening at import time.
    """
    global _codeplane_dir  # noqa: PLW0603
    if _codeplane_dir is None:
        _codeplane_dir = _resolve_codeplane_dir()
    return _codeplane_dir


# Lazy module-level constants via __getattr__.  ``from backend.config import
# CODEPLANE_DIR`` still works, but the env-var read is deferred until the
# attribute is first accessed rather than when *this* module is imported.
_LAZY_CONSTANTS = {"CODEPLANE_DIR", "DEFAULT_CONFIG_PATH", "DEFAULT_DB_PATH"}


def __getattr__(name: str) -> Any:
    if name in _LAZY_CONSTANTS:
        d = get_codeplane_dir()
        if name == "CODEPLANE_DIR":
            return d
        if name == "DEFAULT_CONFIG_PATH":
            return d / "config.yaml"
        if name == "DEFAULT_DB_PATH":
            return d / "data.db"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    copilot_only: true
    suppressed_preflight_agent_prompts: []

retention:
  artifact_retention_days: 30
  max_artifact_size_mb: 100
  cleanup_on_startup: false

native_mappings:
  extra_dirs: []
  migrated_user_dir: null  # Defaults to ~/.codeplane/mappings

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


_DEFAULT_COPILOT_UTILITY_MODEL = "claude-haiku-4.5"


@dataclass
class RuntimeConfig:
    max_concurrent_jobs: int = 2
    worktrees_dirname: str = ".codeplane-worktrees"
    utility_model: str = ""  # Missing/blank uses the provider default; explicit identifiers remain unchanged.
    utility_sdk: str | None = None
    default_sdk: str = "copilot"  # copilot | claude
    copilot_only: bool = True
    suppressed_preflight_agent_prompts: list[str] = field(default_factory=list)
    cli_sidecars: list[str] | None = None  # sidecar names for CLI sessions; None = built-in defaults

    def __post_init__(self) -> None:
        if not isinstance(self.copilot_only, bool):
            raise ValueError("runtime.copilot_only must be a boolean")
        if self.utility_sdk is not None and (not isinstance(self.utility_sdk, str) or not self.utility_sdk.strip()):
            raise ValueError("runtime.utility_sdk must be a nonempty adapter ID or null")
        if not self.utility_model or not self.utility_model.strip():
            provider = self.utility_sdk or ("copilot" if self.copilot_only else self.default_sdk)
            self.utility_model = {
                "copilot": _DEFAULT_COPILOT_UTILITY_MODEL,
                "claude": "claude-haiku-4.5",
            }.get(provider, "")


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
    strategy: str = "manual"  # manual | auto_merge | pr_only
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


DEFAULT_VERIFY_PROMPT = (
    "You are now running a post-task verification pass. "
    "Start with a single short sentence announcing this — e.g. 'Running lint and tests.' "
    "Then check whether any source files or documentation were modified during this "
    "task (e.g. `git diff --stat HEAD` or compare against the base ref). "
    "If no files were modified, state that there is nothing to verify and stop. "
    "Only if files were changed: identify and run this project's test suite, "
    "linter, and type checker. Stop as soon as everything passes — you do not "
    "need to exhaust the maximum number of allowed turns. If something fails, "
    "fix it and re-run. Assume failures are caused by your changes; do not "
    "dismiss them as pre-existing or flaky. Also check that you haven't made "
    "unrelated changes outside the scope of the original task; revert any that "
    "you find. "
    "Your final message must be a single cohesive summary covering: first, what "
    "was built or changed and why (the main task); then, the verification outcome "
    "as one appended sentence (e.g. 'All checks pass.' or 'Fixed a failing test "
    "in foo.py.'). The checks are a footnote — the task summary is the headline."
)

DEFAULT_SELF_REVIEW_PROMPT = (
    "You are now running a post-task self-review pass. "
    "Start with a single short sentence announcing this — e.g. 'Reviewing my changes.' "
    "Then check whether any source files or documentation were modified during this "
    "task (e.g. `git diff --stat HEAD` or compare against the base ref). "
    "If no files were modified, state that there is nothing to review and stop. "
    "Only if files were changed: look at the full diff and check for missed edge "
    "cases, incomplete implementations, leftover debug code, broken imports, dead "
    "code, backwards-compatibility shims or fallback paths that may no longer be "
    "needed, and inconsistencies with the surrounding codebase. If you find "
    "issues, fix them. "
    "Your final message must be a single cohesive summary covering: first, what "
    "was built or changed and why (the main task); then, the review outcome as one "
    "appended sentence (e.g. 'Self-review clean.' or 'Removed a leftover debug "
    "print.'). The review is a footnote — the task summary is the headline."
)


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
class TelemetryConfig:
    """Telemetry / observability configuration."""

    # Retained legacy setting. Only the explicit export binding can enable
    # sanitized network export; this field alone has no transport authority.
    otel_exporter_endpoint: str = ""
    # Optional budget caps for burn-rate projection in the analytics dashboard.
    claude_monthly_budget_usd: float = 0.0
    # Copilot entitlement auto-detected from SDK quota snapshots, but overridable.
    copilot_premium_entitlement: int = 0
    # Personal daily spend limit (0 = no limit).  Shown in the analytics Budget card.
    daily_spend_limit_usd: float = 0.0
    # Unique identifier for this CodePlane instance.  Auto-generated on first run
    # if not set.  Used for future Hub telemetry push.
    instance_id: str = ""


@dataclass
class ExportConfig:
    """Explicit optional export binding; no endpoint alone enables a transport."""

    enabled: bool = False
    endpoint: str = ""
    protocol: str = "http/protobuf"
    credential_id: str | None = None
    approved_models: list[str] = field(default_factory=list)
    metrics_endpoint: str = ""
    metrics_credential_id: str | None = None


@dataclass
class PricingConfig:
    """Model pricing refresh configuration."""

    refresh_interval_hours: int = 24


@dataclass
class TrailConfig:
    """Agent audit trail enrichment tuning."""

    enrich_batch_size: int = 20
    enrich_interval_seconds: float = 10.0
    enrich_max_retries: int = 10
    enrich_decisions_context: int = 5


@dataclass
class NativeMappingsConfig:
    """Local declarative definitions, in explicit search order before user/bundled files."""

    extra_dirs: list[str] = field(default_factory=list)
    migrated_user_dir: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.extra_dirs, list) or any(
            not isinstance(path, str) or not path.strip() or "\x00" in path for path in self.extra_dirs
        ):
            raise ValueError("native_mappings.extra_dirs must be a list of nonempty directory paths")
        if self.migrated_user_dir is not None and (
            not isinstance(self.migrated_user_dir, str)
            or not self.migrated_user_dir.strip()
            or "\x00" in self.migrated_user_dir
        ):
            raise ValueError("native_mappings.migrated_user_dir must be a nonempty directory path")


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
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    trail: TrailConfig = field(default_factory=TrailConfig)
    native_mappings: NativeMappingsConfig = field(default_factory=NativeMappingsConfig)
    platforms: dict[str, PlatformConfig] = field(default_factory=dict)
    repos: list[str] = field(default_factory=list)


def _parse_section(raw: dict[str, Any], cls: type, key: str) -> Any:
    """Parse a config section dict into a dataclass instance."""
    section = raw.get(key, {})
    if not isinstance(section, dict):
        return cls()
    # Only pass keys that the dataclass accepts
    valid_keys = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]  # __dataclass_fields__ exists at runtime
    return cls(**{k: v for k, v in section.items() if k in valid_keys})


def _read_config_data(path: Path) -> dict[str, Any]:
    """Read settings, retiring the obsolete analysis section atomically."""
    target = path.resolve(strict=True)
    original = target.read_bytes()
    parsed = yaml.safe_load(original)
    if parsed is None and yaml.compose(original) is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ValueError(f"CodePlane configuration must be a mapping: {path}")
    if "coderecon" not in parsed:
        return parsed

    import tempfile

    original_stat = target.stat()
    del parsed["coderecon"]
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            yaml.safe_dump(parsed, handle, default_flow_style=False, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        current_stat = target.stat()
        if (
            path.resolve(strict=True) != target
            or (current_stat.st_dev, current_stat.st_ino, current_stat.st_mtime_ns, current_stat.st_size)
            != (original_stat.st_dev, original_stat.st_ino, original_stat.st_mtime_ns, original_stat.st_size)
            or target.read_bytes() != original
        ):
            raise OSError(f"Configuration changed during structural retirement: {path}")
        os.chmod(temporary, original_stat.st_mode)
        os.replace(temporary, target)
        temporary = None
    except (OSError, yaml.YAMLError):
        log.error("retired_analysis_config_removal_failed", path=str(path), exc_info=True)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    log.info("retired_analysis_config_removed", path=str(path))
    return parsed


def load_config(path: Path | None = None) -> CPLConfig:
    """Load CodePlane configuration from a YAML file."""
    if path is None:
        path = get_codeplane_dir() / "config.yaml"

    if not path.exists():
        return CPLConfig()

    raw = _read_config_data(path)
    if "native_mappings" in raw and not isinstance(raw["native_mappings"], dict):
        raise ValueError("native_mappings must be a configuration mapping")

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

    cfg = CPLConfig(
        server=_parse_section(raw, ServerConfig, "server"),
        runtime=_parse_section(raw, RuntimeConfig, "runtime"),
        retention=_parse_section(raw, RetentionConfig, "retention"),
        logging=_parse_section(raw, LoggingConfig, "logging"),
        rate_limits=_parse_section(raw, RateLimitConfig, "rate_limits"),
        completion=_parse_section(raw, CompletionConfig, "completion"),
        terminal=_parse_section(raw, TerminalConfig, "terminal"),
        verification=_parse_section(raw, VerificationConfig, "verification"),
        telemetry=_parse_section(raw, TelemetryConfig, "telemetry"),
        export=_parse_section(raw, ExportConfig, "export"),
        pricing=_parse_section(raw, PricingConfig, "pricing"),
        trail=_parse_section(raw, TrailConfig, "trail"),
        native_mappings=_parse_section(raw, NativeMappingsConfig, "native_mappings"),
        platforms=platforms,
        repos=[str(r) for r in raw.get("repos", []) if r is not None] if isinstance(raw.get("repos", []), list) else [],
    )

    # Auto-generate instance_id on first run if not set
    if not cfg.telemetry.instance_id:
        import uuid

        cfg.telemetry.instance_id = str(uuid.uuid4())
        # Persist so the ID is stable across restarts
        try:
            save_config(cfg, path)
        except (OSError, yaml.YAMLError):
            log.warning("instance_id_persist_failed", exc_info=True)
    elif "tracker" in raw:
        try:
            save_config(cfg, path)
        except (OSError, yaml.YAMLError):
            log.warning("retired_tracker_config_removal_failed", exc_info=True)

    return cfg


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
        path = get_codeplane_dir() / "config.yaml"

    # Load existing config to preserve unknown keys
    existing: dict[str, Any] = {}
    if path.exists():
        existing = _read_config_data(path)

    # Merge our known sections — existing keys not in our model are preserved
    def _to_dict(dc: Any) -> dict[str, Any]:
        return asdict(
            dc,
            dict_factory=lambda pairs: {k: (str(v) if isinstance(v, enum.Enum) else v) for k, v in pairs},
        )

    existing["server"] = _to_dict(config.server)
    existing["runtime"] = _to_dict(config.runtime)
    existing["retention"] = _to_dict(config.retention)
    existing["logging"] = _to_dict(config.logging)
    existing["rate_limits"] = _to_dict(config.rate_limits)
    existing["completion"] = _to_dict(config.completion)
    if (
        config.terminal.enabled is not True
        or config.terminal.max_sessions != 5
        or config.terminal.default_shell is not None
        or config.terminal.scrollback_size_kb != 500
    ):
        d = _to_dict(config.terminal)
        if not d.get("default_shell"):
            d.pop("default_shell", None)
        existing["terminal"] = d
    # repos is intentionally omitted — managed by register_repo / unregister_repo
    existing["verification"] = _to_dict(config.verification)
    existing["telemetry"] = _to_dict(config.telemetry)
    existing["export"] = _to_dict(config.export)
    existing["native_mappings"] = _to_dict(config.native_mappings)
    # Tracker synchronization uses a fixed production cadence. Remove the
    # retired persisted section when any settings save occurs.
    existing.pop("tracker", None)
    if config.platforms:
        existing["platforms"] = {name: _to_dict(pc) for name, pc in config.platforms.items()}

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
        existing = _read_config_data(path)
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
        config_path = get_codeplane_dir() / "config.yaml"
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
        config_path = get_codeplane_dir() / "config.yaml"
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
        path = get_codeplane_dir() / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_YAML)
    return path


# ---------------------------------------------------------------------------
# Session config builders — pure config/filesystem helpers
# ---------------------------------------------------------------------------


def discover_mcp_servers(repo_path: str, config: CPLConfig) -> dict[str, Any]:
    """Discover MCP servers from .vscode/mcp.json and global config, respecting .codeplane.yml disabled list."""
    import json as _json

    from backend.models.domain import MCPServerConfig

    servers: dict[str, MCPServerConfig] = {}

    # 1. Global config: tools.mcp section
    global_config_path = get_codeplane_dir() / "config.yaml"
    if global_config_path.exists():
        try:
            with open(global_config_path) as f:
                raw = yaml.safe_load(f) or {}
            tools_mcp = raw.get("tools", {}).get("mcp", {})
            if isinstance(tools_mcp, dict):
                for name, entry in tools_mcp.items():
                    if name == "disabled" or not isinstance(entry, dict):
                        continue
                    servers[name] = MCPServerConfig(
                        command=entry.get("command", ""),
                        args=entry.get("args", []),
                        env=entry.get("env"),
                    )
        except (OSError, yaml.YAMLError):
            log.warning("mcp_global_config_read_failed", path=str(global_config_path))

    # 2. Repo-level: .vscode/mcp.json (takes precedence over global)
    mcp_json_path = Path(repo_path) / ".vscode" / "mcp.json"
    if mcp_json_path.exists():
        try:
            with open(mcp_json_path) as f:
                mcp_data = _json.load(f)
            repo_servers = mcp_data.get("servers", {})
            if isinstance(repo_servers, dict):
                for name, entry in repo_servers.items():
                    if not isinstance(entry, dict):
                        continue
                    servers[name] = MCPServerConfig(
                        command=entry.get("command", ""),
                        args=entry.get("args", []),
                        env=entry.get("env"),
                    )
        except (OSError, _json.JSONDecodeError):
            log.warning("mcp_repo_config_read_failed", path=str(mcp_json_path))

    # 3. Apply .codeplane.yml disabled list
    codeplane_yml_path = Path(repo_path) / ".codeplane.yml"
    if codeplane_yml_path.exists():
        try:
            with open(codeplane_yml_path) as f:
                codeplane_config = yaml.safe_load(f) or {}
            disabled = codeplane_config.get("tools", {}).get("mcp", {}).get("disabled", [])
            if isinstance(disabled, list):
                for name in disabled:
                    servers.pop(str(name), None)
        except (OSError, yaml.YAMLError):
            log.warning("codeplane_yml_read_failed", path=str(codeplane_yml_path))

    return servers


def resolve_protected_paths(repo_path: str) -> list[str]:
    """Read protected_paths from .codeplane.yml if present."""
    codeplane_yml = Path(repo_path) / ".codeplane.yml"
    if not codeplane_yml.exists():
        return []
    try:
        with open(codeplane_yml) as f:
            data = yaml.safe_load(f) or {}
        paths = data.get("protected_paths", [])
        return [str(p) for p in paths] if isinstance(paths, list) else []
    except (OSError, yaml.YAMLError):
        log.warning("protected_paths_read_failed", path=str(codeplane_yml), exc_info=True)
        return []


def build_session_config(
    job: Job,
    config: CPLConfig,
) -> SessionConfig:
    """Build a SessionConfig from a Job record and resolved config."""
    from backend.models.domain import SessionConfig

    workspace = job.execution_workspace
    mcp_servers = discover_mcp_servers(job.execution_repo, config)
    protected_paths = resolve_protected_paths(job.execution_repo)

    return SessionConfig(
        workspace_path=workspace,
        prompt=job.prompt,
        job_id=job.id,
        model=job.model,
        sdk=job.sdk,
        mcp_servers=mcp_servers,
        protected_paths=protected_paths,
    )
