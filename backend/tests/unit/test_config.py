"""Tests for configuration loading and initialization."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from backend.config import CPLConfig, init_config, load_config, save_config

if TYPE_CHECKING:
    from typing import Any


class TestUtilityModelContract:
    def test_text_provider_override_roundtrips_without_retargeting_execution(self, tmp_path: Path) -> None:
        path = tmp_path / "utility-provider.yaml"
        path.write_text(yaml.safe_dump({
            "runtime": {"default_sdk": "copilot", "copilot_only": False,
                        "utility_sdk": "claude", "utility_model": "claude-explicit"},
        }), encoding="utf-8")
        config = load_config(path)
        save_config(config, path)
        restored = load_config(path)
        assert restored.runtime.utility_sdk == "claude"
        assert restored.runtime.utility_model == "claude-explicit"
        assert restored.runtime.default_sdk == "copilot"

    @pytest.mark.parametrize("invalid", ["", " ", 42, False])
    def test_invalid_text_provider_identifier_is_rejected(self, invalid) -> None:
        from backend.config import RuntimeConfig

        with pytest.raises(ValueError, match="utility_sdk"):
            RuntimeConfig(utility_sdk=invalid)

    def test_blank_text_model_uses_the_actual_provider_default(self, monkeypatch) -> None:
        import backend.config as config_module

        monkeypatch.setattr(config_module, "_DEFAULT_COPILOT_UTILITY_MODEL", "copilot-provider-default")
        config = config_module.RuntimeConfig(
            default_sdk="copilot", utility_sdk="claude", copilot_only=False,
        )
        assert config.utility_model == "claude-haiku-4.5"
        assert config.default_sdk == "copilot"
        config = config_module.RuntimeConfig(
            default_sdk="claude", utility_sdk="copilot", copilot_only=False,
        )
        assert config.utility_model == "copilot-provider-default"
        assert config.default_sdk == "claude"

    @pytest.mark.parametrize("configured", [None, "", " \t "])
    @pytest.mark.parametrize("default_sdk,copilot_only", [("copilot", True), ("copilot", False), ("claude", True)])
    def test_blank_utility_uses_approved_copilot_default(
        self, tmp_path: Path, configured, default_sdk: str, copilot_only: bool
    ) -> None:
        from backend.services.adapters.integration_policy import IntegrationPolicy

        path = tmp_path / "utility.yaml"
        path.write_text(
            yaml.safe_dump(
                {"runtime": {"default_sdk": default_sdk, "copilot_only": copilot_only, "utility_model": configured}}
            ),
            encoding="utf-8",
        )
        config = load_config(path)
        assert config.runtime.utility_model == "claude-haiku-4.5"
        assert config.runtime.default_sdk == default_sdk
        assert config.runtime.copilot_only is copilot_only
        assert IntegrationPolicy(config).default_sdk == "copilot"

    def test_missing_utility_and_new_config_use_approved_default(self, tmp_path: Path) -> None:
        assert load_config(tmp_path / "missing.yaml").runtime.utility_model == "claude-haiku-4.5"
        path = tmp_path / "new.yaml"
        init_config(path)
        assert load_config(path).runtime.utility_model == "claude-haiku-4.5"

    @pytest.mark.parametrize("configured", ["gpt-5-mini", "claude-opus-4.5", "  unavailable/vendor-model  "])
    @pytest.mark.parametrize("default_sdk,copilot_only", [("copilot", True), ("claude", False)])
    def test_explicit_utility_survives_load_and_save_verbatim(
        self, tmp_path: Path, configured: str, default_sdk: str, copilot_only: bool
    ) -> None:
        path = tmp_path / "explicit.yaml"
        path.write_text(
            yaml.safe_dump(
                {"runtime": {"default_sdk": default_sdk, "copilot_only": copilot_only, "utility_model": configured}}
            ),
            encoding="utf-8",
        )
        config = load_config(path)
        assert config.runtime.utility_model == configured
        save_config(config, path)
        assert load_config(path).runtime.utility_model == configured
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["runtime"]["utility_model"] == configured

    def test_flag_off_retains_independent_non_copilot_default(self, monkeypatch) -> None:
        import backend.config as config_module
        from backend.services.adapters.integration_policy import IntegrationPolicy

        monkeypatch.setattr(config_module, "_DEFAULT_COPILOT_UTILITY_MODEL", "future-copilot-default", raising=False)
        config = CPLConfig(runtime=config_module.RuntimeConfig(default_sdk="claude", copilot_only=False))
        assert IntegrationPolicy(config).default_sdk == "claude"
        assert config.runtime.utility_model == "claude-haiku-4.5"
        restricted = config_module.RuntimeConfig(default_sdk="claude", copilot_only=True)
        assert restricted.utility_model == "future-copilot-default"
        assert restricted.default_sdk == "claude"

    def test_primary_job_model_is_independent_of_utility_default(self, tmp_path: Path, monkeypatch) -> None:
        from datetime import UTC, datetime

        import backend.config as config_module
        from backend.models.domain import Job, JobState

        monkeypatch.setattr(config_module, "discover_mcp_servers", lambda *args: {})
        monkeypatch.setattr(config_module, "resolve_protected_paths", lambda *args: [])
        job = Job(
            worktree_path=None,
            repo=str(tmp_path),
            prompt="Primary task",
            id="primary",
            model="primary-model",
            sdk="copilot",
            project_id="project", state=JobState.queued, base_ref="main", branch=None,
            session_id=None, created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        config = CPLConfig()
        session = config_module.build_session_config(job, config)
        assert session.model == "primary-model"
        assert job.model == "primary-model"
        assert config.runtime.utility_model == "claude-haiku-4.5"


def test_load_config_returns_defaults_when_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nonexistent.yaml")
    assert isinstance(config, CPLConfig)
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8080
    assert config.runtime.max_concurrent_jobs == 2
    assert config.repos == []


def test_load_config_parses_yaml(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """\
server:
  host: 0.0.0.0
  port: 9090

runtime:
  max_concurrent_jobs: 4

repos:
  - /repos/a
  - /repos/b
"""
    )
    config = load_config(cfg_file)
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 9090
    assert config.runtime.max_concurrent_jobs == 4
    assert config.repos == ["/repos/a", "/repos/b"]


def test_load_config_ignores_unknown_keys(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """\
server:
  host: 127.0.0.1
  unknown_key: value

future_section:
  something: true
"""
    )
    config = load_config(cfg_file)
    assert config.server.host == "127.0.0.1"


def test_init_config_creates_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sub" / "config.yaml"
    result = init_config(cfg_path)
    assert result == cfg_path
    assert cfg_path.exists()
    content = cfg_path.read_text()
    assert "server:" in content
    assert "host: 127.0.0.1" in content
    assert "coderecon" not in content.lower()


def test_init_config_roundtrips(tmp_path: Path) -> None:
    """Config generated by init can be loaded back."""
    cfg_path = tmp_path / "config.yaml"
    init_config(cfg_path)
    config = load_config(cfg_path)
    assert config.server.port == 8080
    assert config.runtime.worktrees_dirname == ".codeplane-worktrees"
    assert config.retention.artifact_retention_days == 30


def test_load_config_removes_retired_tracker_interval(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("telemetry:\n  instance_id: existing-instance\ntracker:\n  poll_interval_seconds: 90\n")
    config = load_config(cfg_path)
    assert not hasattr(config, "tracker")
    assert "tracker" not in cfg_path.read_text()


# ---------------------------------------------------------------------------
# TelemetryConfig
# ---------------------------------------------------------------------------


def test_telemetry_defaults(tmp_path: Path) -> None:
    """Default TelemetryConfig has zero daily_spend_limit and empty instance_id field."""
    cfg = CPLConfig()
    assert cfg.telemetry.daily_spend_limit_usd == 0.0
    assert cfg.telemetry.instance_id == ""  # before load_config auto-generates it


def test_instance_id_auto_generated(tmp_path: Path) -> None:
    """load_config auto-generates a UUID instance_id when not set in YAML."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("server:\n  port: 8080\n")
    config = load_config(cfg_path)
    assert config.telemetry.instance_id != ""
    # Should look like a UUID
    assert len(config.telemetry.instance_id) == 36
    assert config.telemetry.instance_id.count("-") == 4


def test_instance_id_stable_across_loads(tmp_path: Path) -> None:
    """Once auto-generated, instance_id is persisted and stable across reloads."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("server:\n  port: 8080\n")
    config1 = load_config(cfg_path)
    config2 = load_config(cfg_path)
    assert config1.telemetry.instance_id == config2.telemetry.instance_id


def test_daily_spend_limit_roundtrip(tmp_path: Path) -> None:
    """daily_spend_limit_usd survives a save and load roundtrip."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("server:\n  port: 8080\n")
    config = load_config(cfg_path)
    config.telemetry.daily_spend_limit_usd = 5.0
    save_config(config, cfg_path)

    reloaded = load_config(cfg_path)
    assert reloaded.telemetry.daily_spend_limit_usd == 5.0


def test_telemetry_section_parsed_from_yaml(tmp_path: Path) -> None:
    """Telemetry section in YAML is properly parsed."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """\
telemetry:
  daily_spend_limit_usd: 10.0
  instance_id: "my-instance-id"
  otel_exporter_endpoint: "http://localhost:4317"
"""
    )
    config = load_config(cfg_path)
    assert config.telemetry.daily_spend_limit_usd == 10.0
    assert config.telemetry.instance_id == "my-instance-id"
    assert config.telemetry.otel_exporter_endpoint == "http://localhost:4317"


def _retired_config(path: Path) -> dict[str, Any]:
    data = {
        "coderecon": {"splade": True, "cross_encoder": True, "legacy_unknown": "discard"},
        "server": {"host": "127.0.0.1", "port": 9090, "future_server_key": "preserve"},
        "runtime": {
            "utility_model": "utility-model",
            "default_sdk": "copilot",
            "suppressed_preflight_agent_prompts": ["keep-sdk-warning-preference"],
        },
        "telemetry": {"instance_id": "existing-instance", "daily_spend_limit_usd": 3.25},
        "repos": ["repo-a", "repo-b"],
        "mcp_servers": {"ordinary": {"command": "fixture-tool", "args": ["--read"]}},
        "future_section": {"coderecon": "not a root section", "nested": [1, False, None]},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return data


def _link_config_directory(selected: Path, target: Path) -> None:
    if os.name == "nt":
        # Junctions exercise resolved-target handling without requiring Windows symlink privileges.
        selected_arg, target_arg = str(selected).replace("'", "''"), str(target).replace("'", "''")
        subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "$ErrorActionPreference = 'Stop'; "
                f"New-Item -ItemType Junction -Path '{selected_arg}' -Target '{target_arg}' | Out-Null",
            ],
            check=True,
            capture_output=True,
        )
    else:
        selected.symlink_to(target, target_is_directory=True)


def test_retired_analysis_section_removed_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    expected = _retired_config(cfg_path)
    expected.pop("coderecon")
    original = cfg_path.read_bytes()
    real_replace, real_fsync = os.replace, os.fsync
    replacements: list[Path] = []
    synced: list[int] = []

    def fsync(fd: int) -> None:
        assert os.fstat(fd).st_size > 0, "The temporary output must be flushed before fsync"
        synced.append(fd)
        real_fsync(fd)

    def replace(src: Any, dst: Any) -> None:
        source, target = Path(src), Path(dst)
        assert target == cfg_path.resolve()
        assert source.parent == target.parent and source != target
        assert cfg_path.read_bytes() == original, "Do not truncate the selected file before replacement"
        assert yaml.safe_load(source.read_text(encoding="utf-8")) == expected
        assert synced, "Retirement must fsync its temporary output before replacing the config"
        replacements.append(source)
        real_replace(src, dst)

    monkeypatch.setattr("backend.config.os.fsync", fsync)
    monkeypatch.setattr("backend.config.os.replace", replace)
    config = load_config(cfg_path)
    assert yaml.safe_load(cfg_path.read_text(encoding="utf-8")) == expected, "The root coderecon section must retire"
    assert not hasattr(config, "coderecon")
    assert config.runtime.utility_model == "utility-model"
    assert config.runtime.suppressed_preflight_agent_prompts == ["keep-sdk-warning-preference"]
    assert len(replacements) == 1
    assert set(tmp_path.iterdir()) == {cfg_path}

    retired_bytes, retired_mtime = cfg_path.read_bytes(), cfg_path.stat().st_mtime_ns
    load_config(cfg_path)
    assert cfg_path.read_bytes() == retired_bytes
    assert cfg_path.stat().st_mtime_ns == retired_mtime
    assert len(replacements) == 1, "An already retired config must not be rewritten"


def test_retirement_preserves_selected_link_and_unrelated_target_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "settings"
    target_dir.mkdir()
    target = target_dir / "actual.yaml"
    expected = _retired_config(target)
    expected.pop("coderecon")
    selected_dir = tmp_path / "selected"
    _link_config_directory(selected_dir, target_dir)
    selected = selected_dir / target.name
    original_link = os.readlink(selected_dir)
    real_replace = os.replace
    replacements: list[Path] = []

    def replace(src: Any, dst: Any) -> None:
        assert Path(dst) == target.resolve()
        assert Path(src).parent == target.parent
        replacements.append(Path(src))
        real_replace(src, dst)

    monkeypatch.setattr("backend.config.os.replace", replace)
    load_config(selected)
    assert os.readlink(selected_dir) == original_link
    assert selected.resolve() == target.resolve()
    assert yaml.safe_load(target.read_text(encoding="utf-8")) == expected
    assert len(replacements) == 1
    assert set(target_dir.iterdir()) == {target}


@pytest.mark.parametrize("operation", ["load", "save"])
@pytest.mark.parametrize("contents", ["broken: [\n", "- value\n", "[]\n", "false\n", "null\n"])
def test_retirement_rejects_malformed_or_non_mapping_config_without_overwrite(
    tmp_path: Path, contents: str, operation: str,
) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(contents, encoding="utf-8")
    before = cfg_path.read_bytes()
    with pytest.raises((yaml.YAMLError, ValueError, TypeError, RuntimeError)):
        if operation == "load":
            load_config(cfg_path)
        else:
            save_config(CPLConfig(), cfg_path)
    assert cfg_path.read_bytes() == before
    assert set(tmp_path.iterdir()) == {cfg_path}


@pytest.mark.parametrize("missing_instance_id", [False, True])
def test_retirement_replace_failure_surfaces_and_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_instance_id: bool,
) -> None:
    cfg_path = tmp_path / "config.yaml"
    data = _retired_config(cfg_path)
    if missing_instance_id:
        data["telemetry"].pop("instance_id")
        cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = cfg_path.read_bytes()

    def fail_replace(src: Any, dst: Any) -> None:
        assert cfg_path.read_bytes() == before
        raise OSError("injected replacement failure")

    monkeypatch.setattr("backend.config.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected replacement failure"):
        load_config(cfg_path)
    assert cfg_path.read_bytes() == before
    assert set(tmp_path.iterdir()) == {cfg_path}


def test_retirement_detects_concurrent_same_size_input_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_path = tmp_path / "config.yaml"
    _retired_config(cfg_path)
    before = cfg_path.read_bytes()
    stat = cfg_path.stat()
    concurrent = before.replace(b"9090", b"9191")
    real_fsync = os.fsync
    changed = False

    def change_after_fsync(fd: int) -> None:
        nonlocal changed
        real_fsync(fd)
        if not changed:
            cfg_path.write_bytes(concurrent)
            os.utime(cfg_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            changed = True

    monkeypatch.setattr("backend.config.os.fsync", change_after_fsync)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        load_config(cfg_path)
    assert changed, "Inject the race only after temporary output is durable"
    assert cfg_path.read_bytes() == concurrent, "Do not overwrite another writer's update"
    assert set(tmp_path.iterdir()) == {cfg_path}


def test_retirement_detects_selected_link_retargeting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first, second = first_dir / "config.yaml", second_dir / "config.yaml"
    _retired_config(first)
    _retired_config(second)
    selected_dir = tmp_path / "selected"
    _link_config_directory(selected_dir, first_dir)
    selected = selected_dir / "config.yaml"
    before_first, before_second = first.read_bytes(), second.read_bytes()
    real_fsync = os.fsync
    retargeted = False

    def retarget_after_fsync(fd: int) -> None:
        nonlocal retargeted
        real_fsync(fd)
        if not retargeted:
            if os.name == "nt":
                selected_dir.rmdir()
            else:
                selected_dir.unlink()
            _link_config_directory(selected_dir, second_dir)
            retargeted = True

    monkeypatch.setattr("backend.config.os.fsync", retarget_after_fsync)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        load_config(selected)
    assert retargeted
    assert os.readlink(selected_dir)
    assert selected.resolve() == second.resolve()
    assert first.read_bytes() == before_first
    assert second.read_bytes() == before_second
    assert set(tmp_path.iterdir()) == {first_dir, second_dir, selected_dir}
    assert set(first_dir.iterdir()) == {first}
    assert set(second_dir.iterdir()) == {second}


def test_settings_save_retires_restored_legacy_section_without_losing_other_values(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    expected = _retired_config(cfg_path)
    config = load_config(cfg_path)
    # Simulate an older config restored after the application loaded its settings.
    cfg_path.write_text(yaml.safe_dump(expected), encoding="utf-8")
    config.telemetry.daily_spend_limit_usd = 9.5
    save_config(config, cfg_path)
    result = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "coderecon" not in result
    assert result["telemetry"]["daily_spend_limit_usd"] == 9.5
    assert result["runtime"]["utility_model"] == expected["runtime"]["utility_model"]
    assert result["runtime"]["suppressed_preflight_agent_prompts"] == ["keep-sdk-warning-preference"]
    for key in ("repos", "mcp_servers", "future_section"):
        assert result[key] == expected[key]
