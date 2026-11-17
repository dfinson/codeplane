"""Tool classification for cost analytics.

Maps tool names (as reported by Copilot/Claude SDKs) to normalized
categories and extracts the primary target (file path, command name)
from tool arguments.
"""

from __future__ import annotations

import re

from backend.services.tools.parsing_utils import ensure_dict

TOOL_CATEGORIES: dict[str, str] = {
    # file_read — reading file contents
    "read_file": "file_read",
    "view": "file_read",
    "cat": "file_read",
    "Read": "file_read",
    "readFile": "file_read",
    "open_file": "file_read",
    "get_file_contents": "file_read",
    "TodoRead": "file_read",
    "NotebookRead": "file_read",
    "view_image": "file_read",
    # file_write — creating or editing files
    "edit_file": "file_write",
    "edit": "file_write",
    "create_file": "file_write",
    "write_file": "file_write",
    "write": "file_write",
    "Write": "file_write",
    "Edit": "file_write",
    "MultiEdit": "file_write",
    "editFile": "file_write",
    "create": "file_write",
    "replace_string_in_file": "file_write",
    "multi_replace_string_in_file": "file_write",
    "str_replace_based_edit_tool": "file_write",
    "str_replace_editor": "file_write",
    "insert_edit_into_file": "file_write",
    "apply_patch": "file_write",
    "create_or_update_file": "file_write",
    "delete_file": "file_write",
    "create_directory": "file_write",
    "TodoWrite": "file_write",
    "NotebookEdit": "file_write",
    # file_search — searching and navigating the codebase
    "grep": "file_search",
    "grep_search": "file_search",
    "Grep": "file_search",
    "glob": "file_search",
    "Glob": "file_search",
    "find": "file_search",
    "rg": "file_search",
    "ripgrep": "file_search",
    "search": "file_search",
    "semantic_search": "file_search",
    "codeSearch": "file_search",
    "listDir": "file_search",
    "list_dir": "file_search",
    "LS": "file_search",
    "file_search": "file_search",
    "vscode_listCodeUsages": "file_search",
    "tool_search_tool_regex": "file_search",
    "ToolSearch": "file_search",
    "ListMcpResources": "file_search",
    "ListMcpResourceTemplates": "file_search",
    # shell — running commands in a terminal
    "bash": "shell",
    "Bash": "shell",
    "terminal": "shell",
    "exec": "shell",
    "runCommand": "shell",
    "run_in_terminal": "shell",
    "get_terminal_output": "shell",
    "read_bash": "shell",
    "write_bash": "shell",
    "stop_bash": "shell",
    "sql": "bookkeeping",
    # git — version control operations (split read vs write)
    "git_diff": "git_read",
    "git_status": "git_read",
    "git_log": "git_read",
    "get_changed_files": "git_read",
    "git_commit": "git_write",
    "git_push": "git_write",
    "git_add": "git_write",
    "git_checkout": "git_write",
    "git_merge": "git_write",
    "git_rebase": "git_write",
    "git_reset": "git_write",
    "git_stash": "git_write",
    # browser — web fetches and browsing
    "fetch_url": "browser",
    "web_search": "browser",
    "web_fetch": "browser",
    "WebFetch": "browser",
    "WebSearch": "browser",
    "fetch_webpage": "browser",
    "ReadMcpResource": "browser",
    # agent — delegation to sub-agents
    "task": "agent",
    "subagent": "agent",
    "Agent": "agent",
    "runSubagent": "agent",
    "search_subagent": "agent",
    "skill": "agent",
    "Task": "agent",
    "read_agent": "agent",
    "list_agents": "agent",
    # thinking — agent reasoning / planning
    "Think": "thinking",
    "Computer": "thinking",
    # bookkeeping — agent-internal housekeeping
    "report_intent": "bookkeeping",
    "store_memory": "bookkeeping",
    "manage_todo_list": "bookkeeping",
    "memory": "bookkeeping",
    # execution_subagent — delegation to execution-focused sub-agents
    "execution_subagent": "agent",
}


_CATEGORY_TO_ACTIVITY: dict[str, str] = {
    "file_write": "implementation",
    "git_write": "git_ops",
    "git_read": "investigation",
    "file_read": "investigation",
    "file_search": "investigation",
    "browser": "investigation",
    "shell": "investigation",
    "agent": "investigation",
    "thinking": "reasoning",
    "bookkeeping": "overhead",
    "other": "overhead",
}

# ---------------------------------------------------------------------------
# Shell command → activity refinement
#
# When we know the actual command a shell tool executed, we can assign a
# more precise activity than the generic "investigation" default.
#
# Architecture: commands are split on shell separators (&&, ||, |, ;),
# leading environment variables are stripped, and each segment is
# classified independently.  Regexes anchor to ^ so tool names appearing
# as arguments (pip install pytest) or in strings don't false-positive.
# The highest-priority match across all segments wins.
# ---------------------------------------------------------------------------

# -- Helpers ----------------------------------------------------------------


def _split_shell(cmd: str) -> list[str]:
    """Split a compound command on shell separators, respecting quotes.

    Splits on &&, ||, |, ; but only when they appear outside single/double
    quotes.  Handles escaped quotes within strings.
    """
    segments: list[str] = []
    current: list[str] = []
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        # Handle escaped characters
        if c == "\\" and i + 1 < n:
            current.append(cmd[i : i + 2])
            i += 2
            continue
        # Handle quoted strings
        if c in ('"', "'"):
            quote = c
            current.append(c)
            i += 1
            while i < n and cmd[i] != quote:
                if cmd[i] == "\\" and i + 1 < n:
                    current.append(cmd[i : i + 2])
                    i += 2
                else:
                    current.append(cmd[i])
                    i += 1
            if i < n:
                current.append(cmd[i])  # closing quote
                i += 1
            continue
        # Check for separators (order matters: && and || before | and ;)
        if cmd[i : i + 2] in ("&&", "||"):
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
            i += 2
            continue
        if c in ("|", ";"):
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    seg = "".join(current).strip()
    if seg:
        segments.append(seg)
    return segments


def _strip_env_vars(seg: str) -> str:
    """Remove leading command wrappers and VAR=value tokens from a segment.

    Loops to handle stacked wrappers: sudo env FOO=1 cmd → cmd.
    Strips: sudo, env, time, nice, nohup, and KEY=val prefixes.
    """
    while True:
        prev = seg
        seg = re.sub(r"^(sudo|env|time|nice|nohup)\s+", "", seg).strip()
        seg = re.sub(r"^(\w+=\S+\s+)*", "", seg).strip()
        if seg == prev:
            break
    return seg


# -- Verification (tests, linters, type-checkers, build validation) ---------

# Group A: Known test runner names at command position
_RUNNER_PREFIXES = r"(?:uv\s+run\s+|npx\s+|bunx\s+|bundle\s+exec\s+|python\s+-m\s+)?"
_RE_TEST_RUNNER = re.compile(
    r"^" + _RUNNER_PREFIXES + r"(pytest|jest|vitest|mocha|rspec|phpunit|unittest|playwright|ctest|bats|pest|tap)\b",
    re.IGNORECASE,
)

# Group B: Any tool with "test" as subcommand
_RE_GENERIC_TEST = re.compile(
    r"^(?:" + _RUNNER_PREFIXES + r")?"
    r"(cargo|go|swift|dart|flutter|dotnet|mvn|gradle|"
    r"\.?/?gradlew|sbt|mix|zig|rake|rails|composer|npm|npm\s+run)\s+tests?\b",
    re.IGNORECASE,
)

# Group C: Make targets that are verification
_RE_MAKE_VERIFY = re.compile(
    r"^make\s+(tests?|lint|check|verify|e2e|integration|unit)\b",
    re.IGNORECASE,
)

# Group D: Linters and type-checkers (only when NOT in fix/write mode)
_RE_LINT_CHECK = re.compile(
    r"^" + _RUNNER_PREFIXES + r"("
    # Python
    r"mypy|pyright|pytype|pyre(?:\s+check)?|flake8|pylint|"
    r"ruff\s+check|ruff\s+format\s+--check|black\s+--check|"
    # JS/TS
    r"eslint|biome\s+(?:check|lint)|oxlint|deno\s+lint|"
    r"tsc\s+--noEmit|flow\s+check|stylelint|prettier\s+--check|"
    # Ruby
    r"rubocop|"
    # Go
    r"golangci-lint\s+run|"
    # Rust
    r"cargo\s+clippy|"
    # Swift
    r"swiftlint|"
    # Shell/DevOps
    r"shellcheck|hadolint|actionlint"
    r")\b"
    r"(?!.*\s--fix\b)(?!.*\s--write\b)",
    re.IGNORECASE,
)

# Group E: Build commands (compilation validates correctness)
_RE_BUILD = re.compile(
    r"^(?:" + _RUNNER_PREFIXES + r")?"
    r"(npm\s+run\s+build|cargo\s+build|go\s+build|gradle\s+build|"
    r"\.?/?gradlew\s+build|mvn\s+(?:compile|package)|dotnet\s+build|"
    r"make\s+(?:build|all|dist)|cmake\s+--build)\b",
    re.IGNORECASE,
)


def _is_verification_segment(seg: str) -> bool:
    """Check if a single (stripped) command segment is verification."""
    return bool(
        _RE_TEST_RUNNER.search(seg)
        or _RE_GENERIC_TEST.search(seg)
        or _RE_MAKE_VERIFY.search(seg)
        or _RE_LINT_CHECK.search(seg)
        or _RE_BUILD.search(seg)
    )


# -- Git (split write vs read; checkout/switch/stash removed from write) ----

# Allow common flags between 'git' and subcommand:
# --no-pager, -C <path>, -c <key=val>, --git-dir=<x>, --work-tree=<x>
_GIT_FLAGS = r"(?:(?:--[\w-]+(?:=\S+)?|-\w(?:\s+\S+)?)\s+)*"

_RE_SHELL_GIT_WRITE = re.compile(
    r"^git\s+" + _GIT_FLAGS + r"(add|commit|push|merge|rebase|cherry-pick|tag|reset)\b",
    re.IGNORECASE,
)
_RE_SHELL_GIT_READ = re.compile(
    r"^git\s+" + _GIT_FLAGS + r"(diff|log|status|show|blame|branch|checkout|switch|stash)\b",
    re.IGNORECASE,
)

# -- Setup (install/deploy — docker scoped to setup subcommands) ------------

_RE_SHELL_SETUP = re.compile(
    r"^(uv\s+sync|uv\s+add|pip\s+install|npm\s+install|npm\s+ci|"
    r"yarn\s+install|brew\s+install|apt\s+install|apt-get\s+install|"
    r"docker\s+(?:build|pull|push|compose\s+(?:up|build|pull))|deploy)\b",
    re.IGNORECASE,
)

# -- Investigation (read-only exploration commands) -------------------------

_RE_SHELL_INVESTIGATE = re.compile(
    r"^(find|ls|cat|head|tail|wc|tree|du|file|grep|awk|diff|less|more|stat|strings|curl|wget)\b",
    re.IGNORECASE,
)

# -- Implementation (commands that modify files) ----------------------------

_RE_SHELL_IMPLEMENT = re.compile(
    r"^(sed|rm|mv|cp|chmod|chown|mkdir|patch)\b",
    re.IGNORECASE,
)


# -- Priority ladder applied per-segment -----------------------------------

# Activity priority (higher index = higher priority)
_ACTIVITY_PRIORITY = {
    "shell_other": 0,
    "investigation": 1,
    "setup": 2,
    "git_ops": 3,
    "verification": 4,
    "implementation": 5,
}


def _classify_segment(seg: str) -> str:
    """Classify a single shell command segment into an activity."""
    if _is_verification_segment(seg):
        return "verification"
    if _RE_SHELL_GIT_WRITE.search(seg):
        return "git_ops"
    if _RE_SHELL_SETUP.search(seg):
        return "setup"
    if _RE_SHELL_GIT_READ.search(seg):
        return "investigation"
    if _RE_SHELL_IMPLEMENT.search(seg):
        return "implementation"
    if _RE_SHELL_INVESTIGATE.search(seg):
        return "investigation"
    return "shell_other"


def classify_shell_command(cmd: str) -> str:
    """Classify a shell command string into an activity.

    Splits compound commands (&&, ||, |, ;), strips leading env vars,
    classifies each segment, and returns the highest-priority activity.

    Returns one of: verification, git_ops, setup, implementation,
    investigation, shell_other.
    """
    best = "shell_other"
    best_pri = -1
    for seg in _split_shell(cmd):
        seg = _strip_env_vars(seg)
        if not seg:
            continue
        activity = _classify_segment(seg)
        pri = _ACTIVITY_PRIORITY.get(activity, 0)
        if pri > best_pri:
            best = activity
            best_pri = pri
    return best


# ---------------------------------------------------------------------------
# Action classification — the new Action × Purpose system (Item 19)
# ---------------------------------------------------------------------------

_CATEGORY_TO_ACTION: dict[str, str] = {
    "file_write": "write",
    "git_write": "vcs",
    "git_read": "read",
    "file_read": "read",
    "file_search": "read",
    "browser": "read",
    "shell": "execute",
    "agent": "delegate",
    "thinking": "think",
    "bookkeeping": "think",
    "other": "think",
}


def shell_action(cmd: str) -> str:
    """Map a shell command to an action bucket (test/vcs/read/execute).

    Priority: test > vcs (write) > read > execute.
    Uses the same segment-splitting as classify_shell_command.
    """
    activity = classify_shell_command(cmd)
    # Map from activity names to action buckets
    _map = {
        "verification": "test",
        "git_ops": "vcs",
        "implementation": "execute",
        "setup": "execute",
        "investigation": "read",
        "shell_other": "execute",
    }
    return _map.get(activity, "execute")


def classify_action_from_tools(
    tool_categories: list[str],
    shell_commands: list[str] | None = None,
) -> str:
    """Deterministic action classifier from tool categories.

    Priority ladder: write > test > vcs > execute > delegate > read > think.
    Git read operations (diff, log, status) map to 'read', not 'vcs'.
    """
    has_write = "file_write" in tool_categories
    has_delegate = "agent" in tool_categories

    # Check shell commands for test/vcs
    shell_actions: set[str] = set()
    if shell_commands:
        for cmd in shell_commands:
            shell_actions.add(shell_action(cmd))

    if has_write:
        return "write"
    if "test" in shell_actions:
        return "test"
    if "git_write" in tool_categories or "vcs" in shell_actions:
        return "vcs"
    if "shell" in tool_categories:
        return "execute"
    if has_delegate:
        return "delegate"
    if any(c in tool_categories for c in ("file_read", "file_search", "browser", "git_read")):
        return "read"
    if "read" in shell_actions:
        return "read"
    return "think"


def classify_tool(tool_name: str) -> str:
    """Return the normalized category for a tool name.

    For MCP-style names like ``server/tool``, tries the full name first,
    then falls back to just the tool part after the slash.

    For underscore-prefixed MCP names like ``mcp_server_tool``, strips the
    ``mcp_<server>_`` prefix and looks up the remainder.
    """
    cat = TOOL_CATEGORIES.get(tool_name)
    if cat:
        return cat
    if "/" in tool_name:
        return TOOL_CATEGORIES.get(tool_name.rsplit("/", 1)[-1], "other")
    if tool_name.startswith("mcp_"):
        parts = tool_name.split("_", 2)
        if len(parts) >= 3:
            tool_part = parts[2]
            cat = TOOL_CATEGORIES.get(tool_part)
            if cat:
                return cat
    return "other"


def refine_shell_category(tool_args_json: str | None) -> str | None:
    """Inspect shell tool arguments and return a refined category if applicable.

    When a shell tool (bash, run_in_terminal, etc.) executes a git command,
    returns ``"git_read"`` or ``"git_write"`` instead of the generic ``"shell"``.
    Returns ``None`` when the command is not a git command and should stay as ``"shell"``.
    """
    if not tool_args_json:
        return None
    parsed = ensure_dict(tool_args_json)
    if not parsed:
        return None
    cmd = str(parsed.get("command", "") or parsed.get("cmd", "") or parsed.get("input", ""))
    if not cmd:
        return None
    for seg in _split_shell(cmd):
        seg = _strip_env_vars(seg)
        if not seg:
            continue
        if _RE_SHELL_GIT_WRITE.search(seg):
            return "git_write"
        if _RE_SHELL_GIT_READ.search(seg):
            return "git_read"
    return None


def classify_tool_activity(tool_name: str, tool_args_json: str | None = None) -> str:
    """Return the high-level activity bucket for a tool invocation.

    For shell tools, inspects the actual command from tool_args_json to
    assign a precise activity (verification, git_ops, setup, etc.)
    instead of the generic 'investigation' fallback.

    Agent/delegation tools return the sentinel ``"_delegation"`` so the
    cost attribution pipeline can resolve them against the sub-agent's
    actual activity breakdown rather than blindly calling them investigation.
    """
    category = classify_tool(tool_name)
    if category == "agent":
        return "_delegation"
    if category == "shell" and tool_args_json:
        parsed = ensure_dict(tool_args_json)
        if parsed:
            cmd = str(parsed.get("command", "") or parsed.get("cmd", "") or parsed.get("input", ""))
            if cmd:
                shell_activity = classify_shell_command(cmd)
                if shell_activity != "shell_other":
                    return shell_activity
    return _CATEGORY_TO_ACTIVITY.get(category, "overhead")


def extract_tool_target(tool_name: str, tool_args: str | None) -> str:
    """Extract the primary target from tool arguments.

    Returns a short identifier suitable for grouping — e.g. a file path
    for file operations, or the command prefix for shell commands.
    """
    if not tool_args:
        return ""

    parsed = ensure_dict(tool_args)
    if parsed is None:
        return ""

    category = classify_tool(tool_name)

    if category in ("file_read", "file_write"):
        return str(
            parsed.get("path", "")
            or parsed.get("file", "")
            or parsed.get("file_path", "")
            or parsed.get("filePath", "")
        )

    if category == "file_search":
        return str(parsed.get("pattern", "") or parsed.get("query", ""))

    if category == "shell":
        cmd = str(parsed.get("command", "") or parsed.get("cmd", ""))
        # Return first word of command as the target
        return cmd.split()[0] if cmd else ""

    if category in ("git_read", "git_write"):
        return str(parsed.get("path", "") or parsed.get("file", ""))

    if category == "browser":
        return str(parsed.get("url", ""))

    return ""


def extract_file_paths(tool_name: str, tool_args: str | None) -> list[str]:
    """Extract all file paths referenced in tool arguments."""
    if not tool_args:
        return []

    parsed = ensure_dict(tool_args)
    if parsed is None:
        return []

    paths: list[str] = []
    for key in ("path", "file", "file_path", "filePath", "filename"):
        val = parsed.get(key)
        if val and isinstance(val, str):
            paths.append(val)

    # Some tools have a list of files
    for key in ("files", "paths"):
        val = parsed.get(key)
        if isinstance(val, list):
            paths.extend(str(v) for v in val if v)

    return paths
