"""Tests for core.formatting utilities.

Tests the summary formatting utilities used for consistent terminal output.
"""

from __future__ import annotations

from codeplane.core.formatting import (
    compress_path,
    format_path_list,
    pluralize,
    truncate_at_word,
    truncate_query,
)


class TestCompressPath:
    """Tests for compress_path function."""

    def test_short_path_unchanged(self) -> None:
        """Paths under max_len are returned unchanged."""
        assert compress_path("short/path.py", max_len=30) == "short/path.py"

    def test_path_at_max_len_unchanged(self) -> None:
        """Path exactly at max_len is unchanged."""
        path = "a" * 30
        assert compress_path(path, max_len=30) == path

    def test_long_path_compressed(self) -> None:
        """Long paths are compressed to first/last segments."""
        result = compress_path("src/codeplane/mcp/tools/mutation.py", max_len=30)
        assert result == "src/.../mutation.py"

    def test_very_long_path_uses_filename_only(self) -> None:
        """When even compressed form is too long, use filename only."""
        # First + ... + last is too long
        result = compress_path("very_long_directory_name/another_long_name/filename.py", max_len=20)
        assert result == "filename.py"

    def test_two_segment_path_unchanged(self) -> None:
        """Paths with only 2 segments can't be compressed."""
        result = compress_path("src/very_long_filename_that_exceeds.py", max_len=20)
        # Can't compress, keeps as-is
        assert result == "src/very_long_filename_that_exceeds.py"

    def test_single_segment_path_unchanged(self) -> None:
        """Single segment paths (no slash) are unchanged."""
        result = compress_path("filename.py", max_len=5)
        assert result == "filename.py"

    def test_empty_path(self) -> None:
        """Empty path returns empty string."""
        assert compress_path("", max_len=30) == ""

    def test_custom_max_len(self) -> None:
        """Custom max_len is respected."""
        path = "a/b/c/d/e.py"
        assert compress_path(path, max_len=50) == path
        assert compress_path(path, max_len=10) == "a/.../e.py"


class TestFormatPathList:
    """Tests for format_path_list function."""

    def test_empty_list(self) -> None:
        """Empty list returns empty string."""
        assert format_path_list([]) == ""

    def test_single_path(self) -> None:
        """Single path is returned as-is."""
        assert format_path_list(["a.py"]) == "a.py"

    def test_two_paths(self) -> None:
        """Two paths are joined with comma."""
        result = format_path_list(["a.py", "b.py"])
        assert result == "a.py, b.py"

    def test_three_paths_within_max_shown(self) -> None:
        """Three paths shown when max_shown=3."""
        result = format_path_list(["a.py", "b.py", "c.py"], max_shown=3)
        assert result == "a.py, b.py, c.py"

    def test_exceeds_max_shown(self) -> None:
        """Paths exceeding max_shown show '+N more'."""
        result = format_path_list(["a.py", "b.py", "c.py", "d.py"], max_shown=3)
        assert result == "a.py, b.py, +2 more"

    def test_exceeds_max_total_reduces_further(self) -> None:
        """When result exceeds max_total, reduce to first + more."""
        result = format_path_list(
            ["long_name1.py", "long_name2.py", "long_name3.py"],
            max_total=20,
            max_shown=3,
        )
        # Should reduce to "long_name1.py, +2 more" or "3 files" if still too long
        assert "+" in result or "files" in result

    def test_very_small_max_total_uses_count(self) -> None:
        """When even shortened form is too long, use 'N files'."""
        result = format_path_list(
            ["a.py", "b.py", "c.py", "d.py"],
            max_total=5,
            max_shown=3,
        )
        assert result == "4 files"

    def test_compress_option(self) -> None:
        """compress=True compresses individual paths."""
        paths = ["src/deep/nested/path/file.py"]
        result_compressed = format_path_list(paths, compress=True)
        result_not_compressed = format_path_list(paths, compress=False)
        # Compressed should be shorter (or same if already short)
        assert len(result_compressed) <= len(result_not_compressed)

    def test_compress_false(self) -> None:
        """compress=False keeps paths unchanged."""
        long_path = "src/codeplane/mcp/tools/mutation.py"
        result = format_path_list([long_path], compress=False)
        assert result == long_path


class TestPluralize:
    """Tests for pluralize function."""

    def test_singular(self) -> None:
        """Count of 1 uses singular form."""
        assert pluralize(1, "file") == "1 file"

    def test_zero(self) -> None:
        """Count of 0 uses plural form."""
        assert pluralize(0, "file") == "0 files"

    def test_plural(self) -> None:
        """Count > 1 uses plural form."""
        assert pluralize(2, "file") == "2 files"
        assert pluralize(100, "file") == "100 files"

    def test_custom_plural(self) -> None:
        """Custom plural form is used when provided."""
        assert pluralize(2, "index", "indices") == "2 indices"
        assert pluralize(1, "index", "indices") == "1 index"

    def test_irregular_plurals(self) -> None:
        """Various irregular plural forms work."""
        assert pluralize(2, "match", "matches") == "2 matches"
        assert pluralize(2, "entry", "entries") == "2 entries"

    def test_negative_count(self) -> None:
        """Negative counts use plural form."""
        # Edge case - should use plural for non-1 values
        assert pluralize(-1, "file") == "-1 files"


class TestTruncateAtWord:
    """Tests for truncate_at_word function."""

    def test_short_text_unchanged(self) -> None:
        """Text under max_len is unchanged."""
        text = "short text"
        assert truncate_at_word(text, max_len=40) == text

    def test_text_at_max_len_unchanged(self) -> None:
        """Text exactly at max_len is unchanged."""
        text = "exactly forty characters long text here!"
        assert truncate_at_word(text, max_len=40) == text

    def test_truncates_at_word_boundary(self) -> None:
        """Long text is truncated at word boundary."""
        text = "fix: update parser to handle edge cases properly"
        result = truncate_at_word(text, max_len=30)
        assert result.endswith("...")
        # Should not cut mid-word
        assert result in ["fix: update parser to...", "fix: update parser..."]

    def test_custom_suffix(self) -> None:
        """Custom suffix is used."""
        result = truncate_at_word("this is a long sentence", max_len=15, suffix=">>")
        assert result.endswith(">>")

    def test_no_space_hard_cut(self) -> None:
        """Text with no spaces does hard cut."""
        text = "verylongwordwithoutspaces"
        result = truncate_at_word(text, max_len=15)
        assert result == "verylongword..."

    def test_empty_text(self) -> None:
        """Empty text returns empty string."""
        assert truncate_at_word("", max_len=40) == ""

    def test_very_short_max_len(self) -> None:
        """Very short max_len returns just suffix if cut_at <= 0."""
        result = truncate_at_word("hello world", max_len=3)
        assert result == "..."

    def test_max_len_equals_suffix_len(self) -> None:
        """Edge case where max_len equals suffix length."""
        result = truncate_at_word("hello world", max_len=3, suffix="...")
        assert result == "..."


class TestTruncateQuery:
    """Tests for truncate_query function."""

    def test_short_query_unchanged(self) -> None:
        """Short queries are unchanged."""
        assert truncate_query("short", max_len=20) == "short"

    def test_query_at_max_len_unchanged(self) -> None:
        """Query exactly at max_len is unchanged."""
        query = "a" * 20
        assert truncate_query(query, max_len=20) == query

    def test_long_query_truncated(self) -> None:
        """Long queries are truncated with ellipsis."""
        query = "def _summarize_write_result"
        result = truncate_query(query, max_len=20)
        assert result == "def _summarize_wr..."
        assert len(result) == 20

    def test_empty_query(self) -> None:
        """Empty query returns empty string."""
        assert truncate_query("", max_len=20) == ""

    def test_custom_max_len(self) -> None:
        """Custom max_len is respected."""
        query = "search query"
        assert truncate_query(query, max_len=10) == "search ..."
        assert truncate_query(query, max_len=50) == query
