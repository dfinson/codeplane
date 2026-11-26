"""Tests for the active launch profile (``~/.codeplane/run.json``): schema,
atomic persistence, secret-source classification, and fail-closed
validation (Story 1.1).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from backend.services.dev_restart.launch_profile import (
    LaunchProfile,
    LaunchProfileInvalidError,
    LaunchProfileMissingError,
    LaunchProfileStaleError,
    SecretSource,
    active_launch_profile_path,
    build_active_launch_profile,
    load_active_profile,
    profile_owns_listener,
    validate_launch_profile,
    write_active_launch_profile,
)

if TYPE_CHECKING:
    from pathlib import Path

SENTINEL_PASSWORD = "sentinel-password-do-not-leak-Xk29fQ"
SENTINEL_TOKEN = "sentinel-cf-token-do-not-leak-77gh2"


@pytest.fixture(autouse=True)
def _isolated_codeplane_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODEPLANE_HOME", str(tmp_path))
    import backend.config as config_module

    monkeypatch.setattr(config_module, "_codeplane_dir", None)
    return tmp_path


def _local_profile(**overrides: object) -> LaunchProfile:
    defaults: dict[str, object] = dict(
        executable="/usr/bin/python3",
        working_directory="/home/dev/codeplane",
        host="127.0.0.1",
        port=8080,
        dev=False,
        remote=False,
        provider="local",
        tunnel_ownership=None,
        tunnel_name=None,
        password_source=SecretSource.not_required(),
        tunnel_credential_source=SecretSource.not_required(),
        started_pid=4242,
        started_process_time=1_700_000_000.5,
    )
    defaults.update(overrides)
    return build_active_launch_profile(**defaults)  # type: ignore[arg-type]


def _remote_profile(**overrides: object) -> LaunchProfile:
    defaults: dict[str, object] = dict(
        executable="/usr/bin/python3",
        working_directory="/home/dev/codeplane",
        host="0.0.0.0",  # noqa: S104
        port=8080,
        dev=False,
        remote=True,
        provider="devtunnel",
        tunnel_ownership="managed",
        tunnel_name="cpl-ab12cd34",
        tunnel_origin="https://cpl-ab12cd34.devtunnels.ms",
        tunnel_origin_reusable=True,
        password_source=SecretSource.resolvable("environment", "CPL_PASSWORD"),
        tunnel_credential_source=SecretSource.resolvable("provider-login", "devtunnel"),
        started_pid=4242,
        started_process_time=1_700_000_000.5,
    )
    defaults.update(overrides)
    return build_active_launch_profile(**defaults)  # type: ignore[arg-type]


class TestRuntimeContract:
    def test_access_only_mode_survives_secret_free_roundtrip(self) -> None:
        data = _remote_profile(provider="cloudflare").to_dict()
        data["passwordSource"] = {"kind": "not_required"}
        data["authMode"] = "access_only"
        restored = LaunchProfile.from_dict(data).to_dict()
        assert restored.get("authMode") == "access_only"
        assert restored["passwordSource"] == {"kind": "not_required"}

    def test_legacy_remote_profile_without_password_fails_closed(self) -> None:
        data = _remote_profile().to_dict()
        data["passwordSource"] = {"kind": "not_required"}
        data.pop("authMode", None)
        with pytest.raises(LaunchProfileInvalidError, match="auth|password"):
            LaunchProfile.from_dict(data)

    def test_access_only_rejects_non_cloudflare_profile(self) -> None:
        data = _remote_profile().to_dict()
        data["passwordSource"] = {"kind": "not_required"}
        data["authMode"] = "access_only"
        with pytest.raises(LaunchProfileInvalidError, match="Cloudflare|cloudflare"):
            LaunchProfile.from_dict(data)


class TestSecretSource:
    def test_not_required_roundtrip(self) -> None:
        source = SecretSource.not_required()
        assert SecretSource.from_dict(source.to_dict()) == source

    def test_unreplayable_roundtrip(self) -> None:
        source = SecretSource.unreplayable()
        assert SecretSource.from_dict(source.to_dict()) == source

    def test_resolvable_roundtrip(self) -> None:
        source = SecretSource.resolvable("environment", "CPL_PASSWORD")
        data = source.to_dict()
        assert data == {"kind": "resolvable", "provider": "environment", "reference": "CPL_PASSWORD"}
        assert SecretSource.from_dict(data) == source

    def test_resolvable_missing_provider_raises(self) -> None:
        with pytest.raises(LaunchProfileInvalidError):
            SecretSource.from_dict({"kind": "resolvable", "reference": "CPL_PASSWORD"})

    def test_resolvable_missing_reference_raises(self) -> None:
        with pytest.raises(LaunchProfileInvalidError):
            SecretSource.from_dict({"kind": "resolvable", "provider": "environment"})

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(LaunchProfileInvalidError):
            SecretSource.from_dict({"kind": "bogus"})

    def test_not_a_dict_raises(self) -> None:
        with pytest.raises(LaunchProfileInvalidError):
            SecretSource.from_dict("not-a-dict")


class TestLaunchProfileSchema:
    def test_local_profile_roundtrip(self) -> None:
        profile = _local_profile()
        restored = LaunchProfile.from_dict(json.loads(json.dumps(profile.to_dict())))
        assert restored == profile

    def test_remote_profile_roundtrip(self) -> None:
        profile = _remote_profile()
        restored = LaunchProfile.from_dict(json.loads(json.dumps(profile.to_dict())))
        assert restored == profile

    def test_camel_case_field_names(self) -> None:
        data = _local_profile().to_dict()
        assert set(data) == {
            "schemaVersion",
            "executable",
            "workingDirectory",
            "host",
            "port",
            "dev",
            "remote",
            "provider",
            "tunnelOwnership",
            "tunnelName",
            "tunnelOrigin",
            "tunnelOriginReusable",
            "passwordSource",
            "authMode",
            "tunnelCredentialSource",
            "startedPid",
            "startedProcessTime",
            "writtenAt",
        }

    def test_tunnel_origin_and_reusable_roundtrip(self) -> None:
        profile = _remote_profile(tunnel_origin="https://cpl-fixed.example.com", tunnel_origin_reusable=False)
        restored = LaunchProfile.from_dict(json.loads(json.dumps(profile.to_dict())))
        assert restored.tunnel_origin == "https://cpl-fixed.example.com"
        assert restored.tunnel_origin_reusable is False

    def test_missing_tunnel_origin_fields_default_to_none(self) -> None:
        """Older profiles written before these fields existed must still load (schema stays v1)."""
        data = _local_profile().to_dict()
        del data["tunnelOrigin"]
        del data["tunnelOriginReusable"]
        restored = LaunchProfile.from_dict(data)
        assert restored.tunnel_origin is None
        assert restored.tunnel_origin_reusable is None

    def test_wrong_type_for_tunnel_origin_raises(self) -> None:
        data = _remote_profile().to_dict()
        data["tunnelOrigin"] = 12345
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_wrong_type_for_tunnel_origin_reusable_raises(self) -> None:
        data = _remote_profile().to_dict()
        data["tunnelOriginReusable"] = "yes"
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_local_mode_with_tunnel_origin_raises(self) -> None:
        """Impossible local/remote combination: remote=false but tunnelOrigin is set."""
        data = _local_profile().to_dict()
        data["tunnelOrigin"] = "https://should-not-be-set.example.com"
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_unsupported_schema_version_raises(self) -> None:
        data = _local_profile().to_dict()
        data["schemaVersion"] = 2
        with pytest.raises(LaunchProfileInvalidError, match="schemaVersion"):
            LaunchProfile.from_dict(data)

    def test_missing_schema_version_raises(self) -> None:
        data = _local_profile().to_dict()
        del data["schemaVersion"]
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    @pytest.mark.parametrize(
        "field_name",
        [
            "executable",
            "workingDirectory",
            "host",
            "port",
            "dev",
            "remote",
            "provider",
            "startedPid",
            "startedProcessTime",
            "writtenAt",
        ],
    )
    def test_missing_required_field_raises(self, field_name: str) -> None:
        data = _local_profile().to_dict()
        del data[field_name]
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_wrong_type_for_port_raises(self) -> None:
        data = _local_profile().to_dict()
        data["port"] = "8080"
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_wrong_type_for_dev_raises(self) -> None:
        data = _local_profile().to_dict()
        data["dev"] = "false"
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_invalid_provider_raises(self) -> None:
        data = _local_profile().to_dict()
        data["provider"] = "ngrok"
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_invalid_tunnel_ownership_raises(self) -> None:
        data = _remote_profile().to_dict()
        data["tunnelOwnership"] = "shared"
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_local_mode_with_nonlocal_provider_raises(self) -> None:
        """Impossible local/remote combination (AC 5): remote=false but provider != local."""
        data = _local_profile().to_dict()
        data["provider"] = "devtunnel"
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_local_mode_with_tunnel_ownership_raises(self) -> None:
        data = _local_profile().to_dict()
        data["tunnelOwnership"] = "managed"
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_remote_mode_without_tunnel_ownership_raises(self) -> None:
        data = _remote_profile().to_dict()
        data["tunnelOwnership"] = None
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_remote_mode_with_local_provider_raises(self) -> None:
        data = _remote_profile().to_dict()
        data["provider"] = "local"
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(data)

    def test_malformed_json_object_raises(self) -> None:
        with pytest.raises(LaunchProfileInvalidError):
            LaunchProfile.from_dict(["not", "an", "object"])

    def test_windows_native_path_preserved(self) -> None:
        """Native Windows paths must not be translated to POSIX form."""
        profile = _local_profile(
            executable="C:\\Users\\dev\\.venv\\Scripts\\python.exe",
            working_directory="C:\\Users\\dev\\codeplane",
        )
        data = json.loads(json.dumps(profile.to_dict()))
        restored = LaunchProfile.from_dict(data)
        assert restored.executable == "C:\\Users\\dev\\.venv\\Scripts\\python.exe"
        assert restored.working_directory == "C:\\Users\\dev\\codeplane"

    def test_posix_native_path_preserved(self) -> None:
        profile = _local_profile(executable="/usr/bin/python3", working_directory="/home/dev/codeplane")
        data = json.loads(json.dumps(profile.to_dict()))
        restored = LaunchProfile.from_dict(data)
        assert restored.executable == "/usr/bin/python3"
        assert restored.working_directory == "/home/dev/codeplane"


class TestSecretRedaction:
    def test_password_value_never_serialized(self) -> None:
        """Even though a sentinel password influenced classification upstream,
        the profile must only ever carry source references, never the value."""
        profile = _local_profile(password_source=SecretSource.unreplayable())
        serialized = json.dumps(profile.to_dict())
        assert SENTINEL_PASSWORD not in serialized

    def test_error_messages_never_contain_secret_values(self) -> None:
        try:
            SecretSource.from_dict({"kind": "resolvable", "provider": "environment", "reference": None})
        except LaunchProfileInvalidError as exc:
            assert SENTINEL_TOKEN not in str(exc)
        else:
            pytest.fail("expected LaunchProfileInvalidError")


class TestAtomicPersistence:
    def test_write_then_load_roundtrip(self, tmp_path: Path) -> None:
        profile = _remote_profile()
        write_active_launch_profile(profile)
        assert active_launch_profile_path() == tmp_path / "run.json"
        loaded = load_active_profile()
        assert loaded == profile

    def test_interrupted_write_leaves_previous_profile_intact(self, tmp_path: Path) -> None:
        first = _local_profile(started_pid=111)
        write_active_launch_profile(first)

        second = _local_profile(started_pid=222)
        with (
            patch("backend.services.dev_restart.restart_protocol.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError, match="boom"),
        ):
            write_active_launch_profile(second)

        # The previous complete profile must still be the one on disk.
        loaded = load_active_profile()
        assert loaded.started_pid == 111

    def test_temp_file_cleaned_up_on_failed_write(self, tmp_path: Path) -> None:
        with (
            patch("backend.services.dev_restart.restart_protocol.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError),
        ):
            write_active_launch_profile(_local_profile())
        leftovers = list(tmp_path.iterdir())
        assert leftovers == []

    def test_load_missing_profile_raises(self) -> None:
        with pytest.raises(LaunchProfileMissingError):
            load_active_profile()

    def test_load_malformed_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "run.json").write_text("{not valid", encoding="utf-8")
        with pytest.raises(LaunchProfileInvalidError):
            load_active_profile()


class TestValidateLaunchProfile:
    def test_valid_profile_passes(self) -> None:
        profile = _local_profile(started_pid=555)
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=True),
            patch("backend.cli._find_pids_on_port", return_value=[555]),
        ):
            validate_launch_profile(profile)  # must not raise

    def test_dead_pid_is_refused(self) -> None:
        profile = _local_profile(started_pid=555)
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=False),
            pytest.raises(LaunchProfileStaleError),
        ):
            validate_launch_profile(profile)

    def test_reused_pid_creation_time_mismatch_is_refused(self) -> None:
        """is_identity_alive already encodes the creation-time comparison;
        a mismatch must surface as a stale-profile refusal here."""
        profile = _local_profile(started_pid=555)
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=False),
            pytest.raises(LaunchProfileStaleError),
        ):
            validate_launch_profile(profile)

    def test_wrong_listener_owner_is_refused(self) -> None:
        """PID is alive, but a *different* process now owns the recorded port."""
        profile = _local_profile(started_pid=555)
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=True),
            patch("backend.cli._find_pids_on_port", return_value=[999]),
            pytest.raises(LaunchProfileStaleError),
        ):
            validate_launch_profile(profile)

    def test_required_unreplayable_password_is_refused(self) -> None:
        profile = _remote_profile(password_source=SecretSource.unreplayable())
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=True),
            patch("backend.cli._find_pids_on_port", return_value=[profile.started_pid]),
            pytest.raises(LaunchProfileInvalidError),
        ):
            validate_launch_profile(profile)

    def test_required_unreplayable_tunnel_credential_is_refused(self) -> None:
        profile = _remote_profile(tunnel_credential_source=SecretSource.unreplayable())
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=True),
            patch("backend.cli._find_pids_on_port", return_value=[profile.started_pid]),
            pytest.raises(LaunchProfileInvalidError),
        ):
            validate_launch_profile(profile)

    def test_unreplayable_secret_allowed_when_replay_check_opted_out(self) -> None:
        profile = _remote_profile(password_source=SecretSource.unreplayable())
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=True),
            patch("backend.cli._find_pids_on_port", return_value=[profile.started_pid]),
        ):
            validate_launch_profile(profile, require_replayable_secrets=False)  # must not raise

    def test_process_inspection_error_is_refused(self) -> None:
        """psutil.AccessDenied while inspecting the PID must fail closed, not crash."""
        profile = _local_profile(started_pid=555)
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=False),
            pytest.raises(LaunchProfileStaleError),
        ):
            validate_launch_profile(profile)


class TestProfileOwnsListener:
    def test_true_when_alive_and_owns_port(self) -> None:
        profile = _local_profile(started_pid=42, port=9000)
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=True),
            patch("backend.cli._find_pids_on_port", return_value=[42]),
        ):
            assert profile_owns_listener(profile) is True

    def test_false_when_not_alive(self) -> None:
        profile = _local_profile(started_pid=42, port=9000)
        with patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=False):
            assert profile_owns_listener(profile) is False

    def test_false_when_alive_but_different_owner(self) -> None:
        profile = _local_profile(started_pid=42, port=9000)
        with (
            patch("backend.services.dev_restart.launch_profile.is_identity_alive", return_value=True),
            patch("backend.cli._find_pids_on_port", return_value=[43]),
        ):
            assert profile_owns_listener(profile) is False
