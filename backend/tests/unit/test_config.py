"""Tests for configuration loading and initialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from backend.config import CPLConfig, init_config, load_config, save_config

if TYPE_CHECKING:
    from pathlib import Path


class TestUtilityModelContract:
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
        from types import SimpleNamespace

        import backend.config as config_module

        monkeypatch.setattr(config_module, "discover_mcp_servers", lambda *args: {})
        monkeypatch.setattr(config_module, "resolve_protected_paths", lambda *args: [])
        job = SimpleNamespace(
            worktree_path=None,
            repo=str(tmp_path),
            prompt="Primary task",
            id="primary",
            model="primary-model",
            sdk="copilot",
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
    """daily_spend_limit_usd survives save → load roundtrip."""
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
