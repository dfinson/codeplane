"""Deterministic per-tool display formatters.

Each formatter extracts a short human-readable label from a tool's
arguments and (optionally) its result, avoiding LLM calls entirely.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def _truncate(s: str, max_len: int = 60) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _parse_args(tool_args: str | None) -> dict[str, Any]:
    if not tool_args:
        return {}
    try:
        parsed = json.loads(tool_args)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _short_path(path: str) -> str:
    """Abbreviate long file paths to last two path components."""
    p = PurePosixPath(path)
    parts = p.parts
    if len(parts) <= 2:
        return str(p)
    return str(PurePosixPath(*parts[-2:]))


# -- Individual formatters ---------------------------------------------------


def _fmt_bash(args: dict[str, Any]) -> str:
    cmd = args.get("command", "")
    return f"$ {_truncate(cmd, 55)}" if cmd else "bash"


def _fmt_run_in_terminal(args: dict[str, Any]) -> str:
    cmd = args.get("command", "")
    return f"$ {_truncate(cmd, 55)}" if cmd else "Run command"


def _fmt_read_file(args: dict[str, Any]) -> str:
    path = args.get("filePath", args.get("file_path", ""))
    if not path:
        return "Read file"
    short = _short_path(path)
    start = args.get("startLine", args.get("start_line"))
    end = args.get("endLine", args.get("end_line"))
    if start and end:
        return f"Read {short}:{start}-{end}"
    return f"Read {short}"


def _fmt_create_file(args: dict[str, Any]) -> str:
    path = args.get("filePath", args.get("file_path", ""))
    return f"Create {_short_path(path)}" if path else "Create file"


def _fmt_replace_string(args: dict[str, Any]) -> str:
    path = args.get("filePath", args.get("file_path", ""))
    return f"Edit {_short_path(path)}" if path else "Edit file"


def _fmt_multi_replace(args: dict[str, Any]) -> str:
    replacements = args.get("replacements", [])
    paths: set[str] = set()
    for r in replacements:
        if isinstance(r, dict):
            p = r.get("filePath", r.get("file_path", ""))
            if p:
                paths.add(_short_path(p))
    if paths:
        listed = ", ".join(sorted(paths)[:3])
        suffix = "…" if len(paths) > 3 else ""
        return f"Edit {listed}{suffix}"
    count = len(replacements) if isinstance(replacements, list) else 0
    return f"Edit {count} locations"


def _fmt_grep_search(args: dict[str, Any]) -> str:
    query = args.get("query", args.get("pattern", ""))
    return f'Grep: "{_truncate(query, 40)}"' if query else "Grep search"


def _fmt_semantic_search(args: dict[str, Any]) -> str:
    query = args.get("query", "")
    return f'Search: "{_truncate(query, 40)}"' if query else "Semantic search"


def _fmt_file_search(args: dict[str, Any]) -> str:
    query = args.get("query", args.get("pattern", ""))
    return f'Find: "{_truncate(query, 40)}"' if query else "File search"


def _fmt_list_dir(args: dict[str, Any]) -> str:
    path = args.get("path", args.get("directory", ""))
    return f"List {_short_path(path)}" if path else "List directory"


def _fmt_memory(args: dict[str, Any]) -> str:
    cmd = args.get("command", "")
    path = args.get("path", "")
    if cmd and path:
        return f"Memory {cmd}: {_short_path(path)}"
    return f"Memory {cmd}" if cmd else "Memory"


def _fmt_manage_todo(args: dict[str, Any]) -> str:
    items = args.get("todoList", [])
    count = len(items) if isinstance(items, list) else 0
    return f"Update todo list ({count} items)" if count else "Update todo list"


def _fmt_get_errors(args: dict[str, Any]) -> str:
    paths = args.get("filePaths", [])
    if not paths:
        return "Check all errors"
    if len(paths) == 1:
        return f"Check errors: {_short_path(paths[0])}"
    return f"Check errors: {len(paths)} files"


def _fmt_run_subagent(args: dict[str, Any]) -> str:
    desc = args.get("description", "")
    return f"Subagent: {_truncate(desc, 50)}" if desc else "Run subagent"


def _fmt_search_subagent(args: dict[str, Any]) -> str:
    desc = args.get("description", args.get("query", ""))
    return f"Search agent: {_truncate(desc, 45)}" if desc else "Search agent"


def _fmt_get_terminal_output(args: dict[str, Any]) -> str:
    tid = args.get("id", "")
    return f"Read terminal {tid}" if tid else "Read terminal"


def _fmt_fetch_webpage(args: dict[str, Any]) -> str:
    url = args.get("url", "")
    if url:
        # Show domain + path start only
        from urllib.parse import urlparse

        try:
            p = urlparse(url)
            short = p.netloc + p.path[:30]
            return f"Fetch {_truncate(short, 50)}"
        except Exception:
            pass
    return "Fetch webpage"


def _fmt_tool_search(args: dict[str, Any]) -> str:
    pat = args.get("pattern", "")
    return f'Find tools: "{_truncate(pat, 40)}"' if pat else "Find tools"


def _fmt_rename_symbol(args: dict[str, Any]) -> str:
    old = args.get("oldName", args.get("old_name", ""))
    new = args.get("newName", args.get("new_name", ""))
    if old and new:
        return f"Rename {_truncate(old, 20)} → {_truncate(new, 20)}"
    return "Rename symbol"


def _fmt_list_code_usages(args: dict[str, Any]) -> str:
    sym = args.get("symbol", args.get("query", ""))
    return f"Usages: {_truncate(sym, 45)}" if sym else "Find usages"


# -- Result hint formatters ---------------------------------------------------
# Each takes the raw result string and returns a terse suffix like "→ 12 matches".


def _count_lines(result: str) -> int:
    """Count non-empty lines in a result string."""
    return sum(1 for line in result.splitlines() if line.strip())


def _hint_bash(result: str, success: bool) -> str:
    if not success:
        first = result.strip().splitlines()[0] if result.strip() else "error"
        return f"→ FAIL: {_truncate(first, 40)}"
    n = _count_lines(result)
    return f"→ {n} lines" if n else "→ done"


def _hint_read_file(result: str, success: bool) -> str:
    n = _count_lines(result)
    return f"→ {n} lines" if n else "→ empty"


def _hint_create_file(result: str, success: bool) -> str:
    return "→ created" if success else "→ FAIL"


def _hint_replace_string(result: str, success: bool) -> str:
    return "→ applied" if success else "→ FAIL: no match"


def _hint_multi_replace(result: str, success: bool) -> str:
    if not success:
        return "→ partial FAIL"
    return "→ applied"


def _hint_grep_search(result: str, success: bool) -> str:
    # grep_search results typically contain match lines
    n = _count_lines(result)
    if n == 0:
        return "→ no matches"
    return f"→ {n} matches"


def _hint_semantic_search(result: str, success: bool) -> str:
    n = _count_lines(result)
    return f"→ {n} results" if n else "→ no results"


def _hint_file_search(result: str, success: bool) -> str:
    n = _count_lines(result)
    return f"→ {n} files" if n else "→ no files"


def _hint_list_dir(result: str, success: bool) -> str:
    n = _count_lines(result)
    return f"→ {n} entries" if n else "→ empty"


def _hint_manage_todo(result: str, success: bool) -> str:
    return "→ updated"


def _hint_get_errors(result: str, success: bool) -> str:
    n = _count_lines(result)
    return "→ clean" if n == 0 else f"→ {n} diagnostics"


def _hint_subagent(result: str, success: bool) -> str:
    if not success:
        return "→ FAIL"
    n = _count_lines(result)
    return f"→ done ({n} lines)" if n > 1 else "→ done"


def _hint_get_terminal_output(result: str, success: bool) -> str:
    n = _count_lines(result)
    return f"→ {n} lines" if n else "→ empty"


def _hint_fetch_webpage(result: str, success: bool) -> str:
    if not success:
        return "→ FAIL"
    n = len(result)
    if n > 1024:
        return f"→ {n // 1024}KB"
    return f"→ {n} bytes"


def _hint_memory(result: str, success: bool) -> str:
    if not success:
        return "→ FAIL"
    n = _count_lines(result)
    return f"→ {n} lines" if n else "→ done"


def _hint_rename_symbol(result: str, success: bool) -> str:
    return "→ renamed" if success else "→ FAIL"


def _hint_list_code_usages(result: str, success: bool) -> str:
    n = _count_lines(result)
    return f"→ {n} usages" if n else "→ none"


# -- Registries ---------------------------------------------------------------

_FORMATTERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "bash": _fmt_bash,
    "run_in_terminal": _fmt_run_in_terminal,
    "read_file": _fmt_read_file,
    "create_file": _fmt_create_file,
    "replace_string_in_file": _fmt_replace_string,
    "multi_replace_string_in_file": _fmt_multi_replace,
    "grep_search": _fmt_grep_search,
    "semantic_search": _fmt_semantic_search,
    "file_search": _fmt_file_search,
    "list_dir": _fmt_list_dir,
    "memory": _fmt_memory,
    "manage_todo_list": _fmt_manage_todo,
    "get_errors": _fmt_get_errors,
    "runSubagent": _fmt_run_subagent,
    "search_subagent": _fmt_search_subagent,
    "get_terminal_output": _fmt_get_terminal_output,
    "fetch_webpage": _fmt_fetch_webpage,
    "tool_search_tool_regex": _fmt_tool_search,
    "vscode_renameSymbol": _fmt_rename_symbol,
    "vscode_listCodeUsages": _fmt_list_code_usages,
}

_RESULT_HINTS: dict[str, Callable[[str, bool], str]] = {
    "bash": _hint_bash,
    "run_in_terminal": _hint_bash,
    "read_file": _hint_read_file,
    "create_file": _hint_create_file,
    "replace_string_in_file": _hint_replace_string,
    "multi_replace_string_in_file": _hint_multi_replace,
    "grep_search": _hint_grep_search,
    "semantic_search": _hint_semantic_search,
    "file_search": _hint_file_search,
    "list_dir": _hint_list_dir,
    "manage_todo_list": _hint_manage_todo,
    "get_errors": _hint_get_errors,
    "runSubagent": _hint_subagent,
    "search_subagent": _hint_subagent,
    "get_terminal_output": _hint_get_terminal_output,
    "fetch_webpage": _hint_fetch_webpage,
    "memory": _hint_memory,
    "vscode_renameSymbol": _hint_rename_symbol,
    "vscode_listCodeUsages": _hint_list_code_usages,
}


def format_tool_display(
    tool_name: str,
    tool_args: str | None,
    tool_result: str | None = None,
    tool_success: bool = True,
) -> str:
    """Return a short, deterministic, human-readable label for a tool call.

    When *tool_result* is provided (i.e. after execution), a result hint
    is appended (e.g. ``Grep: "foo" → 12 matches``).
    Falls back to the raw tool name if no formatter is registered.
    """
    # Strip MCP server prefix for lookup (e.g. "github/search_code" → "search_code")
    lookup_name = tool_name.rsplit("/", 1)[-1] if "/" in tool_name else tool_name
    formatter = _FORMATTERS.get(lookup_name)
    if formatter is None:
        label = tool_name
    else:
        args = _parse_args(tool_args)
        try:
            label = formatter(args)
        except Exception:
            label = tool_name

    # Append result hint when result is available
    if tool_result is not None:
        hint_fn = _RESULT_HINTS.get(lookup_name)
        if hint_fn is not None:
            try:
                label = f"{label} {hint_fn(tool_result, tool_success)}"
            except Exception:
                pass

    return label
