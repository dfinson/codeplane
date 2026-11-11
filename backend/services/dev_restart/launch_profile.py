"""The active launch profile: ``~/.codeplane/run.json``.

``cpl up`` publishes this file only after its listener has bound (see
``backend/cli.py``'s post-bind publication callback). Later restart tooling
loads and validates it here -- fail-closed -- before taking any disruptive
action. This is the single place that parses, serializes, persists, and
validates the profile so the schema is never duplicated between ``cpl up``
and restart tooling (Story 1.1 Dev Notes).

Field names are camelCase in the JSON wire format; see
``internal-docs/design/developer-restart.md``
for the authoritative schema table and secret-source classification rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pathlib import Path

from backend import config as backend_config
from backend.models.domain import CodePlaneError
from backend.services.dev_restart.restart_protocol import (
    RestartProtocolError,
    is_identity_alive,
    read_json_file,
    write_json_atomic,
)

LAUNCH_PROFILE_SCHEMA_VERSION = 1

SecretSourceKind = Literal["not_required", "resolvable", "unreplayable"]
_VALID_SECRET_KINDS = frozenset({"not_required", "resolvable", "unreplayable"})
_VALID_PROVIDERS = frozenset({"local", "devtunnel", "cloudflare"})
_VALID_TUNNEL_OWNERSHIP = frozenset({"managed", "external"})


class LaunchProfileError(CodePlaneError):
    """Base error for active launch-profile persistence or validation failures."""


class LaunchProfileMissingError(LaunchProfileError):
    """No launch profile exists at the expected path."""


class LaunchProfileInvalidError(LaunchProfileError):
    """The launch profile is malformed, incomplete, or uses an unsupported schema."""


class LaunchProfileStaleError(LaunchProfileError):
    """The recorded process identity no longer owns the recorded listener."""


# ---------------------------------------------------------------------------
# Secret-source classification (closed union: never carries a secret value)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SecretSource:
    """A closed-union, secret-free record of where a credential came from."""

    kind: SecretSourceKind
    provider: str | None = None
    reference: str | None = None

    def to_dict(self) -> dict[str, str]:
        if self.kind == "resolvable":
            if not self.provider or not self.reference:
                raise LaunchProfileInvalidError("resolvable secret source requires provider and reference")
            return {"kind": self.kind, "provider": self.provider, "reference": self.reference}
        return {"kind": self.kind}

    @staticmethod
    def not_required() -> SecretSource:
        return SecretSource(kind="not_required")

    @staticmethod
    def unreplayable() -> SecretSource:
        return SecretSource(kind="unreplayable")

    @staticmethod
    def resolvable(provider: str, reference: str) -> SecretSource:
        return SecretSource(kind="resolvable", provider=provider, reference=reference)

    @staticmethod
    def from_dict(data: Any) -> SecretSource:
        if not isinstance(data, dict):
            raise LaunchProfileInvalidError("secret source must be a JSON object")
        kind = data.get("kind")
        if kind not in _VALID_SECRET_KINDS:
            raise LaunchProfileInvalidError(f"invalid secret source kind: {kind!r}")
        if kind == "resolvable":
            provider = data.get("provider")
            reference = data.get("reference")
            if not isinstance(provider, str) or not provider or not isinstance(reference, str) or not reference:
                raise LaunchProfileInvalidError("resolvable secret source requires non-empty provider and reference")
            return SecretSource(kind="resolvable", provider=provider, reference=reference)
        return SecretSource(kind=kind)


# ---------------------------------------------------------------------------
# Active launch profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LaunchProfile:
    """The resolved native launch identity of the currently running ``cpl up`` process."""

    schema_version: int
    executable: str
    working_directory: str
    host: str
    port: int
    dev: bool
    remote: bool
    provider: str
    tunnel_ownership: str | None
    tunnel_name: str | None
    tunnel_origin: str | None
    tunnel_origin_reusable: bool | None
    password_source: SecretSource
    tunnel_credential_source: SecretSource
    started_pid: int
    started_process_time: float
    written_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "executable": self.executable,
            "workingDirectory": self.working_directory,
            "host": self.host,
            "port": self.port,
            "dev": self.dev,
            "remote": self.remote,
            "provider": self.provider,
            "tunnelOwnership": self.tunnel_ownership,
            "tunnelName": self.tunnel_name,
            "tunnelOrigin": self.tunnel_origin,
            "tunnelOriginReusable": self.tunnel_origin_reusable,
            "passwordSource": self.password_source.to_dict(),
            "tunnelCredentialSource": self.tunnel_credential_source.to_dict(),
            "startedPid": self.started_pid,
            "startedProcessTime": self.started_process_time,
            "writtenAt": self.written_at,
        }

    @staticmethod
    def from_dict(data: Any) -> LaunchProfile:
        if not isinstance(data, dict):
            raise LaunchProfileInvalidError("active launch profile must be a JSON object")

        schema_version = data.get("schemaVersion")
        if schema_version != LAUNCH_PROFILE_SCHEMA_VERSION:
            raise LaunchProfileInvalidError(f"unsupported launch profile schemaVersion: {schema_version!r}")

        def _require_str(key: str) -> str:
            value = data.get(key)
            if not isinstance(value, str) or not value:
                raise LaunchProfileInvalidError(f"launch profile field {key!r} must be a non-empty string")
            return value

        def _require_bool(key: str) -> bool:
            value = data.get(key)
            if not isinstance(value, bool):
                raise LaunchProfileInvalidError(f"launch profile field {key!r} must be a boolean")
            return value

        def _require_int(key: str) -> int:
            value = data.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                raise LaunchProfileInvalidError(f"launch profile field {key!r} must be an integer")
            return value

        def _require_number(key: str) -> float:
            value = data.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise LaunchProfileInvalidError(f"launch profile field {key!r} must be a number")
            return float(value)

        executable = _require_str("executable")
        working_directory = _require_str("workingDirectory")
        host = _require_str("host")
        port = _require_int("port")
        dev = _require_bool("dev")
        remote = _require_bool("remote")
        provider = _require_str("provider")
        if provider not in _VALID_PROVIDERS:
            raise LaunchProfileInvalidError(f"invalid provider: {provider!r}")

        tunnel_ownership = data.get("tunnelOwnership")
        if tunnel_ownership is not None and tunnel_ownership not in _VALID_TUNNEL_OWNERSHIP:
            raise LaunchProfileInvalidError(f"invalid tunnelOwnership: {tunnel_ownership!r}")

        tunnel_name = data.get("tunnelName")
        if tunnel_name is not None and not isinstance(tunnel_name, str):
            raise LaunchProfileInvalidError("tunnelName must be a string or null")

        tunnel_origin = data.get("tunnelOrigin")
        if tunnel_origin is not None and not isinstance(tunnel_origin, str):
            raise LaunchProfileInvalidError("tunnelOrigin must be a string or null")

        tunnel_origin_reusable = data.get("tunnelOriginReusable")
        if tunnel_origin_reusable is not None and not isinstance(tunnel_origin_reusable, bool):
            raise LaunchProfileInvalidError("tunnelOriginReusable must be a boolean or null")

        password_source = SecretSource.from_dict(data.get("passwordSource"))
        tunnel_credential_source = SecretSource.from_dict(data.get("tunnelCredentialSource"))

        started_pid = _require_int("startedPid")
        started_process_time = _require_number("startedProcessTime")
        written_at = _require_str("writtenAt")

        profile = LaunchProfile(
            schema_version=schema_version,
            executable=executable,
            working_directory=working_directory,
            host=host,
            port=port,
            dev=dev,
            remote=remote,
            provider=provider,
            tunnel_ownership=tunnel_ownership,
            tunnel_name=tunnel_name,
            tunnel_origin=tunnel_origin,
            tunnel_origin_reusable=tunnel_origin_reusable,
            password_source=password_source,
            tunnel_credential_source=tunnel_credential_source,
            started_pid=started_pid,
            started_process_time=started_process_time,
            written_at=written_at,
        )
        profile.validate_combination()
        return profile

    def validate_combination(self) -> None:
        """Reject impossible local/remote field combinations (AC 5)."""
        if not self.remote:
            if self.provider != "local":
                raise LaunchProfileInvalidError("provider must be 'local' when remote is false")
            if self.tunnel_ownership is not None or self.tunnel_name is not None:
                raise LaunchProfileInvalidError("tunnelOwnership/tunnelName must be null when remote is false")
            if self.tunnel_origin is not None or self.tunnel_origin_reusable is not None:
                raise LaunchProfileInvalidError("tunnelOrigin/tunnelOriginReusable must be null when remote is false")
        else:
            if self.provider not in ("devtunnel", "cloudflare"):
                raise LaunchProfileInvalidError("provider must be 'devtunnel' or 'cloudflare' when remote is true")
            if self.tunnel_ownership is None:
                raise LaunchProfileInvalidError("tunnelOwnership is required when remote is true")

    def ensure_secrets_are_replayable(self) -> None:
        """Refuse a required secret source that cannot be resolved again (AC 4)."""
        if self.password_source.kind == "unreplayable":
            raise LaunchProfileInvalidError("password source is not replayable (unreplayable)")
        if self.tunnel_credential_source.kind == "unreplayable":
            raise LaunchProfileInvalidError("tunnel credential source is not replayable (unreplayable)")


def build_active_launch_profile(
    *,
    executable: str,
    working_directory: str,
    host: str,
    port: int,
    dev: bool,
    remote: bool,
    provider: str,
    tunnel_ownership: str | None,
    tunnel_name: str | None,
    tunnel_origin: str | None = None,
    tunnel_origin_reusable: bool | None = None,
    password_source: SecretSource,
    tunnel_credential_source: SecretSource,
    started_pid: int,
    started_process_time: float,
) -> LaunchProfile:
    """Build and structurally validate a profile ready for atomic publication."""
    profile = LaunchProfile(
        schema_version=LAUNCH_PROFILE_SCHEMA_VERSION,
        executable=executable,
        working_directory=working_directory,
        host=host,
        port=port,
        dev=dev,
        remote=remote,
        provider=provider,
        tunnel_ownership=tunnel_ownership,
        tunnel_name=tunnel_name,
        tunnel_origin=tunnel_origin,
        tunnel_origin_reusable=tunnel_origin_reusable,
        password_source=password_source,
        tunnel_credential_source=tunnel_credential_source,
        started_pid=started_pid,
        started_process_time=started_process_time,
        written_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    profile.validate_combination()
    return profile


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def active_launch_profile_path() -> Path:
    """Resolve through ``get_codeplane_dir()`` so ``CODEPLANE_HOME`` remains authoritative."""
    return backend_config.get_codeplane_dir() / "run.json"


def write_active_launch_profile(profile: LaunchProfile) -> None:
    """Atomically publish *profile* to ``run.json``. Raises ``OSError`` on failure.

    Never deletes an older complete profile before the replacement succeeds;
    on failure only the just-written temporary file is removed (see
    ``write_json_atomic``).
    """
    write_json_atomic(active_launch_profile_path(), profile.to_dict())


def load_active_profile() -> LaunchProfile:
    """Load and structurally validate the active launch profile. Fails closed."""
    path = active_launch_profile_path()
    if not path.exists():
        raise LaunchProfileMissingError(f"no active launch profile at {path}")
    try:
        data = read_json_file(path)
    except RestartProtocolError as exc:
        raise LaunchProfileInvalidError(str(exc)) from exc
    return LaunchProfile.from_dict(data)


# ---------------------------------------------------------------------------
# Validation for restart use
# ---------------------------------------------------------------------------


def profile_owns_listener(profile: LaunchProfile) -> bool:
    """True iff *profile*'s recorded PID+creation-time is alive and owns its recorded port.

    Reuses ``backend.cli``'s existing platform-specific listener-owner
    helper instead of a second process-discovery implementation. Imported
    lazily so importing this module never pulls in Click/CLI machinery.
    """
    if not is_identity_alive(profile.started_pid, profile.started_process_time):
        return False

    from backend.cli import _find_pids_on_port

    return profile.started_pid in _find_pids_on_port(profile.port)


def validate_launch_profile(profile: LaunchProfile, require_replayable_secrets: bool = True) -> None:
    """Fail-closed validation of *profile* for restart use. Raises on any violation.

    Verifies live process identity plus exact listener ownership (never
    process names), and -- unless explicitly opted out -- that every
    required secret source can still be resolved.
    """
    if not profile_owns_listener(profile):
        raise LaunchProfileStaleError(
            f"PID {profile.started_pid} no longer owns the listener on port {profile.port} "
            "(process exited, PID reused, or listener moved)"
        )
    if require_replayable_secrets:
        profile.ensure_secrets_are_replayable()
