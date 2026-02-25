"""Tests for file-level embedding scaffold builder.

Tests the anglicified scaffold generation from tree-sitter
extracted defs and imports — the bridge between English-language
queries and code structure.
"""

from __future__ import annotations

import pytest

from codeplane.index._internal.indexing.file_embedding import (
    _DOC_MAX_COUNT,
    _build_config_defines,
    _build_embed_text,
    _build_enriched_chunks,
    _build_enrichment_lines,
    _compact_sig,
    _path_to_phrase,
    _word_split,
    build_file_scaffold,
)

# ---------------------------------------------------------------------------
# _word_split tests
# ---------------------------------------------------------------------------


class TestWordSplit:
    """Tests for identifier → word splitting."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("getUserById", ["get", "user", "by", "id"]),
            ("XMLParser", ["xml", "parser"]),
            ("snake_case_name", ["snake", "case", "name"]),
            ("PascalCase", ["pascal", "case"]),
            ("simpleword", ["simpleword"]),
            ("__init__", ["init"]),
            ("HTTP2Client", ["http", "2", "client"]),
            ("", []),
        ],
    )
    def test_splits(self, name: str, expected: list[str]) -> None:
        assert _word_split(name) == expected


# ---------------------------------------------------------------------------
# _path_to_phrase tests
# ---------------------------------------------------------------------------


class TestPathToPhrase:
    """Tests for file path → natural phrase conversion."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("src/auth/middleware/rate_limiter.py", "auth middleware rate limiter"),
            ("lib/utils/string_helper.js", "utils string helper"),
            ("app/models/UserProfile.ts", "models user profile"),
            ("core/base.py", "core base"),
            ("README.md", "readme"),
        ],
    )
    def test_paths(self, path: str, expected: str) -> None:
        assert _path_to_phrase(path) == expected


# ---------------------------------------------------------------------------
# _compact_sig tests
# ---------------------------------------------------------------------------


class TestCompactSig:
    """Tests for signature compaction."""

    def test_with_signature(self) -> None:
        result = _compact_sig("check_rate", "(self, request, limit)")
        assert result == "check rate(request, limit)"

    def test_no_signature(self) -> None:
        result = _compact_sig("get_value", "")
        assert result == "get value"

    def test_self_only(self) -> None:
        result = _compact_sig("reset", "(self)")
        assert result == "reset"


# ---------------------------------------------------------------------------
# build_file_scaffold tests
# ---------------------------------------------------------------------------


class TestBuildFileScaffold:
    """Tests for anglicified scaffold generation from tree-sitter data."""

    def test_empty_defs_and_imports(self) -> None:
        """Scaffold with no defs produces just module line."""
        result = build_file_scaffold("src/core/base.py", [], [])
        assert "module" in result
        assert "core base" in result

    def test_with_classes(self) -> None:
        defs = [
            {"kind": "class", "name": "RateLimiter", "signature_text": ""},
            {"kind": "method", "name": "check_rate", "signature_text": "(self, request)"},
        ]
        result = build_file_scaffold("src/rate_limiter.py", defs, [])
        assert "class rate limiter" in result.lower()
        assert "defines" in result

    def test_with_imports(self) -> None:
        imports = [
            {"imported_name": "os", "source_literal": "os"},
            {"imported_name": "Path", "source_literal": "pathlib"},
        ]
        result = build_file_scaffold("src/utils.py", [], imports)
        assert "imports" in result.lower()

    def test_with_functions(self) -> None:
        defs = [
            {"kind": "function", "name": "compute_hash", "signature_text": "(data: bytes)"},
            {"kind": "function", "name": "validate_input", "signature_text": "(value: str)"},
        ]
        result = build_file_scaffold("src/helpers.py", defs, [])
        assert "defines" in result
        assert "compute hash" in result

    def test_scaffold_includes_all_extraction_data(self) -> None:
        """Scaffold includes all defs and imports — no arbitrary truncation."""
        defs = [
            {
                "kind": "function",
                "name": f"very_long_function_name_{i}",
                "signature_text": "(a, b, c)",
            }
            for i in range(50)
        ]
        imports = [
            {"imported_name": f"module_{i}", "source_literal": f"package.module_{i}"}
            for i in range(30)
        ]
        result = build_file_scaffold("src/big_module.py", defs, imports)
        # All 50 functions should appear (no arbitrary cap)
        for i in range(50):
            assert f"very long function name {i}" in result
        # All 30 unique import sources should appear
        for i in range(30):
            assert f"module {i}" in result

    def test_with_docstring(self) -> None:
        defs = [
            {
                "kind": "class",
                "name": "Config",
                "signature_text": "",
                "docstring": "Configuration manager for application settings.",
            },
        ]
        result = build_file_scaffold("src/config.py", defs, [])
        assert "describes" in result.lower()
        # Should include def name as prefix
        assert "config:" in result.lower()

    def test_multiple_docstrings(self) -> None:
        """All meaningful docstrings should be included, not just the first."""
        defs = [
            {
                "kind": "function",
                "name": "connect",
                "signature_text": "(host, port)",
                "docstring": "Establish a database connection to the given host.",
            },
            {
                "kind": "function",
                "name": "disconnect",
                "signature_text": "()",
                "docstring": "Close the active database connection gracefully.",
            },
        ]
        result = build_file_scaffold("src/db.py", defs, [])
        assert "connect:" in result.lower()
        assert "disconnect:" in result.lower()
        assert result.lower().count("describes") == 2

    def test_docstring_count_capped(self) -> None:
        """Should cap docstrings at _DOC_MAX_COUNT."""
        defs = [
            {
                "kind": "function",
                "name": f"func_{i}",
                "signature_text": "()",
                "docstring": f"This is the docstring for function number {i} in the module.",
            }
            for i in range(_DOC_MAX_COUNT + 5)
        ]
        result = build_file_scaffold("src/big.py", defs, [])
        assert result.count("describes") == _DOC_MAX_COUNT

    def test_dedup_imports(self) -> None:
        """Duplicate import sources should be deduplicated."""
        imports = [
            {"imported_name": "A", "source_literal": "os"},
            {"imported_name": "B", "source_literal": "os"},
        ]
        result = build_file_scaffold("src/x.py", [], imports)
        # "os" should appear only once in imports line
        import_line = [line for line in result.split("\n") if line.startswith("imports")][0]
        assert import_line.count("os") == 1

    def test_no_defs_no_imports_from_path(self) -> None:
        """With only a path, scaffold should still produce module line."""
        result = build_file_scaffold("src/auth/middleware.py", [], [])
        assert result.startswith("module")

    def test_mixed_kinds_sorted(self) -> None:
        """Classes should appear before functions in defines."""
        defs = [
            {"kind": "function", "name": "helper_func", "signature_text": "()"},
            {"kind": "class", "name": "MainClass", "signature_text": ""},
            {"kind": "method", "name": "do_work", "signature_text": "(self)"},
        ]
        result = build_file_scaffold("src/main.py", defs, [])
        assert "class main class" in result.lower()
        # Class should come before function in the defines line
        defines_line = [line for line in result.split("\n") if line.startswith("defines")][0]
        class_pos = defines_line.lower().find("class")
        func_pos = defines_line.lower().find("helper")
        assert class_pos < func_pos


# ---------------------------------------------------------------------------
# _build_config_defines tests
# ---------------------------------------------------------------------------


class TestBuildConfigDefines:
    """Tests for config-file scaffold lines (targets, sections, keys, headings)."""

    def test_makefile_targets(self) -> None:
        defs = [
            {"kind": "target", "name": "build"},
            {"kind": "target", "name": "clean"},
            {"kind": "target", "name": "test"},
            {"kind": "target", "name": "lint"},
        ]
        lines = _build_config_defines(defs)
        assert len(lines) == 1
        assert lines[0].startswith("targets ")
        assert "build" in lines[0]
        assert "test" in lines[0]

    def test_makefile_variables(self) -> None:
        defs = [
            {"kind": "variable", "name": "CORE_VENV"},
            {"kind": "variable", "name": "COV_REPORT"},
        ]
        lines = _build_config_defines(defs)
        assert len(lines) == 1
        assert lines[0].startswith("variables ")
        assert "core venv" in lines[0]

    def test_phony_and_default_skipped(self) -> None:
        defs = [
            {"kind": "target", "name": ".PHONY"},
            {"kind": "variable", "name": ".DEFAULT_GOAL"},
            {"kind": "target", "name": "build"},
        ]
        lines = _build_config_defines(defs)
        combined = " ".join(lines)
        assert ".PHONY" not in combined
        assert ".DEFAULT_GOAL" not in combined
        assert "build" in combined

    def test_toml_tables(self) -> None:
        defs = [
            {"kind": "table", "name": "build-system"},
            {"kind": "table", "name": "project.scripts"},
            {"kind": "table", "name": "tool.pytest.ini_options"},
        ]
        lines = _build_config_defines(defs)
        assert len(lines) == 1
        assert lines[0].startswith("sections ")
        assert "build system" in lines[0]
        assert "project scripts" in lines[0]

    def test_toml_pairs(self) -> None:
        defs = [
            {"kind": "pair", "name": "addopts"},
            {"kind": "pair", "name": "asyncio_mode"},
        ]
        lines = _build_config_defines(defs)
        assert len(lines) == 1
        assert lines[0].startswith("configures ")
        assert "addopts" in lines[0]

    def test_yaml_keys(self) -> None:
        defs = [
            {"kind": "key", "name": "GITHUB_TOKEN"},
            {"kind": "key", "name": "coverage_report"},
        ]
        lines = _build_config_defines(defs)
        assert len(lines) == 1
        assert lines[0].startswith("configures ")

    def test_markdown_headings(self) -> None:
        defs = [
            {"kind": "heading", "name": "1. run_experiment"},
            {"kind": "heading", "name": "2. validate_config"},
        ]
        lines = _build_config_defines(defs)
        assert len(lines) == 1
        assert lines[0].startswith("topics ")
        # Numbering prefix should be stripped
        assert "1" not in lines[0]
        assert "run experiment" in lines[0]

    def test_mixed_config_kinds(self) -> None:
        defs = [
            {"kind": "target", "name": "build"},
            {"kind": "variable", "name": "VENV"},
            {"kind": "table", "name": "project"},
            {"kind": "pair", "name": "version"},
            {"kind": "heading", "name": "Overview"},
        ]
        lines = _build_config_defines(defs)
        assert len(lines) == 5
        line_types = [ln.split()[0] for ln in lines]
        assert "targets" in line_types
        assert "variables" in line_types
        assert "sections" in line_types
        assert "configures" in line_types
        assert "topics" in line_types

    def test_empty_defs(self) -> None:
        lines = _build_config_defines([])
        assert lines == []

    def test_source_code_kinds_ignored(self) -> None:
        """Source code kinds (class, function, method) are NOT config kinds."""
        defs = [
            {"kind": "class", "name": "MyClass"},
            {"kind": "function", "name": "helper"},
            {"kind": "method", "name": "do_work"},
        ]
        lines = _build_config_defines(defs)
        assert lines == []

    def test_scaffold_includes_config_defs(self) -> None:
        """build_file_scaffold integrates config defs into output."""
        defs = [
            {"kind": "target", "name": "build"},
            {"kind": "target", "name": "test"},
            {"kind": "variable", "name": "VENV_DIR"},
        ]
        result = build_file_scaffold("Makefile", defs, [])
        assert "targets" in result
        assert "build" in result
        assert "test" in result
        assert "variables" in result

    def test_dedup_config_names(self) -> None:
        """Duplicate kind:name pairs should be deduplicated."""
        defs = [
            {"kind": "key", "name": "TOKEN"},
            {"kind": "key", "name": "TOKEN"},
            {"kind": "key", "name": "SECRET"},
        ]
        lines = _build_config_defines(defs)
        assert len(lines) == 1
        # TOKEN should appear only once
        assert lines[0].count("token") == 1


# ---------------------------------------------------------------------------
# _build_embed_text tests
# ---------------------------------------------------------------------------


class TestBuildEmbedText:
    """Tests for composed embed text (scaffold only, no file content)."""

    def test_with_scaffold(self) -> None:
        scaffold = "module auth rate limiter\ndefines class RateLimiter"
        content = "class RateLimiter:\n    pass"
        result = _build_embed_text(scaffold, content)
        assert "FILE_SCAFFOLD" in result
        assert "module auth" in result
        # No FILE_CHUNK — scaffold-only
        assert "FILE_CHUNK" not in result
        # Raw content should NOT be in the embed text
        assert "class RateLimiter:\n    pass" not in result

    def test_without_scaffold(self) -> None:
        """Fallback: no scaffold → use truncated content."""
        result = _build_embed_text("", "print('hello')")
        assert "FILE_SCAFFOLD" not in result
        assert "print('hello')" in result

    def test_scaffold_truncated_at_budget(self) -> None:
        """Very large scaffolds should be capped at FILE_EMBED_MAX_CHARS."""
        scaffold = "module test\n" + "defines function x\n" * 500
        result = _build_embed_text(scaffold, "")
        from codeplane.index._internal.indexing.file_embedding import FILE_EMBED_MAX_CHARS

        assert len(result) <= FILE_EMBED_MAX_CHARS


# ---------------------------------------------------------------------------
# _detect_batch_size tests
# ---------------------------------------------------------------------------


class TestDetectBatchSize:
    """Tests for dynamic batch size detection."""

    def test_returns_positive_int(self) -> None:
        from codeplane.index._internal.indexing.file_embedding import _detect_batch_size

        result = _detect_batch_size()
        assert isinstance(result, int)
        assert result >= 4
        assert result <= 32


# ---------------------------------------------------------------------------
# _build_enrichment_lines tests
# ---------------------------------------------------------------------------


class TestBuildEnrichmentLines:
    """Tests for S+I+C+D enrichment signal extraction."""

    def test_empty_defs_and_imports(self) -> None:
        result = _build_enrichment_lines([], [])
        assert result == {}

    def test_string_literals_signal(self) -> None:
        """S signal: extracts string literals from _string_literals."""
        defs = [
            {
                "name": "load_config",
                "_string_literals": ["EVEE_MCP_MODE", "config.yaml", "ab", "true", ""],
            }
        ]
        result = _build_enrichment_lines(defs, [])
        assert "S" in result
        assert result["S"].startswith("mentions ")
        assert "EVEE_MCP_MODE" in result["S"]
        assert "config.yaml" in result["S"]
        # Too short (ab) and trivial (true, empty) should be excluded
        assert "ab" not in result["S"]
        assert "true" not in result["S"]

    def test_string_literals_budget(self) -> None:
        """S signal respects the character budget."""
        defs = [
            {
                "name": "f",
                "_string_literals": [f"literal_value_{i:04d}" for i in range(100)],
            }
        ]
        result = _build_enrichment_lines(defs, [])
        assert "S" in result
        from codeplane.index._internal.indexing.file_embedding import _STRING_LIT_BUDGET_CHARS

        # The mentions line prefix + content should stay within budget
        content_part = result["S"][len("mentions ") :]
        assert len(content_part) <= _STRING_LIT_BUDGET_CHARS + 50  # allow for last item

    def test_full_imports_signal(self) -> None:
        """I signal: full dotted import path, not just last segment."""
        imports = [
            {"imported_name": "Path", "source_literal": "pathlib"},
            {"imported_name": "Progress", "source_literal": "rich.progress"},
        ]
        result = _build_enrichment_lines([], imports)
        assert "I" in result
        assert result["I"].startswith("imports ")
        # Full path should be word-split: rich.progress → "rich progress"
        assert "rich progress" in result["I"]

    def test_calls_signal(self) -> None:
        """C signal: function/method call names from _sem_facts."""
        defs = [
            {
                "name": "setup",
                "_sem_facts": {"calls": ["load_dotenv", "Progress", "x"]},
            },
            {
                "name": "run",
                "_sem_facts": {"calls": ["Progress", "SpinnerColumn"]},
            },
        ]
        result = _build_enrichment_lines(defs, [])
        assert "C" in result
        assert result["C"].startswith("calls ")
        assert "load_dotenv" in result["C"]
        assert "Progress" in result["C"]
        assert "SpinnerColumn" in result["C"]
        # Single-char call 'x' should be excluded (len < 2)
        assert ", x" not in result["C"]

    def test_decorators_signal(self) -> None:
        """D signal: decorator names from decorators_json."""
        defs = [
            {
                "name": "cli",
                "decorators_json": '["@click.command()", "@property"]',
            }
        ]
        result = _build_enrichment_lines(defs, [])
        assert "D" in result
        assert result["D"].startswith("decorated ")
        assert "click.command" in result["D"]
        assert "property" in result["D"]

    def test_all_signals_present(self) -> None:
        """All four signals should be generated when data is available."""
        defs = [
            {
                "name": "handler",
                "_string_literals": ["api_key", "secret_token"],
                "_sem_facts": {"calls": ["authenticate", "validate_token"]},
                "decorators_json": '["@require_auth"]',
            }
        ]
        imports = [{"imported_name": "FastAPI", "source_literal": "fastapi"}]
        result = _build_enrichment_lines(defs, imports)
        assert "S" in result
        assert "I" in result
        assert "C" in result
        assert "D" in result

    def test_dedup_calls(self) -> None:
        """Duplicate call names across defs should be deduplicated."""
        defs = [
            {"name": "a", "_sem_facts": {"calls": ["log", "save"]}},
            {"name": "b", "_sem_facts": {"calls": ["log", "load"]}},
        ]
        result = _build_enrichment_lines(defs, [])
        calls_part = result["C"][len("calls ") :]
        # "log" should appear only once
        assert calls_part.count("log") == 1

    def test_dedup_string_literals(self) -> None:
        """Duplicate string literals across defs should be deduplicated."""
        defs = [
            {"name": "a", "_string_literals": ["config.yaml"]},
            {"name": "b", "_string_literals": ["config.yaml", "other.txt"]},
        ]
        result = _build_enrichment_lines(defs, [])
        mentions_part = result["S"][len("mentions ") :]
        assert mentions_part.count("config.yaml") == 1


# ---------------------------------------------------------------------------
# _build_enriched_chunks tests
# ---------------------------------------------------------------------------


class TestBuildEnrichedChunks:
    """Tests for enriched scaffold chunking (1-chunk and 2-chunk split)."""

    def test_single_chunk_small_scaffold(self) -> None:
        """Small enriched scaffold fits in one chunk."""
        scaffold = "module auth handler\nimports os\ndefines function check(request)"
        enrichment = {"S": "mentions API_KEY", "C": "calls validate"}
        chunks = _build_enriched_chunks(scaffold, enrichment, "")
        assert len(chunks) == 1
        assert "FILE_SCAFFOLD" in chunks[0]
        assert "mentions API_KEY" in chunks[0]
        assert "calls validate" in chunks[0]

    def test_import_replacement(self) -> None:
        """I signal replaces the short imports line with full paths."""
        scaffold = "module test\nimports os\ndefines function main()"
        enrichment = {"I": "imports operating system"}
        chunks = _build_enriched_chunks(scaffold, enrichment, "")
        assert len(chunks) == 1
        # Full import should replace the short one
        assert "imports operating system" in chunks[0]
        # Short import should NOT be present (replaced)
        lines = chunks[0].split("\n")
        import_lines = [ln for ln in lines if ln.startswith("imports ")]
        assert len(import_lines) == 1
        assert import_lines[0] == "imports operating system"

    def test_two_chunk_split_large_scaffold(self) -> None:
        """Large scaffold should split into 2 chunks."""
        # Create a scaffold that exceeds _CHUNK_SPLIT_CHARS when enriched
        defs_text = ", ".join(f"function very_long_function_name_{i}(a, b, c)" for i in range(60))
        scaffold = (
            f"module large module with many definitions\nimports many_modules\ndefines {defs_text}"
        )
        enrichment = {
            "I": "imports " + ", ".join(f"package_{i} module_{i}" for i in range(20)),
            "S": "mentions " + ", ".join(f"CONFIG_KEY_{i}" for i in range(20)),
            "C": "calls " + ", ".join(f"function_call_{i}" for i in range(15)),
            "D": "decorated dataclass, property, classmethod",
        }
        chunks = _build_enriched_chunks(scaffold, enrichment, "")
        assert len(chunks) == 2
        # Chunk 0: base scaffold (without enrichment signals)
        assert "FILE_SCAFFOLD" in chunks[0]
        assert "defines" in chunks[0]
        assert "mentions" not in chunks[0]
        # Chunk 1: module context + enrichment signals
        assert "FILE_SCAFFOLD" in chunks[1]
        assert "module " in chunks[1]
        assert "mentions " in chunks[1]
        assert "calls " in chunks[1]
        assert "decorated " in chunks[1]

    def test_fallback_no_scaffold(self) -> None:
        """Without scaffold, falls back to truncated content."""
        chunks = _build_enriched_chunks("", {}, "print('hello')")
        assert len(chunks) == 1
        assert "print('hello')" in chunks[0]
        assert "FILE_SCAFFOLD" not in chunks[0]

    def test_no_enrichment_single_chunk(self) -> None:
        """Scaffold without enrichment stays as single chunk."""
        scaffold = "module test\ndefines function main()"
        chunks = _build_enriched_chunks(scaffold, {}, "")
        assert len(chunks) == 1
        assert "FILE_SCAFFOLD" in chunks[0]
        assert "module test" in chunks[0]

    def test_chunk_respects_max_chars(self) -> None:
        """Chunks should not exceed FILE_EMBED_MAX_CHARS."""
        from codeplane.index._internal.indexing.file_embedding import FILE_EMBED_MAX_CHARS

        scaffold = "module test\n" + "defines function x\n" * 500
        enrichment = {"S": "mentions " + "x" * 500}
        chunks = _build_enriched_chunks(scaffold, enrichment, "")
        for chunk in chunks:
            assert len(chunk) <= FILE_EMBED_MAX_CHARS
