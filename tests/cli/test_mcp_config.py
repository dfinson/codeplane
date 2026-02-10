"""Tests for .vscode/mcp.json config management.

Covers _parse_jsonc, _get_mcp_server_name,
_ensure_vscode_mcp_config, and sync_vscode_mcp_port.
"""

import json
from pathlib import Path

from codeplane.cli.init import (
    _ensure_vscode_mcp_config,
    _get_mcp_server_name,
    _parse_jsonc,
    sync_vscode_mcp_port,
)

# ── _parse_jsonc ─────────────────────────────────────────────────────────────


class TestParseJsonc:
    """JSONC parsing including trailing commas and comments."""

    def test_valid_json(self) -> None:
        assert _parse_jsonc('{"a": 1}') == {"a": 1}

    def test_trailing_comma_object(self) -> None:
        assert _parse_jsonc('{"a": 1,}') == {"a": 1}

    def test_trailing_comma_array(self) -> None:
        assert _parse_jsonc('{"a": [1, 2,]}') == {"a": [1, 2]}

    def test_line_comment_removed(self) -> None:
        text = '{"key": "value"} // comment'
        assert _parse_jsonc(text) == {"key": "value"}

    def test_block_comment_removed(self) -> None:
        text = '{"key": /* inline */ "value"}'
        assert _parse_jsonc(text) == {"key": "value"}

    def test_url_inside_string_preserved(self) -> None:
        """URLs with // inside JSON strings must NOT be stripped."""
        text = '{"url": "http://127.0.0.1:3000/mcp"}'
        result = _parse_jsonc(text)
        assert result is not None
        assert result["url"] == "http://127.0.0.1:3000/mcp"

    def test_escaped_quote_inside_string(self) -> None:
        text = r'{"msg": "say \"hello\""}'
        result = _parse_jsonc(text)
        assert result == {"msg": 'say "hello"'}

    def test_comment_after_url_string(self) -> None:
        """Comment after a URL-containing string value is stripped."""
        text = '{"url": "http://example.com"} // trailing'
        result = _parse_jsonc(text)
        assert result is not None
        assert result["url"] == "http://example.com"

    def test_multiline_block_comment(self) -> None:
        text = '{\n/* multi\nline\ncomment */\n"key": 1}'
        result = _parse_jsonc(text)
        assert result == {"key": 1}

    def test_comments_plus_trailing_comma(self) -> None:
        text = """
        {
            // server config
            "servers": {
                "foo": {"cmd": "bar"},
            }
        }
        """
        result = _parse_jsonc(text)
        assert result is not None
        assert "foo" in result["servers"]

    def test_unparseable_returns_none(self) -> None:
        assert _parse_jsonc("not json at all") is None

    def test_empty_object(self) -> None:
        assert _parse_jsonc("{}") == {}

    def test_url_values_survive_round_trip(self) -> None:
        """Real-world mcp.json with URL args must parse correctly."""
        text = json.dumps(
            {
                "servers": {
                    "other-server": {
                        "command": "npx",
                        "args": ["-y", "mcp-remote", "http://127.0.0.1:9999/mcp"],
                    }
                }
            },
            indent=2,
        )
        result = _parse_jsonc(text)
        assert result is not None
        assert result["servers"]["other-server"]["args"][-1] == "http://127.0.0.1:9999/mcp"


# ── _get_mcp_server_name ─────────────────────────────────────────────────────


class TestGetMcpServerName:
    def test_simple_name(self, tmp_path: Path) -> None:
        repo = tmp_path / "myrepo"
        repo.mkdir()
        assert _get_mcp_server_name(repo) == "codeplane-myrepo"

    def test_hyphen_normalized(self, tmp_path: Path) -> None:
        repo = tmp_path / "my-repo"
        repo.mkdir()
        assert _get_mcp_server_name(repo) == "codeplane-my_repo"

    def test_dot_normalized(self, tmp_path: Path) -> None:
        repo = tmp_path / "my.repo"
        repo.mkdir()
        assert _get_mcp_server_name(repo) == "codeplane-my_repo"

    def test_uppercase_lowered(self, tmp_path: Path) -> None:
        repo = tmp_path / "MyRepo"
        repo.mkdir()
        assert _get_mcp_server_name(repo) == "codeplane-myrepo"


# ── _ensure_vscode_mcp_config ────────────────────────────────────────────────


class TestEnsureVscodeMcpConfig:
    """Ensures .vscode/mcp.json is created or updated correctly."""

    def test_creates_mcp_json_when_missing(self, tmp_path: Path) -> None:
        modified, name = _ensure_vscode_mcp_config(tmp_path, 3100)
        assert modified is True
        mcp = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        assert name in mcp["servers"]
        assert mcp["servers"][name]["args"][-1] == "http://127.0.0.1:3100/mcp"

    def test_no_op_when_config_matches(self, tmp_path: Path) -> None:
        _ensure_vscode_mcp_config(tmp_path, 3100)
        modified, _ = _ensure_vscode_mcp_config(tmp_path, 3100)
        assert modified is False

    def test_updates_port_when_different(self, tmp_path: Path) -> None:
        _ensure_vscode_mcp_config(tmp_path, 3100)
        modified, name = _ensure_vscode_mcp_config(tmp_path, 4200)
        assert modified is True
        mcp = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        assert mcp["servers"][name]["args"][-1] == "http://127.0.0.1:4200/mcp"

    def test_preserves_other_servers(self, tmp_path: Path) -> None:
        """Adding CodePlane entry must NOT remove existing servers."""
        vscode = tmp_path / ".vscode"
        vscode.mkdir(parents=True)
        existing = {
            "servers": {
                "my-other-mcp": {
                    "command": "node",
                    "args": ["server.js"],
                }
            }
        }
        (vscode / "mcp.json").write_text(json.dumps(existing, indent=2))

        modified, name = _ensure_vscode_mcp_config(tmp_path, 3100)
        assert modified is True
        mcp = json.loads((vscode / "mcp.json").read_text())
        # Both servers present
        assert "my-other-mcp" in mcp["servers"]
        assert name in mcp["servers"]
        # Original config intact
        assert mcp["servers"]["my-other-mcp"]["command"] == "node"

    def test_preserves_servers_with_url_args(self, tmp_path: Path) -> None:
        """Servers whose args contain URLs must not be corrupted."""
        vscode = tmp_path / ".vscode"
        vscode.mkdir(parents=True)
        existing = {
            "servers": {
                "remote-mcp": {
                    "command": "npx",
                    "args": ["-y", "mcp-remote", "http://10.0.0.5:8080/mcp"],
                }
            }
        }
        (vscode / "mcp.json").write_text(json.dumps(existing, indent=2))

        _ensure_vscode_mcp_config(tmp_path, 3100)
        mcp = json.loads((vscode / "mcp.json").read_text())
        assert mcp["servers"]["remote-mcp"]["args"][-1] == "http://10.0.0.5:8080/mcp"

    def test_handles_jsonc_with_comments(self, tmp_path: Path) -> None:
        """mcp.json with JSONC comments is parsed and updated correctly."""
        vscode = tmp_path / ".vscode"
        vscode.mkdir(parents=True)
        jsonc_content = """{
    // My servers
    "servers": {
        "existing": {
            "command": "node",
            "args": ["serve.js"] // inline comment
        }
    }
}
"""
        (vscode / "mcp.json").write_text(jsonc_content)

        modified, name = _ensure_vscode_mcp_config(tmp_path, 3100)
        assert modified is True
        mcp = json.loads((vscode / "mcp.json").read_text())
        assert "existing" in mcp["servers"]
        assert name in mcp["servers"]

    def test_handles_jsonc_trailing_commas(self, tmp_path: Path) -> None:
        vscode = tmp_path / ".vscode"
        vscode.mkdir(parents=True)
        jsonc_content = '{"servers": {"x": {"command": "y",},},}'
        (vscode / "mcp.json").write_text(jsonc_content)

        modified, name = _ensure_vscode_mcp_config(tmp_path, 3100)
        assert modified is True
        mcp = json.loads((vscode / "mcp.json").read_text())
        assert "x" in mcp["servers"]
        assert name in mcp["servers"]

    def test_unparseable_json_does_not_overwrite(self, tmp_path: Path) -> None:
        """Corrupt mcp.json must NOT be silently replaced."""
        vscode = tmp_path / ".vscode"
        vscode.mkdir(parents=True)
        original = "this is not json {{{["
        (vscode / "mcp.json").write_text(original)

        modified, _ = _ensure_vscode_mcp_config(tmp_path, 3100)
        assert modified is False
        # File unchanged
        assert (vscode / "mcp.json").read_text() == original

    def test_preserves_non_servers_keys(self, tmp_path: Path) -> None:
        """Top-level keys other than 'servers' are preserved."""
        vscode = tmp_path / ".vscode"
        vscode.mkdir(parents=True)
        existing = {
            "inputs": [{"id": "token", "type": "promptString"}],
            "servers": {},
        }
        (vscode / "mcp.json").write_text(json.dumps(existing, indent=2))

        _ensure_vscode_mcp_config(tmp_path, 3100)
        mcp = json.loads((vscode / "mcp.json").read_text())
        assert "inputs" in mcp
        assert mcp["inputs"] == [{"id": "token", "type": "promptString"}]


# ── sync_vscode_mcp_port ─────────────────────────────────────────────────────


class TestSyncVscodeMcpPort:
    """Port sync for 'cpl up'."""

    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        assert sync_vscode_mcp_port(tmp_path, 3100) is True
        assert (tmp_path / ".vscode" / "mcp.json").exists()

    def test_no_op_when_port_matches(self, tmp_path: Path) -> None:
        _ensure_vscode_mcp_config(tmp_path, 3100)
        assert sync_vscode_mcp_port(tmp_path, 3100) is False

    def test_updates_port(self, tmp_path: Path) -> None:
        _ensure_vscode_mcp_config(tmp_path, 3100)
        name = _get_mcp_server_name(tmp_path)
        assert sync_vscode_mcp_port(tmp_path, 5000) is True
        mcp = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
        assert mcp["servers"][name]["args"][-1] == "http://127.0.0.1:5000/mcp"

    def test_adds_entry_when_missing_from_existing_file(self, tmp_path: Path) -> None:
        """If mcp.json exists but has no CodePlane entry, adds it."""
        vscode = tmp_path / ".vscode"
        vscode.mkdir(parents=True)
        existing = {"servers": {"other": {"command": "x"}}}
        (vscode / "mcp.json").write_text(json.dumps(existing))

        assert sync_vscode_mcp_port(tmp_path, 3100) is True
        mcp = json.loads((vscode / "mcp.json").read_text())
        name = _get_mcp_server_name(tmp_path)
        assert name in mcp["servers"]
        assert "other" in mcp["servers"]

    def test_preserves_other_servers_on_port_update(self, tmp_path: Path) -> None:
        vscode = tmp_path / ".vscode"
        vscode.mkdir(parents=True)
        name = _get_mcp_server_name(tmp_path)
        existing = {
            "servers": {
                name: {
                    "command": "npx",
                    "args": ["-y", "mcp-remote", "http://127.0.0.1:3100/mcp"],
                },
                "keep-me": {"command": "node", "args": ["s.js"]},
            }
        }
        (vscode / "mcp.json").write_text(json.dumps(existing, indent=2))

        assert sync_vscode_mcp_port(tmp_path, 4200) is True
        mcp = json.loads((vscode / "mcp.json").read_text())
        assert mcp["servers"]["keep-me"]["command"] == "node"
        assert mcp["servers"][name]["args"][-1] == "http://127.0.0.1:4200/mcp"

    def test_unparseable_json_does_not_overwrite(self, tmp_path: Path) -> None:
        vscode = tmp_path / ".vscode"
        vscode.mkdir(parents=True)
        original = "{broken"
        (vscode / "mcp.json").write_text(original)

        assert sync_vscode_mcp_port(tmp_path, 3100) is False
        assert (vscode / "mcp.json").read_text() == original
