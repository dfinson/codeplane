import pytest

from backend.services.adapters.capability_guard import enforce_command_capabilities, enforce_tool_capabilities
from backend.services.adapters.contracts import CapabilityDeniedError


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
