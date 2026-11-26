"""Enforce scoped grants before adapter tools or trusted command runners execute."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from backend.services.action_policy.syntax import analyze_syntax_async
from backend.services.adapters.contracts import Capability, CapabilityDeniedError, CapabilityGrant
from backend.services.auth.permission_policy import PermissionRequest
from backend.services.auth.workspace_paths import workspace_glob_root, workspace_target

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    from backend.services.action_policy.syntax import SyntaxFacts

_READ_TOOLS = frozenset(
    {
        "read",
        "view",
        "read_file",
        "read_files",
        "open_file",
        "glob",
        "grep",
        "rg",
        "ls",
        "list_dir",
        "list_files",
        "file_search",
        "grep_search",
        "search_code",
        "report_intent",
        "toolsearch",
    }
)
_WRITE_TOOLS = frozenset(
    {
        "edit",
        "write",
        "multiedit",
        "apply_patch",
        "create_file",
        "insert_edit_file",
        "insert_edit_into_file",
        "str_replace_editor",
        "str_replace_based_edit_tool",
        "notebookedit",
        "todowrite",
    }
)
_DELEGATE_TOOLS = frozenset({"task", "agent", "runsubagent", "search_subagent"})
_NETWORK_TOOLS = frozenset({"webfetch", "websearch", "web_fetch", "web_search", "fetch_webpage"})
_SHELL_TOOLS = frozenset({"bash", "powershell", "shell", "terminal", "run_in_terminal"})
_PURE_BASH_BUILTINS = frozenset({"pwd", "echo", "printf", "true", "false", ":"})


def has_explicit_file_target(tool_name: str) -> bool:
    """Only direct native file writes, not patches, batches or overloaded editors."""
    return tool_name.lower() in _WRITE_TOOLS - {
        "multiedit",
        "apply_patch",
        "todowrite",
        "str_replace_editor",
        "str_replace_based_edit_tool",
    }


def invocation_permission_request(workspace: str, tool_name: str, arguments: dict[str, Any]) -> PermissionRequest:
    """Translate actual invocation fields without trusting producer read-only hints."""
    name = tool_name.lower()
    kind = (
        "shell"
        if name in _SHELL_TOOLS
        else "write"
        if name in _WRITE_TOOLS
        else "read"
        if name in _READ_TOOLS
        else "url"
        if name in _NETWORK_TOOLS
        else "custom-tool"
    )
    command = arguments.get("command")
    if kind == "shell" and not isinstance(command, str):
        raise CapabilityDeniedError("Shell invocation has no complete command string")
    paths = [
        value
        for key in ("path", "file_path", "filePath", "file_name", "pattern" if name == "glob" else "path")
        if isinstance(value := arguments.get(key), str) and value
    ]
    if kind in {"read", "write"} and arguments.get("paths") is not None:
        supplied = arguments["paths"]
        if isinstance(supplied, str) and supplied:
            paths.append(supplied)
        elif isinstance(supplied, list) and all(isinstance(value, str) and value for value in supplied):
            paths.extend(supplied)
        else:
            raise CapabilityDeniedError("Native paths must be a nonempty string or a list of nonempty strings")
    return PermissionRequest(
        kind=kind,
        workspace_path=workspace,
        possible_paths=paths,
        full_command_text=command if kind == "shell" and isinstance(command, str) else None,
    )


async def enforce_invocation_capabilities(
    capabilities: frozenset[str],
    *,
    workspace: str,
    tool_name: str,
    arguments: dict[str, Any],
    dialect: str | None = None,
) -> None:
    request = invocation_permission_request(workspace, tool_name, arguments)
    command = arguments.get("command")
    if command is not None and not isinstance(command, str):
        raise CapabilityDeniedError("Shell command must be a complete string")
    await enforce_tool_capabilities(
        capabilities,
        workspace=workspace,
        kind="",
        tool_name=tool_name,
        paths=request.possible_paths or (),
        command=command,
        dialect=dialect,
    )


def _contained(workspace: str, paths: Sequence[str], *, glob_paths: bool = False) -> None:
    try:
        root = Path(workspace).resolve(strict=True)
        resolve = workspace_glob_root if glob_paths else workspace_target
        for value in paths:
            resolve(str(root), value)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CapabilityDeniedError("Workspace containment could not be established") from exc


def _pure_shell_read(facts: SyntaxFacts, *, trusted_builtins: bool) -> bool:
    # Only a fresh trusted shell can establish these names as actual builtins.
    # A producer-controlled PATH/function table is not evidence of read-only work.
    if not trusted_builtins or not facts.complete or len(facts.documents) != 1 or facts.structures:
        return False
    if facts.documents[0].dialect not in {"bash", "sh"} or len(facts.commands) != 1:
        return False
    command = facts.commands[0]
    if command.name not in _PURE_BASH_BUILTINS or command.scope != "immediate":
        return False
    if command.name == "printf" and any(arg == "-v" for arg in command.arguments):
        return False
    return all(arg is not None for arg in command.arguments)


async def enforce_command_capabilities(
    capabilities: frozenset[str],
    *,
    workspace: str,
    command: str | None = None,
    dialect: str | None = None,
    argv: Sequence[str] | None = None,
    trusted_builtins: bool = False,
) -> None:
    """Capability check only. Policy/approval must still run before execution.

    Unknown shell/argv effects require write and external_access capability.
    A restricted caller cannot turn an unproven command into a read by naming it.
    """
    grant = _grant(capabilities)
    grant.require(frozenset({Capability.shell}))
    _contained(workspace, ())
    if (command is None) == (argv is None):
        raise CapabilityDeniedError("Supply exactly one real shell command or argv invocation")
    if command is not None:
        facts = await analyze_syntax_async(command, dialect)
        if _pure_shell_read(facts, trusted_builtins=trusted_builtins):
            grant.require(frozenset({Capability.read}))
            return
    # Even apparently read-only executables may load plugins, run hooks, or be
    # replaced via PATH. No arbitrary argv whitelist can prove their effects.
    grant.require(frozenset({Capability.write, Capability.external_access}))


def _grant(capabilities: frozenset[str]) -> CapabilityGrant:
    try:
        return CapabilityGrant(frozenset(Capability(value) for value in capabilities))
    except ValueError as exc:
        raise CapabilityDeniedError("Unknown execution capability") from exc


async def enforce_tool_capabilities(
    capabilities: frozenset[str] | None,
    *,
    workspace: str,
    kind: str,
    tool_name: str,
    paths: Sequence[str] = (),
    command: str | None = None,
    dialect: str | None = None,
) -> None:
    if capabilities is None:
        return
    grant = _grant(capabilities)
    name = tool_name.lower()
    if kind == "shell" or name in _SHELL_TOOLS or command:
        await enforce_command_capabilities(
            capabilities,
            workspace=workspace,
            command=command or "",
            dialect=dialect,
        )
        return
    if kind in {"mcp", "custom-tool", "unknown"}:
        grant.require(frozenset(Capability))
    elif name in _NETWORK_TOOLS or kind == "url":
        grant.require(frozenset({Capability.external_access}))
    elif name in _DELEGATE_TOOLS:
        grant.require(frozenset({Capability.delegate}))
    elif name in _WRITE_TOOLS or kind == "write":
        grant.require(frozenset({Capability.write}))
    elif name in _READ_TOOLS or (kind == "read" and not name):
        grant.require(frozenset({Capability.read}))
    else:
        # Unclassified SDK/MCP/custom tools can have arbitrary effects.
        grant.require(frozenset(Capability))
    if paths and Capability.external_access not in grant.capabilities:
        _contained(workspace, paths, glob_paths=name == "glob")


def excluded_tools_for_capabilities(capabilities: frozenset[str] | None, existing: Sequence[str]) -> list[str]:
    if capabilities is None:
        return list(existing)
    grant = _grant(capabilities)
    excluded = set(existing)
    groups = (
        (Capability.read, _READ_TOOLS),
        (Capability.write, _WRITE_TOOLS),
        (Capability.delegate, _DELEGATE_TOOLS),
        (Capability.external_access, _NETWORK_TOOLS),
    )
    for capability, tools in groups:
        if capability not in grant.capabilities:
            excluded.update(tools)
    if not {Capability.shell, Capability.write, Capability.external_access} <= grant.capabilities:
        excluded.update(_SHELL_TOOLS)
    # Provider spellings matter for the best-effort prefilter. The invocation
    # guard above remains the authority even if an SDK adds or renames a tool.
    aliases = {
        "bash": "Bash",
        "edit": "Edit",
        "write": "Write",
        "multiedit": "MultiEdit",
        "task": "Task",
        "agent": "Agent",
        "webfetch": "WebFetch",
        "websearch": "WebSearch",
        "notebookedit": "NotebookEdit",
        "todowrite": "TodoWrite",
    }
    excluded.update(aliases[name] for name in tuple(excluded) if name in aliases)
    return sorted(excluded)
