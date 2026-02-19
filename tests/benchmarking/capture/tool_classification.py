"""Tool classification constants and functions for Copilot agent benchmarking.

Provides a shared vocabulary for categorising tool calls captured from
Copilot Chat API traffic.  Used by both ``trace_from_capture.py`` (the
mitmproxy post-processor) and any future extractors.

Public API:
    DEFAULT_CODEPLANE_PREFIX
    classify_tool_kind(tool_name)      -> "mcp" | "native" | "builtin"
    derive_tool_namespace(...)         -> "codeplane" | "github_mcp" | ...
    infer_call_subkind(tool_name)      -> "terminal" | "edit" | ...
    strip_mcp_prefix(tool_name)        -> short tool name
"""

from __future__ import annotations

# Default prefix used to identify CodePlane MCP tool names
DEFAULT_CODEPLANE_PREFIX = "codeplane_"

# ---------------------------------------------------------------------------
# Native tool subkind inference
# ---------------------------------------------------------------------------
_SUBKIND_BY_TOOL_NAME: dict[str, str] = {
    # --- Terminal ---
    "run_in_terminal": "terminal",
    "get_terminal_output": "terminal",
    # --- Agent planning ---
    "manage_todo_list": "planning",
    "ask_questions": "planning",
    "tool_search_tool_regex": "planning",
    # --- CodePlane MCP: read ---
    "read_source": "read_file",
    "read_file_full": "read_file",
    "read_scaffold": "read_file",
    "read_file": "read_file",
    "search": "search",
    "list_files": "read_file",
    "map_repo": "read_file",
    "describe": "read_file",
    # --- CodePlane MCP: edit ---
    "write_source": "edit",
    "write_files": "edit",
    # --- CodePlane MCP: git ---
    "semantic_diff": "git",
    "git_status": "git",
    "git_log": "git",
    "git_diff": "git",
    "git_commit": "git",
    "git_stage": "git",
    "git_stage_and_commit": "git",
    "git_push": "git",
    "git_pull": "git",
    "git_branch": "git",
    "git_checkout": "git",
    "git_stash": "git",
    "git_reset": "git",
    "git_merge": "git",
    "git_rebase": "git",
    "git_remote": "git",
    "git_inspect": "git",
    "git_history": "git",
    "git_submodule": "git",
    "git_worktree": "git",
    # --- CodePlane MCP: lint ---
    "lint_check": "lint",
    "lint_tools": "lint",
    # --- CodePlane MCP: tests ---
    "run_test_targets": "tests",
    "discover_test_targets": "tests",
    "inspect_affected_tests": "tests",
    "get_test_run_status": "tests",
    "cancel_test_run": "tests",
    # --- CodePlane MCP: refactor ---
    "refactor_rename": "edit",
    "refactor_move": "edit",
    "refactor_delete": "edit",
    "refactor_apply": "edit",
    "refactor_cancel": "edit",
    "refactor_inspect": "read_file",
    "refactor_impact": "read_file",
    # --- CodePlane MCP: budget ---
    "reset_budget": "planning",
    # --- GitHub (non-MCP built-in) ---
    "github_api": "git",
    "github_repo": "git",
    # --- VS Code built-in: file editing ---
    "editFiles": "edit",
    "edit_file": "edit",
    "createFile": "edit",
    "create_file": "edit",
    "deleteFile": "edit",
    "delete_file": "edit",
    "renameFile": "edit",
    "rename_file": "edit",
    "insertEdit": "edit",
    "insert_edit": "edit",
    "replaceInFile": "edit",
    "replace_in_file": "edit",
    # --- VS Code built-in: file reading ---
    "readFile": "read_file",
    "listDirectory": "read_file",
    "list_directory": "read_file",
    "openFile": "read_file",
    "open_file": "read_file",
    "getWorkspaceStructure": "read_file",
    "get_workspace_structure": "read_file",
    # --- VS Code built-in: search ---
    "searchFiles": "search",
    "search_files": "search",
    "findTextInFiles": "search",
    "find_text_in_files": "search",
    "findInFiles": "search",
    "find_in_files": "search",
    # --- VS Code built-in: environment ---
    "configure_python_environment": "environment",
    "install_python_packages": "environment",
    "get_python_environment_details": "environment",
    "get_python_executable_details": "environment",
    "configure_python_notebook": "environment",
    "configure_non_python_notebook": "environment",
    "restart_notebook_kernel": "environment",
    # --- VS Code built-in: browser ---
    "fetch_webpage": "browser",
    "open_simple_browser": "browser",
    # --- VS Code built-in: commands ---
    "run_vscode_command": "vscode_cmd",
    "install_extension": "vscode_cmd",
    "vscode_searchExtensions_internal": "vscode_cmd",
    "create_new_workspace": "vscode_cmd",
    "get_project_setup_info": "vscode_cmd",
    "create_and_run_task": "vscode_cmd",
    "get_vscode_api": "vscode_cmd",
    # --- VS Code built-in: tests ---
    "test_failure": "tests",
    # --- GitHub MCP overrides (short names that differ from default "git") ---
    "search_code": "search",
    "search_issues": "search",
    "search_pull_requests": "search",
    "search_repositories": "search",
    "search_users": "search",
    "get_file_contents": "read_file",
    # --- Pylance MCP (short names via strip_mcp_prefix) ---
    "mcp_s_pylanceDocuments": "read_file",
    "mcp_s_pylanceFileSyntaxErrors": "lint",
    "mcp_s_pylanceImports": "read_file",
    "mcp_s_pylanceInstalledTopLevelModules": "read_file",
    "mcp_s_pylanceInvokeRefactoring": "edit",
    "mcp_s_pylancePythonEnvironments": "environment",
    "mcp_s_pylanceRunCodeSnippet": "terminal",
    "mcp_s_pylanceSettings": "read_file",
    "mcp_s_pylanceSyntaxErrors": "lint",
    "mcp_s_pylanceUpdatePythonEnvironment": "environment",
    "mcp_s_pylanceWorkspaceRoots": "read_file",
    "mcp_s_pylanceWorkspaceUserFiles": "read_file",
}


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def strip_mcp_prefix(tool_name: str) -> str:
    """Strip MCP server prefix: ``mcp_<server-label>_<tool>`` -> ``<tool>``.

    The server label sits between the first and second ``_`` after the
    ``mcp_`` literal.  Labels may contain hyphens (``codeplane-cod``) but
    real labels don't contain underscores today, so the first ``_`` inside
    the remainder reliably separates label from tool name.
    """
    if not tool_name.startswith("mcp_"):
        return tool_name
    rest = tool_name[4:]  # drop 'mcp_'
    sep = rest.find("_")
    if sep == -1:
        return tool_name  # no second underscore — not a real MCP name
    return rest[sep + 1 :]


def infer_call_subkind(tool_name: str) -> str:
    """Classify a tool invocation into a broad functional subkind.

    Applies to all tool_kind values (native, mcp, builtin).
    """
    short = strip_mcp_prefix(tool_name)
    if short in _SUBKIND_BY_TOOL_NAME:
        return _SUBKIND_BY_TOOL_NAME[short]
    if tool_name in _SUBKIND_BY_TOOL_NAME:
        return _SUBKIND_BY_TOOL_NAME[tool_name]
    for prefix, kind in [
        ("git_", "git"),
        ("refactor_", "edit"),
        ("mcp_codeplane", "mcp_codeplane"),
        ("mcp_github", "git"),
        ("mcp_pylance", "read_file"),
    ]:
        if tool_name.startswith(prefix) or short.startswith(prefix):
            return kind
    return "unknown"


def classify_tool_kind(tool_name: str) -> str:
    """Classify tool into kind: mcp, native, builtin.

    - mcp: MCP server tools (prefixed with ``mcp_``)
    - native: raw terminal access (``run_in_terminal``, ``get_terminal_output``)
    - builtin: all other VS Code / agent platform tools
    """
    if tool_name.startswith("mcp_"):
        return "mcp"
    if tool_name in ("run_in_terminal", "get_terminal_output"):
        return "native"
    return "builtin"


def derive_tool_namespace(
    tool_name: str,
    tool_kind: str,
    codeplane_prefix: str = DEFAULT_CODEPLANE_PREFIX,
) -> str:
    """Classify a tool call into a namespace bucket.

    Returns one of: "codeplane", "github_mcp", "pylance_mcp",
    "other_mcp", "native", "builtin", "unknown".
    """
    if tool_kind == "mcp":
        if tool_name.startswith(codeplane_prefix) or "codeplane" in tool_name:
            return "codeplane"
        if tool_name.startswith("mcp_github"):
            return "github_mcp"
        if tool_name.startswith("mcp_pylance"):
            return "pylance_mcp"
        return "other_mcp"
    if tool_kind == "native":
        return "native"
    if tool_kind == "builtin":
        return "builtin"
    return "unknown"
