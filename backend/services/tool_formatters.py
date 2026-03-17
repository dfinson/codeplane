"""Deterministic per-tool display formatters.

Each formatter extracts a short human-readable label from a tool's
arguments, avoiding LLM calls entirely.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Callable


def _truncate(s: str, max_len: int = 60) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _parse_args(tool_args: str | None) -> dict:
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


def _fmt_bash(args: dict) -> str:
    cmd = args.get("command", "")
    return f"$ {_truncate(cmd, 55)}" if cmd else "bash"


def _fmt_run_in_terminal(args: dict) -> str:
    cmd = args.get("command", "")
    return f"$ {_truncate(cmd, 55)}" if cmd else "Run command"


def _fmt_read_file(args: dict) -> str:
    path = args.get("filePath", args.get("file_path", ""))
    if not path:
        return "Read file"
    short = _short_path(path)
    start = args.get("startLine", args.get("start_line"))
    end = args.get("endLine", args.get("end_line"))
    if start and end:
        return f"Read {short}:{start}-{end}"
    return f"Read {short}"


def _fmt_create_file(args: dict) -> str:
    path = args.get("filePath", args.get("file_path", ""))
    return f"Create {_short_path(path)}" if path else "Create file"


def _fmt_replace_string(args: dict) -> str:
    path = args.get("filePath", args.get("file_path", ""))
    return f"Edit {_short_path(path)}" if path else "Edit file"


def _fmt_multi_replace(args: dict) -> str:
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


def _fmt_grep_search(args: dict) -> str:
    query = args.get("query", args.get("pattern", ""))
    return f'Grep: "{_truncate(query, 40)}"' if query else "Grep search"


def _fmt_semantic_search(args: dict) -> str:
    query = args.get("query", "")
    return f'Search: "{_truncate(query, 40)}"' if query else "Semantic search"


def _fmt_file_search(args: dict) -> str:
    query = args.get("query", args.get("pattern", ""))
    return f'Find: "{_truncate(query, 40)}"' if query else "File search"


def _fmt_list_dir(args: dict) -> str:
    path = args.get("path", args.get("directory", ""))
    return f"List {_short_path(path)}" if path else "List directory"


def _fmt_memory(args: dict) -> str:
    cmd = args.get("command", "")
    path = args.get("path", "")
    if cmd and path:
        return f"Memory {cmd}: {_short_path(path)}"
    return f"Memory {cmd}" if cmd else "Memory"


def _fmt_manage_todo(args: dict) -> str:
    items = args.get("todoList", [])
    count = len(items) if isinstance(items, list) else 0
    return f"Update todo list ({count} items)" if count else "Update todo list"


# -- Registry ----------------------------------------------------------------

_FORMATTERS: dict[str, Callable[[dict], str]] = {
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
}


def format_tool_display(tool_name: str, tool_args: str | None) -> str:
    """Return a short, deterministic, human-readable label for a tool call.

    Falls back to the raw tool name if no formatter is registered.
    """
    # Strip MCP server prefix for lookup (e.g. "github/search_code" → "search_code")
    lookup_name = tool_name.rsplit("/", 1)[-1] if "/" in tool_name else tool_name
    formatter = _FORMATTERS.get(lookup_name)
    if formatter is None:
        return tool_name
    args = _parse_args(tool_args)
    try:
        return formatter(args)
    except Exception:
        return tool_name
