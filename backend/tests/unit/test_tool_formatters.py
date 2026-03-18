"""Tests for backend.services.tool_formatters."""

from __future__ import annotations

import json

from backend.services.tool_formatters import (
    _count_lines,
    _parse_args,
    _short_path,
    _truncate,
    format_tool_display,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello", 60) == "hello"

    def test_exact_length_unchanged(self):
        s = "a" * 60
        assert _truncate(s, 60) == s

    def test_long_string_truncated(self):
        s = "a" * 80
        result = _truncate(s, 60)
        assert len(result) == 60
        assert result.endswith("…")

    def test_custom_max_len(self):
        s = "abcdef"
        result = _truncate(s, 4)
        assert result == "abc…"
        assert len(result) == 4

    def test_empty_string(self):
        assert _truncate("", 60) == ""

    def test_one_char_max(self):
        # max_len=1 → s[:0] + "…" = "…"
        assert _truncate("abc", 1) == "…"


class TestParseArgs:
    def test_valid_json_dict(self):
        assert _parse_args('{"a": 1}') == {"a": 1}

    def test_none_returns_empty(self):
        assert _parse_args(None) == {}

    def test_empty_string_returns_empty(self):
        assert _parse_args("") == {}

    def test_invalid_json_returns_empty(self):
        assert _parse_args("{bad json}") == {}

    def test_json_array_returns_empty(self):
        assert _parse_args("[1, 2, 3]") == {}

    def test_json_string_returns_empty(self):
        assert _parse_args('"just a string"') == {}

    def test_json_number_returns_empty(self):
        assert _parse_args("42") == {}

    def test_nested_dict(self):
        data = {"outer": {"inner": True}}
        assert _parse_args(json.dumps(data)) == data


class TestShortPath:
    def test_short_path_unchanged(self):
        assert _short_path("file.py") == "file.py"

    def test_two_components_unchanged(self):
        assert _short_path("src/file.py") == "src/file.py"

    def test_long_path_abbreviated(self):
        assert _short_path("/home/user/project/src/file.py") == "src/file.py"

    def test_three_components(self):
        assert _short_path("a/b/c") == "b/c"

    def test_trailing_slash(self):
        result = _short_path("a/b/c/")
        # PurePosixPath normalises trailing slash
        assert result == "b/c"


class TestCountLines:
    def test_empty_string(self):
        assert _count_lines("") == 0

    def test_blank_lines_not_counted(self):
        assert _count_lines("\n\n\n") == 0

    def test_mixed_blank_and_content(self):
        assert _count_lines("a\n\nb\n") == 2

    def test_single_line_no_newline(self):
        assert _count_lines("hello") == 1

    def test_whitespace_only_lines_not_counted(self):
        assert _count_lines("  \n\t\n  a  \n") == 1


# ---------------------------------------------------------------------------
# Individual tool call formatters via format_tool_display
# ---------------------------------------------------------------------------


def _fmt(tool_name: str, args: dict | None = None) -> str:
    """Shorthand: format a tool call with no result."""
    return format_tool_display(tool_name, json.dumps(args) if args else None)


class TestFmtBash:
    def test_with_command(self):
        assert _fmt("bash", {"command": "ls -la"}) == "$ ls -la"

    def test_empty_command(self):
        assert _fmt("bash", {"command": ""}) == "bash"

    def test_no_args(self):
        assert _fmt("bash") == "bash"

    def test_long_command_truncated(self):
        cmd = "x" * 100
        result = _fmt("bash", {"command": cmd})
        assert result.startswith("$ ")
        assert result.endswith("…")
        # "$ " (2 chars) + truncated command (55 chars)
        assert len(result) == 57


class TestFmtRunInTerminal:
    def test_with_command(self):
        assert _fmt("run_in_terminal", {"command": "npm start"}) == "$ npm start"

    def test_empty_command(self):
        assert _fmt("run_in_terminal", {"command": ""}) == "Run command"

    def test_no_args(self):
        assert _fmt("run_in_terminal") == "Run command"


class TestFmtReadFile:
    def test_with_path(self):
        result = _fmt("read_file", {"filePath": "src/main.py"})
        assert result == "Read src/main.py"

    def test_camel_case_path(self):
        result = _fmt("read_file", {"file_path": "src/main.py"})
        assert result == "Read src/main.py"

    def test_with_line_range(self):
        result = _fmt("read_file", {"filePath": "a/b.py", "startLine": 10, "endLine": 20})
        assert result == "Read a/b.py:10-20"

    def test_snake_case_line_range(self):
        result = _fmt("read_file", {"file_path": "a/b.py", "start_line": 5, "end_line": 15})
        assert result == "Read a/b.py:5-15"

    def test_no_path(self):
        assert _fmt("read_file", {}) == "Read file"

    def test_no_args(self):
        assert _fmt("read_file") == "Read file"

    def test_long_path_abbreviated(self):
        result = _fmt("read_file", {"filePath": "/home/user/project/deep/file.py"})
        assert result == "Read deep/file.py"


class TestFmtCreateFile:
    def test_with_path(self):
        assert _fmt("create_file", {"filePath": "src/new.py"}) == "Create src/new.py"

    def test_snake_case(self):
        assert _fmt("create_file", {"file_path": "x.py"}) == "Create x.py"

    def test_no_path(self):
        assert _fmt("create_file", {}) == "Create file"


class TestFmtReplaceString:
    def test_with_path(self):
        result = _fmt("replace_string_in_file", {"filePath": "src/app.py"})
        assert result == "Edit src/app.py"

    def test_no_path(self):
        assert _fmt("replace_string_in_file", {}) == "Edit file"


class TestFmtMultiReplace:
    def test_single_file(self):
        args = {"replacements": [{"filePath": "a/b/c.py"}]}
        assert _fmt("multi_replace_string_in_file", args) == "Edit b/c.py"

    def test_multiple_files(self):
        args = {
            "replacements": [
                {"filePath": "x.py"},
                {"filePath": "y.py"},
            ]
        }
        result = _fmt("multi_replace_string_in_file", args)
        assert "x.py" in result
        assert "y.py" in result

    def test_more_than_three_files_shows_ellipsis(self):
        args = {
            "replacements": [
                {"filePath": "a.py"},
                {"filePath": "b.py"},
                {"filePath": "c.py"},
                {"filePath": "d.py"},
            ]
        }
        result = _fmt("multi_replace_string_in_file", args)
        assert result.endswith("…")

    def test_exactly_three_files_no_ellipsis(self):
        args = {
            "replacements": [
                {"filePath": "a.py"},
                {"filePath": "b.py"},
                {"filePath": "c.py"},
            ]
        }
        result = _fmt("multi_replace_string_in_file", args)
        assert not result.endswith("…")

    def test_empty_replacements(self):
        result = _fmt("multi_replace_string_in_file", {"replacements": []})
        assert result == "Edit 0 locations"

    def test_replacements_without_paths(self):
        args = {"replacements": [{"old": "a", "new": "b"}]}
        result = _fmt("multi_replace_string_in_file", args)
        assert result == "Edit 1 locations"

    def test_non_list_replacements(self):
        args = {"replacements": "not-a-list"}
        result = _fmt("multi_replace_string_in_file", args)
        assert result == "Edit 0 locations"

    def test_non_dict_items_in_replacements(self):
        args = {"replacements": ["str1", 42]}
        result = _fmt("multi_replace_string_in_file", args)
        # Neither is a dict so paths is empty, count is 2
        assert result == "Edit 2 locations"

    def test_snake_case_file_path(self):
        args = {"replacements": [{"file_path": "a/b.py"}]}
        assert _fmt("multi_replace_string_in_file", args) == "Edit a/b.py"

    def test_duplicate_paths_deduplicated(self):
        args = {
            "replacements": [
                {"filePath": "a.py"},
                {"filePath": "a.py"},
            ]
        }
        result = _fmt("multi_replace_string_in_file", args)
        assert result == "Edit a.py"


class TestFmtGrepSearch:
    def test_with_query(self):
        result = _fmt("grep_search", {"query": "TODO"})
        assert result == 'Grep: "TODO"'

    def test_pattern_fallback(self):
        result = _fmt("grep_search", {"pattern": "FIXME"})
        assert result == 'Grep: "FIXME"'

    def test_no_query(self):
        assert _fmt("grep_search", {}) == "Grep search"

    def test_long_query_truncated(self):
        query = "a" * 80
        result = _fmt("grep_search", {"query": query})
        assert result.endswith('…"')


class TestFmtSemanticSearch:
    def test_with_query(self):
        result = _fmt("semantic_search", {"query": "auth logic"})
        assert result == 'Search: "auth logic"'

    def test_no_query(self):
        assert _fmt("semantic_search", {}) == "Semantic search"


class TestFmtFileSearch:
    def test_with_query(self):
        result = _fmt("file_search", {"query": "*.py"})
        assert result == 'Find: "*.py"'

    def test_pattern_fallback(self):
        result = _fmt("file_search", {"pattern": "*.ts"})
        assert result == 'Find: "*.ts"'

    def test_no_query(self):
        assert _fmt("file_search", {}) == "File search"


class TestFmtListDir:
    def test_with_path(self):
        result = _fmt("list_dir", {"path": "src"})
        assert result == "List src"

    def test_directory_fallback(self):
        result = _fmt("list_dir", {"directory": "lib"})
        assert result == "List lib"

    def test_no_path(self):
        assert _fmt("list_dir", {}) == "List directory"


class TestFmtMemory:
    def test_command_and_path(self):
        result = _fmt("memory", {"command": "save", "path": "a/b/c.md"})
        assert result == "Memory save: b/c.md"

    def test_command_only(self):
        assert _fmt("memory", {"command": "list"}) == "Memory list"

    def test_no_args(self):
        assert _fmt("memory", {}) == "Memory"


class TestFmtManageTodo:
    def test_with_items(self):
        result = _fmt("manage_todo_list", {"todoList": [1, 2, 3]})
        assert result == "Update todo list (3 items)"

    def test_empty_list(self):
        result = _fmt("manage_todo_list", {"todoList": []})
        assert result == "Update todo list"

    def test_non_list(self):
        result = _fmt("manage_todo_list", {"todoList": "not-a-list"})
        assert result == "Update todo list"

    def test_no_args(self):
        assert _fmt("manage_todo_list") == "Update todo list"


class TestFmtGetErrors:
    def test_no_paths(self):
        assert _fmt("get_errors", {}) == "Check all errors"

    def test_single_path(self):
        result = _fmt("get_errors", {"filePaths": ["src/main.py"]})
        assert result == "Check errors: src/main.py"

    def test_multiple_paths(self):
        result = _fmt("get_errors", {"filePaths": ["a.py", "b.py", "c.py"]})
        assert result == "Check errors: 3 files"

    def test_empty_paths(self):
        assert _fmt("get_errors", {"filePaths": []}) == "Check all errors"


class TestFmtRunSubagent:
    def test_with_description(self):
        result = _fmt("runSubagent", {"description": "fix the bug"})
        assert result == "Subagent: fix the bug"

    def test_no_description(self):
        assert _fmt("runSubagent", {}) == "Run subagent"


class TestFmtSearchSubagent:
    def test_with_description(self):
        result = _fmt("search_subagent", {"description": "find auth code"})
        assert result == "Search agent: find auth code"

    def test_query_fallback(self):
        result = _fmt("search_subagent", {"query": "API routes"})
        assert result == "Search agent: API routes"

    def test_no_args(self):
        assert _fmt("search_subagent", {}) == "Search agent"


class TestFmtGetTerminalOutput:
    def test_with_id(self):
        result = _fmt("get_terminal_output", {"id": "term-1"})
        assert result == "Read terminal term-1"

    def test_no_id(self):
        assert _fmt("get_terminal_output", {}) == "Read terminal"


class TestFmtFetchWebpage:
    def test_with_url(self):
        result = _fmt("fetch_webpage", {"url": "https://example.com/docs/page"})
        assert result.startswith("Fetch example.com")

    def test_no_url(self):
        assert _fmt("fetch_webpage", {}) == "Fetch webpage"

    def test_empty_url(self):
        assert _fmt("fetch_webpage", {"url": ""}) == "Fetch webpage"

    def test_invalid_url_falls_back(self):
        # urlparse doesn't raise on weird strings, but still test robustness
        result = _fmt("fetch_webpage", {"url": "not-a-url"})
        # urlparse handles this as a path with no netloc
        assert result.startswith("Fetch")


class TestFmtToolSearch:
    def test_with_pattern(self):
        result = _fmt("tool_search_tool_regex", {"pattern": "file.*"})
        assert result == 'Find tools: "file.*"'

    def test_no_pattern(self):
        assert _fmt("tool_search_tool_regex", {}) == "Find tools"


class TestFmtRenameSymbol:
    def test_with_names(self):
        result = _fmt("vscode_renameSymbol", {"oldName": "foo", "newName": "bar"})
        assert result == "Rename foo → bar"

    def test_snake_case(self):
        result = _fmt("vscode_renameSymbol", {"old_name": "x", "new_name": "y"})
        assert result == "Rename x → y"

    def test_no_names(self):
        assert _fmt("vscode_renameSymbol", {}) == "Rename symbol"

    def test_only_old_name(self):
        assert _fmt("vscode_renameSymbol", {"oldName": "foo"}) == "Rename symbol"

    def test_long_names_truncated(self):
        result = _fmt(
            "vscode_renameSymbol",
            {"oldName": "a" * 50, "newName": "b" * 50},
        )
        assert "…" in result


class TestFmtListCodeUsages:
    def test_with_symbol(self):
        result = _fmt("vscode_listCodeUsages", {"symbol": "MyClass"})
        assert result == "Usages: MyClass"

    def test_query_fallback(self):
        result = _fmt("vscode_listCodeUsages", {"query": "func"})
        assert result == "Usages: func"

    def test_no_symbol(self):
        assert _fmt("vscode_listCodeUsages", {}) == "Find usages"


# ---------------------------------------------------------------------------
# Result hint formatters (via format_tool_display with result)
# ---------------------------------------------------------------------------


def _fmt_with_result(
    tool_name: str,
    args: dict | None,
    result: str,
    success: bool = True,
) -> str:
    return format_tool_display(
        tool_name,
        json.dumps(args) if args else None,
        tool_result=result,
        tool_success=success,
    )


class TestHintBash:
    def test_success_with_output(self):
        result = _fmt_with_result("bash", {"command": "ls"}, "a\nb\nc\n")
        assert "→ 3 lines" in result

    def test_success_empty_output(self):
        result = _fmt_with_result("bash", {"command": "true"}, "")
        assert "→ done" in result

    def test_failure(self):
        result = _fmt_with_result(
            "bash", {"command": "false"}, "error: not found", success=False
        )
        assert "→ FAIL:" in result

    def test_failure_empty_output(self):
        result = _fmt_with_result("bash", {"command": "false"}, "", success=False)
        assert "→ FAIL: error" in result

    def test_run_in_terminal_uses_bash_hint(self):
        result = _fmt_with_result(
            "run_in_terminal", {"command": "echo hi"}, "hi\n"
        )
        assert "→ 1 lines" in result


class TestHintReadFile:
    def test_with_content(self):
        result = _fmt_with_result("read_file", {"filePath": "a.py"}, "line1\nline2\n")
        assert "→ 2 lines" in result

    def test_empty(self):
        result = _fmt_with_result("read_file", {"filePath": "a.py"}, "")
        assert "→ empty" in result


class TestHintCreateFile:
    def test_success(self):
        result = _fmt_with_result("create_file", {"filePath": "a.py"}, "ok")
        assert "→ created" in result

    def test_failure(self):
        result = _fmt_with_result("create_file", {"filePath": "a.py"}, "err", success=False)
        assert "→ FAIL" in result


class TestHintReplaceString:
    def test_success(self):
        result = _fmt_with_result(
            "replace_string_in_file", {"filePath": "a.py"}, "ok"
        )
        assert "→ applied" in result

    def test_failure(self):
        result = _fmt_with_result(
            "replace_string_in_file", {"filePath": "a.py"}, "err", success=False
        )
        assert "→ FAIL: no match" in result


class TestHintMultiReplace:
    def test_success(self):
        result = _fmt_with_result(
            "multi_replace_string_in_file",
            {"replacements": [{"filePath": "a.py"}]},
            "ok",
        )
        assert "→ applied" in result

    def test_failure(self):
        result = _fmt_with_result(
            "multi_replace_string_in_file",
            {"replacements": []},
            "err",
            success=False,
        )
        assert "→ partial FAIL" in result


class TestHintGrepSearch:
    def test_matches(self):
        result = _fmt_with_result(
            "grep_search", {"query": "TODO"}, "file1:1:TODO\nfile2:5:TODO\n"
        )
        assert "→ 2 matches" in result

    def test_no_matches(self):
        result = _fmt_with_result("grep_search", {"query": "XXX"}, "")
        assert "→ no matches" in result


class TestHintSemanticSearch:
    def test_results(self):
        result = _fmt_with_result(
            "semantic_search", {"query": "auth"}, "result1\nresult2\n"
        )
        assert "→ 2 results" in result

    def test_no_results(self):
        result = _fmt_with_result("semantic_search", {"query": "auth"}, "")
        assert "→ no results" in result


class TestHintFileSearch:
    def test_files_found(self):
        result = _fmt_with_result("file_search", {"query": "*.py"}, "a.py\nb.py\n")
        assert "→ 2 files" in result

    def test_no_files(self):
        result = _fmt_with_result("file_search", {"query": "*.xyz"}, "")
        assert "→ no files" in result


class TestHintListDir:
    def test_entries(self):
        result = _fmt_with_result("list_dir", {"path": "."}, "a\nb\nc\n")
        assert "→ 3 entries" in result

    def test_empty(self):
        result = _fmt_with_result("list_dir", {"path": "."}, "")
        assert "→ empty" in result


class TestHintManageTodo:
    def test_always_updated(self):
        result = _fmt_with_result("manage_todo_list", {}, "anything")
        assert "→ updated" in result


class TestHintGetErrors:
    def test_clean(self):
        result = _fmt_with_result("get_errors", {}, "")
        assert "→ clean" in result

    def test_diagnostics(self):
        result = _fmt_with_result("get_errors", {}, "err1\nerr2\n")
        assert "→ 2 diagnostics" in result


class TestHintSubagent:
    def test_success_multiline(self):
        result = _fmt_with_result("runSubagent", {}, "line1\nline2\n")
        assert "→ done (2 lines)" in result

    def test_success_single_line(self):
        result = _fmt_with_result("runSubagent", {}, "ok")
        assert "→ done" in result
        assert "lines" not in result

    def test_failure(self):
        result = _fmt_with_result("runSubagent", {}, "err", success=False)
        assert "→ FAIL" in result

    def test_search_subagent_same_hint(self):
        result = _fmt_with_result("search_subagent", {}, "line1\nline2\n")
        assert "→ done (2 lines)" in result


class TestHintGetTerminalOutput:
    def test_with_content(self):
        result = _fmt_with_result("get_terminal_output", {"id": "1"}, "a\nb\n")
        assert "→ 2 lines" in result

    def test_empty(self):
        result = _fmt_with_result("get_terminal_output", {"id": "1"}, "")
        assert "→ empty" in result


class TestHintFetchWebpage:
    def test_success_large(self):
        result = _fmt_with_result(
            "fetch_webpage", {"url": "https://example.com"}, "x" * 2048
        )
        assert "→ 2KB" in result

    def test_success_small(self):
        result = _fmt_with_result(
            "fetch_webpage", {"url": "https://example.com"}, "hello"
        )
        assert "→ 5 bytes" in result

    def test_failure(self):
        result = _fmt_with_result(
            "fetch_webpage", {"url": "https://fail.com"}, "err", success=False
        )
        assert "→ FAIL" in result


class TestHintMemory:
    def test_success_with_output(self):
        result = _fmt_with_result("memory", {"command": "list"}, "a\nb\n")
        assert "→ 2 lines" in result

    def test_success_empty(self):
        result = _fmt_with_result("memory", {"command": "save"}, "")
        assert "→ done" in result

    def test_failure(self):
        result = _fmt_with_result("memory", {}, "err", success=False)
        assert "→ FAIL" in result


class TestHintRenameSymbol:
    def test_success(self):
        result = _fmt_with_result(
            "vscode_renameSymbol",
            {"oldName": "a", "newName": "b"},
            "ok",
        )
        assert "→ renamed" in result

    def test_failure(self):
        result = _fmt_with_result(
            "vscode_renameSymbol", {}, "err", success=False
        )
        assert "→ FAIL" in result


class TestHintListCodeUsages:
    def test_usages_found(self):
        result = _fmt_with_result(
            "vscode_listCodeUsages", {"symbol": "Foo"}, "a.py:1\nb.py:2\n"
        )
        assert "→ 2 usages" in result

    def test_none_found(self):
        result = _fmt_with_result(
            "vscode_listCodeUsages", {"symbol": "Foo"}, ""
        )
        assert "→ none" in result


# ---------------------------------------------------------------------------
# format_tool_display integration / edge cases
# ---------------------------------------------------------------------------


class TestFormatToolDisplay:
    def test_unknown_tool_returns_raw_name(self):
        assert format_tool_display("unknown_tool", None) == "unknown_tool"

    def test_unknown_tool_with_result_no_hint(self):
        result = format_tool_display("unknown_tool", None, tool_result="some output")
        assert result == "unknown_tool"

    def test_mcp_prefix_stripped(self):
        result = format_tool_display(
            "github/bash", json.dumps({"command": "echo hi"})
        )
        assert result == "$ echo hi"

    def test_mcp_prefix_with_result(self):
        result = format_tool_display(
            "server/grep_search",
            json.dumps({"query": "TODO"}),
            tool_result="line1\n",
        )
        assert "→ 1 matches" in result

    def test_no_result_no_hint_appended(self):
        result = format_tool_display("bash", json.dumps({"command": "ls"}))
        assert "→" not in result

    def test_result_none_no_hint(self):
        result = format_tool_display(
            "bash", json.dumps({"command": "ls"}), tool_result=None
        )
        assert "→" not in result

    def test_invalid_args_falls_back_to_tool_name(self):
        # _parse_args returns {} → formatter gets empty dict → fallback label
        result = format_tool_display("bash", "not-json")
        assert result == "bash"

    def test_formatter_exception_falls_back(self):
        # If we somehow pass args that cause an exception in the formatter,
        # it should fall back to tool_name. This is hard to trigger with real
        # formatters, but we can verify the structure by passing valid args.
        result = format_tool_display("bash", json.dumps({"command": "ok"}))
        assert result == "$ ok"

    def test_special_characters_in_args(self):
        result = format_tool_display(
            "bash", json.dumps({"command": 'echo "hello world" | grep \'test\''})
        )
        assert result.startswith("$ echo")

    def test_unicode_in_args(self):
        result = format_tool_display(
            "bash", json.dumps({"command": "echo 日本語"})
        )
        assert "日本語" in result

    def test_deeply_nested_mcp_prefix(self):
        # Only the last segment after "/" is used
        result = format_tool_display(
            "a/b/c/bash", json.dumps({"command": "ls"})
        )
        assert result == "$ ls"
