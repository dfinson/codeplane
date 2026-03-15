"""Red-team / pressure tests for configuration loading (Phase 1).

Covers: malformed YAML, type mismatches, path traversal, injection,
extreme values, and edge-case inputs for every config section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from backend.config import TowerConfig, init_config, load_config

if TYPE_CHECKING:
    from pathlib import Path


# ── Malformed YAML ───────────────────────────────────────────────


class TestMalformedYAML:
    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("")
        config = load_config(f)
        assert isinstance(config, TowerConfig)
        assert config.server.host == "127.0.0.1"

    def test_null_content(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("null\n")
        config = load_config(f)
        assert isinstance(config, TowerConfig)

    def test_yaml_just_whitespace(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("   \n\n  \n")
        config = load_config(f)
        assert isinstance(config, TowerConfig)

    def test_invalid_yaml_syntax(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server:\n  host: [unclosed bracket\n")
        with pytest.raises(Exception):  # noqa: B017
            load_config(f)

    def test_yaml_with_tabs(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server:\n\thost: 0.0.0.0\n")
        # Tabs in YAML are invalid — should raise
        with pytest.raises(Exception):  # noqa: B017
            load_config(f)

    def test_binary_content(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_bytes(b"\x00\x01\x02\x03\xff\xfe")
        with pytest.raises(Exception):  # noqa: B017
            load_config(f)

    def test_yaml_scalar_instead_of_mapping(self, tmp_path: Path) -> None:
        """Config file that's a plain string instead of a dict."""
        f = tmp_path / "config.yaml"
        f.write_text("just a plain string\n")
        # raw is a string, not dict, so .get() will fail
        with pytest.raises(Exception):  # noqa: B017
            load_config(f)

    def test_yaml_list_instead_of_mapping(self, tmp_path: Path) -> None:
        """Config file that's a list instead of a dict."""
        f = tmp_path / "config.yaml"
        f.write_text("- item1\n- item2\n")
        with pytest.raises(Exception):  # noqa: B017
            load_config(f)


# ── Type mismatches ──────────────────────────────────────────────


class TestTypeMismatches:
    def test_port_as_string(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server:\n  port: 'not_a_number'\n")
        config = load_config(f)
        # Should use the string value (dataclass doesn't validate types)
        # This is a potential issue — port should be an int
        assert config.server.port == "not_a_number"  # type: ignore[comparison-overlap]

    def test_port_negative(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server:\n  port: -1\n")
        config = load_config(f)
        assert config.server.port == -1

    def test_port_zero(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server:\n  port: 0\n")
        config = load_config(f)
        assert config.server.port == 0

    def test_port_above_65535(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server:\n  port: 99999\n")
        config = load_config(f)
        assert config.server.port == 99999

    def test_max_concurrent_jobs_negative(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("runtime:\n  max_concurrent_jobs: -5\n")
        config = load_config(f)
        assert config.runtime.max_concurrent_jobs == -5

    def test_max_concurrent_jobs_zero(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("runtime:\n  max_concurrent_jobs: 0\n")
        config = load_config(f)
        assert config.runtime.max_concurrent_jobs == 0

    def test_max_concurrent_jobs_massive(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("runtime:\n  max_concurrent_jobs: 999999999\n")
        config = load_config(f)
        assert config.runtime.max_concurrent_jobs == 999999999

    def test_section_as_scalar(self, tmp_path: Path) -> None:
        """What if 'server' is a string instead of a mapping?"""
        f = tmp_path / "config.yaml"
        f.write_text("server: just_a_string\n")
        config = load_config(f)
        # _parse_section handles non-dict gracefully
        assert config.server.host == "127.0.0.1"  # default

    def test_section_as_list(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server:\n  - 127.0.0.1\n  - 8080\n")
        config = load_config(f)
        assert config.server.host == "127.0.0.1"  # default

    def test_host_as_integer(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server:\n  host: 12345\n")
        config = load_config(f)
        assert config.server.host == 12345  # type: ignore[comparison-overlap]

    def test_boolean_as_string(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("retention:\n  cleanup_on_startup: 'yes'\n")
        config = load_config(f)
        # YAML 'yes' is bool True when unquoted; quoted is string
        assert config.retention.cleanup_on_startup == "yes"  # type: ignore[comparison-overlap]


# ── Repos field edge cases ───────────────────────────────────────


class TestReposField:
    def test_repos_as_string(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("repos: /single/path\n")
        config = load_config(f)
        # repos is a string, not a list — should be coerced to empty
        assert config.repos == []

    def test_repos_as_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("repos:\n  key: value\n")
        config = load_config(f)
        assert config.repos == []

    def test_repos_with_none_entries(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("repos:\n  - /good/path\n  - null\n  - /other\n")
        config = load_config(f)
        # None entries should be filtered out
        assert None not in config.repos
        assert len(config.repos) == 2

    def test_repos_with_integer_entries(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("repos:\n  - 12345\n  - /real/path\n")
        config = load_config(f)
        # Integers should be coerced to strings
        assert all(isinstance(r, str) for r in config.repos)

    def test_repos_with_empty_strings(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("repos:\n  - ''\n  - /real/path\n")
        config = load_config(f)
        assert "" in config.repos  # acceptable? empty string gets through

    def test_repos_with_path_traversal(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("repos:\n  - /../../../etc/shadow\n")
        config = load_config(f)
        # Config loads it as-is — validation is the caller's responsibility
        assert len(config.repos) == 1

    def test_repos_with_nested_list(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("repos:\n  - [nested, list]\n")
        config = load_config(f)
        # str() of a list becomes "['nested', 'list']"
        assert len(config.repos) == 1

    def test_repos_very_large_list(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        lines = ["repos:"] + [f"  - /repo/{i}" for i in range(10000)]
        f.write_text("\n".join(lines))
        config = load_config(f)
        assert len(config.repos) == 10000

    def test_repos_as_number(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("repos: 42\n")
        config = load_config(f)
        assert config.repos == []

    def test_repos_as_true(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("repos: true\n")
        config = load_config(f)
        assert config.repos == []


# ── YAML injection / advanced ────────────────────────────────────


class TestYAMLInjection:
    def test_yaml_anchors_and_aliases(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server: &anchor\n  host: 0.0.0.0\nruntime: *anchor\n")
        config = load_config(f)
        # Runtime section gets server's dict — _parse_section should ignore invalid keys
        assert config.runtime.max_concurrent_jobs == 2  # default

    def test_yaml_merge_key(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("defaults: &d\n  host: 0.0.0.0\nserver:\n  <<: *d\n  port: 9090\n")
        config = load_config(f)
        assert config.server.host == "0.0.0.0"
        assert config.server.port == 9090

    def test_extremely_long_string_value(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text(f"server:\n  host: {'A' * 1_000_000}\n")
        config = load_config(f)
        assert len(config.server.host) == 1_000_000

    def test_unicode_values(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("server:\n  host: '日本語ホスト'\n")
        config = load_config(f)
        assert config.server.host == "日本語ホスト"

    def test_special_yaml_values(self, tmp_path: Path) -> None:
        """YAML has special values like .inf, .nan, etc."""
        f = tmp_path / "config.yaml"
        f.write_text("server:\n  port: .inf\n")
        config = load_config(f)
        # .inf is parsed as float inf by YAML
        import math

        assert math.isinf(config.server.port)


# ── init_config edge cases ───────────────────────────────────────


class TestInitConfig:
    def test_init_in_deeply_nested_path(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c" / "d" / "config.yaml"
        result = init_config(target)
        assert result.exists()

    def test_init_config_content_is_valid_yaml(self, tmp_path: Path) -> None:
        import yaml

        target = tmp_path / "config.yaml"
        init_config(target)
        data = yaml.safe_load(target.read_text())
        assert isinstance(data, dict)
        assert "server" in data

    def test_init_preserves_existing_config(self, tmp_path: Path) -> None:
        """init_config never overwrites an existing config file."""
        target = tmp_path / "config.yaml"
        target.write_text("custom: true\n")
        init_config(target)
        assert "custom" in target.read_text()  # preserved
