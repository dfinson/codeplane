import pytest

from backend.services.adapters.capability_guard import (
    enforce_command_capabilities,
    enforce_invocation_capabilities,
    enforce_tool_capabilities,
    excluded_tools_for_capabilities,
    invocation_permission_request,
)
from backend.services.adapters.contracts import CapabilityDeniedError


@pytest.mark.parametrize("as_list", [False, True])
async def test_native_search_paths_are_preserved_and_contained(tmp_path, as_list) -> None:
    inside = str(tmp_path / "source.py")
    supplied = [inside] if as_list else inside
    request = invocation_permission_request(str(tmp_path), "grep", {"paths": supplied, "pattern": "fixture"})
    assert request.kind == "read" and request.possible_paths == [inside]
    await enforce_invocation_capabilities(
        frozenset({"read"}), workspace=str(tmp_path), tool_name="grep", arguments={"paths": supplied}
    )
    outside = str(tmp_path.parent / "outside")
    with pytest.raises(CapabilityDeniedError):
        await enforce_invocation_capabilities(
            frozenset({"read"}),
            workspace=str(tmp_path),
            tool_name="grep",
            arguments={"paths": [inside, outside] if as_list else outside},
        )


@pytest.mark.parametrize("paths", [123, ["source.py", None], {"path": "source.py"}])
def test_malformed_native_search_paths_are_not_silently_discarded(tmp_path, paths) -> None:
    with pytest.raises(CapabilityDeniedError):
        invocation_permission_request(str(tmp_path), "grep", {"paths": paths})


@pytest.mark.parametrize("pattern", ["**/*", "src/**/*.py"])
async def test_native_glob_resolves_search_root_not_windows_wildcard_filename(tmp_path, pattern) -> None:
    await enforce_invocation_capabilities(
        frozenset({"read"}),
        workspace=str(tmp_path),
        tool_name="glob",
        arguments={"paths": ["."], "pattern": pattern},
    )


@pytest.mark.parametrize("pattern", ["../outside/*.py", "**/../outside", "{src,../outside}/*.py"])
async def test_native_glob_does_not_authorize_escaping_patterns(tmp_path, pattern) -> None:
    with pytest.raises(CapabilityDeniedError):
        await enforce_invocation_capabilities(
            frozenset({"read"}),
            workspace=str(tmp_path),
            tool_name="glob",
            arguments={"paths": ["."], "pattern": pattern},
        )


async def test_shell_capability_does_not_authorize_writes_or_network(tmp_path) -> None:
    for command in ("echo x > output", "curl example.test", "git status", "printf hello"):
        with pytest.raises(CapabilityDeniedError):
            await enforce_command_capabilities(
                frozenset({"read", "shell"}), workspace=str(tmp_path), command=command, dialect="bash"
            )


async def test_only_trusted_pure_builtins_can_prove_restricted_shell_read(tmp_path) -> None:
    await enforce_command_capabilities(
        frozenset({"read", "shell"}),
        workspace=str(tmp_path),
        command="printf hello",
        dialect="bash",
        trusted_builtins=True,
    )
    for command in ("printf -v target hello", "printf $(curl example.test)", "printf x > output"):
        with pytest.raises(CapabilityDeniedError):
            await enforce_command_capabilities(
                frozenset({"read", "shell"}),
                workspace=str(tmp_path),
                command=command,
                dialect="bash",
                trusted_builtins=True,
            )


async def test_read_only_argv_is_not_inferred_from_executable_name(tmp_path) -> None:
    with pytest.raises(CapabilityDeniedError):
        await enforce_command_capabilities(
            frozenset({"read", "shell"}),
            workspace=str(tmp_path),
            argv=["git", "status"],
        )


async def test_unknown_tools_and_external_reads_cannot_bypass_grant(tmp_path) -> None:
    for name, kind, paths in [
        ("mcp_untrusted", "mcp", ()),
        ("view", "mcp", ()),
        ("WebFetch", "read", ()),
        ("Read", "read", (str(tmp_path.parent / "external"),)),
    ]:
        with pytest.raises(CapabilityDeniedError):
            await enforce_tool_capabilities(
                frozenset({"read"}),
                workspace=str(tmp_path),
                kind=kind,
                tool_name=name,
                paths=paths,
            )
    await enforce_tool_capabilities(
        frozenset({"read"}),
        workspace=str(tmp_path),
        kind="read",
        tool_name="Read",
        paths=("file.txt",),
    )


async def test_peer_tools_use_narrow_capabilities_only_as_custom_tools(tmp_path) -> None:
    read_delegate = frozenset({"read", "delegate"})
    for name in ("codeplane_list_peer_sessions", "codeplane_send_peer_message"):
        await enforce_invocation_capabilities(read_delegate, workspace=str(tmp_path), tool_name=name, arguments={})
        request = invocation_permission_request(str(tmp_path), name, {})
        await enforce_tool_capabilities(read_delegate, workspace=str(tmp_path), kind=request.kind, tool_name=name)
    await enforce_invocation_capabilities(
        frozenset({"read"}), workspace=str(tmp_path), tool_name="codeplane_list_peer_sessions", arguments={}
    )
    for capabilities, name, kind in [
        (frozenset({"read"}), "codeplane_send_peer_message", "custom-tool"),
        (frozenset({"delegate"}), "codeplane_list_peer_sessions", "custom-tool"),
        (read_delegate, "codeplane_send_peer_message", "mcp"),
        (read_delegate, "codeplane_other_tool", "custom-tool"),
    ]:
        with pytest.raises(CapabilityDeniedError):
            await enforce_tool_capabilities(capabilities, workspace=str(tmp_path), kind=kind, tool_name=name)
    excluded = excluded_tools_for_capabilities(frozenset({"read"}), [])
    assert "codeplane_send_peer_message" in excluded
    assert "codeplane_list_peer_sessions" not in excluded
