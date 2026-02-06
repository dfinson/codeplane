"""Tests for refactor ops helper functions and dataclasses.

Covers:
- _scan_file_for_comment_occurrences
- _word_boundary_match
- _compute_rename_certainty_from_ref
- EditHunk, FileEdit, RefactorPreview dataclasses
- InspectResult, RefactorDivergence, RefactorResult dataclasses
"""

from unittest.mock import MagicMock

from codeplane.refactor.ops import (
    EditHunk,
    FileEdit,
    InspectResult,
    RefactorDivergence,
    RefactorPreview,
    RefactorResult,
    _compute_rename_certainty_from_ref,
    _scan_file_for_comment_occurrences,
    _word_boundary_match,
)


class TestWordBoundaryMatch:
    """Tests for _word_boundary_match helper."""

    def test_matches_whole_word(self):
        assert _word_boundary_match("the function name", "function") is True

    def test_does_not_match_partial_word(self):
        assert _word_boundary_match("functionName", "function") is False

    def test_matches_at_start(self):
        assert _word_boundary_match("function starts here", "function") is True

    def test_matches_at_end(self):
        assert _word_boundary_match("call the function", "function") is True

    def test_matches_standalone(self):
        assert _word_boundary_match("function", "function") is True

    def test_does_not_match_substring(self):
        assert _word_boundary_match("dysfunctional", "function") is False

    def test_handles_special_regex_chars(self):
        # Symbol with regex special characters - $ is escaped but
        # \b (word boundary) doesn't match between space and $
        # since $ is not a word character, so the match fails
        assert _word_boundary_match("call foo.bar here", "foo.bar") is True
        # Period is escaped and matched correctly

    def test_case_sensitive(self):
        assert _word_boundary_match("Function", "function") is False

    def test_handles_underscores_as_word_boundary(self):
        # Note: Python \b considers underscore as word character
        assert _word_boundary_match("my_function_name", "function") is False


class TestScanFileForCommentOccurrences:
    """Tests for _scan_file_for_comment_occurrences helper."""

    def test_finds_python_comment(self):
        content = """# This function is important
x = 1
"""
        results = _scan_file_for_comment_occurrences(content, "function", "python")
        assert len(results) == 1
        assert results[0][0] == 1  # line 1
        assert "function" in results[0][1]

    def test_finds_python_docstring(self):
        content = '''def foo():
    """This function does things."""
    pass
'''
        results = _scan_file_for_comment_occurrences(content, "function", "python")
        assert len(results) == 1
        assert results[0][0] == 2  # line 2

    def test_finds_multiline_docstring(self):
        content = '''def foo():
    """
    This function does things.
    It has multiple lines.
    """
    pass
'''
        results = _scan_file_for_comment_occurrences(content, "function", "python")
        assert len(results) == 1
        assert results[0][0] == 3  # line 3

    def test_finds_js_single_line_comment(self):
        content = """// This function is called
const x = 1;
"""
        results = _scan_file_for_comment_occurrences(content, "function", "javascript")
        assert len(results) == 1
        assert results[0][0] == 1

    def test_finds_js_block_comment(self):
        content = """/* This function is used */
const x = 1;
"""
        results = _scan_file_for_comment_occurrences(content, "function", "javascript")
        assert len(results) == 1
        assert results[0][0] == 1

    def test_finds_multiline_js_comment(self):
        content = """/*
 * This function is complex.
 */
const x = 1;
"""
        results = _scan_file_for_comment_occurrences(content, "function", "javascript")
        assert len(results) == 1
        assert results[0][0] == 2

    def test_typescript_uses_js_patterns(self):
        content = "// function description\nlet x = 1;"
        results = _scan_file_for_comment_occurrences(content, "function", "typescript")
        assert len(results) == 1

    def test_go_uses_c_style_comments(self):
        content = "// function docs\nfunc main() {}"
        results = _scan_file_for_comment_occurrences(content, "function", "go")
        assert len(results) == 1

    def test_rust_uses_c_style_comments(self):
        content = "// This function is important\nfn main() {}"
        results = _scan_file_for_comment_occurrences(content, "function", "rust")
        assert len(results) == 1

    def test_cpp_uses_c_style_comments(self):
        content = "/* function description */\nint main() {}"
        results = _scan_file_for_comment_occurrences(content, "function", "cpp")
        assert len(results) == 1

    def test_java_uses_c_style_comments(self):
        content = "// function info\nclass Foo {}"
        results = _scan_file_for_comment_occurrences(content, "function", "java")
        assert len(results) == 1

    def test_returns_empty_for_no_match(self):
        content = "# This comment has no match\nx = 1"
        results = _scan_file_for_comment_occurrences(content, "foobar", "python")
        assert len(results) == 0

    def test_none_language_uses_python_patterns(self):
        content = "# test function\nx = 1"
        results = _scan_file_for_comment_occurrences(content, "function", None)
        assert len(results) == 1


class TestComputeRenameCertaintyFromRef:
    """Tests for _compute_rename_certainty_from_ref helper."""

    def test_proven_ref_tier_returns_high(self):
        ref = MagicMock()
        ref.ref_tier = "PROVEN"
        ref.certainty = None
        assert _compute_rename_certainty_from_ref(ref) == "high"

    def test_strong_ref_tier_returns_high(self):
        ref = MagicMock()
        ref.ref_tier = "STRONG"
        ref.certainty = None
        assert _compute_rename_certainty_from_ref(ref) == "high"

    def test_anchored_ref_tier_returns_medium(self):
        ref = MagicMock()
        ref.ref_tier = "ANCHORED"
        ref.certainty = None
        assert _compute_rename_certainty_from_ref(ref) == "medium"

    def test_certain_certainty_returns_high(self):
        ref = MagicMock()
        ref.ref_tier = None
        ref.certainty = "CERTAIN"
        assert _compute_rename_certainty_from_ref(ref) == "high"

    def test_unknown_ref_tier_falls_through(self):
        ref = MagicMock()
        ref.ref_tier = "UNKNOWN"
        ref.certainty = None
        assert _compute_rename_certainty_from_ref(ref) == "low"

    def test_no_ref_tier_or_certainty_returns_low(self):
        ref = MagicMock()
        ref.ref_tier = None
        ref.certainty = None
        assert _compute_rename_certainty_from_ref(ref) == "low"

    def test_lowercase_ref_tier_proven(self):
        ref = MagicMock()
        ref.ref_tier = "proven"
        ref.certainty = None
        assert _compute_rename_certainty_from_ref(ref) == "high"

    def test_lowercase_certainty_certain(self):
        ref = MagicMock()
        ref.ref_tier = None
        ref.certainty = "certain"
        assert _compute_rename_certainty_from_ref(ref) == "high"


class TestEditHunk:
    """Tests for EditHunk dataclass."""

    def test_basic_creation(self):
        hunk = EditHunk(
            old="old_name",
            new="new_name",
            line=42,
            certainty="high",
        )
        assert hunk.old == "old_name"
        assert hunk.new == "new_name"
        assert hunk.line == 42
        assert hunk.certainty == "high"

    def test_medium_certainty(self):
        hunk = EditHunk(old="x", new="y", line=1, certainty="medium")
        assert hunk.certainty == "medium"

    def test_low_certainty(self):
        hunk = EditHunk(old="x", new="y", line=1, certainty="low")
        assert hunk.certainty == "low"


class TestFileEdit:
    """Tests for FileEdit dataclass."""

    def test_basic_creation(self):
        edit = FileEdit(path="src/main.py")
        assert edit.path == "src/main.py"
        assert edit.hunks == []

    def test_with_hunks(self):
        edit = FileEdit(
            path="src/main.py",
            hunks=[
                EditHunk(old="a", new="b", line=1, certainty="high"),
                EditHunk(old="c", new="d", line=5, certainty="low"),
            ],
        )
        assert len(edit.hunks) == 2


class TestRefactorPreview:
    """Tests for RefactorPreview dataclass."""

    def test_basic_creation(self):
        preview = RefactorPreview(
            files_affected=3,
        )
        assert preview.files_affected == 3
        assert preview.edits == []
        assert preview.contexts_used == []
        assert preview.high_certainty_count == 0
        assert preview.medium_certainty_count == 0
        assert preview.low_certainty_count == 0
        assert preview.verification_required is False
        assert preview.low_certainty_files == []
        assert preview.verification_guidance is None

    def test_full_creation(self):
        preview = RefactorPreview(
            files_affected=5,
            edits=[FileEdit(path="a.py")],
            contexts_used=["index", "lexical"],
            high_certainty_count=10,
            medium_certainty_count=3,
            low_certainty_count=2,
            verification_required=True,
            low_certainty_files=["a.py", "b.py"],
            verification_guidance="Please review low certainty renames.",
        )
        assert preview.files_affected == 5
        assert len(preview.edits) == 1
        assert preview.verification_required is True


class TestInspectResult:
    """Tests for InspectResult dataclass."""

    def test_basic_creation(self):
        result = InspectResult(
            path="src/utils.py",
            matches=[],
        )
        assert result.path == "src/utils.py"
        assert result.matches == []

    def test_with_matches(self):
        result = InspectResult(
            path="src/utils.py",
            matches=[
                {"line": 10, "snippet": "old_name"},
                {"line": 25, "snippet": "old_name again"},
            ],
        )
        assert len(result.matches) == 2
        assert result.matches[0]["line"] == 10


class TestRefactorDivergence:
    """Tests for RefactorDivergence dataclass."""

    def test_empty_creation(self):
        div = RefactorDivergence()
        assert div.conflicting_hunks == []
        assert div.resolution_options == []

    def test_with_conflicts(self):
        div = RefactorDivergence(
            conflicting_hunks=[{"file": "a.py", "hunks": ["hunk1"]}],
            resolution_options=["abort", "force", "merge"],
        )
        assert len(div.conflicting_hunks) == 1
        assert "abort" in div.resolution_options


class TestRefactorResult:
    """Tests for RefactorResult dataclass."""

    def test_previewed_status(self):
        result = RefactorResult(
            refactor_id="ref_123",
            status="previewed",
            preview=RefactorPreview(files_affected=2),
        )
        assert result.status == "previewed"
        assert result.preview is not None
        assert result.applied is None
        assert result.divergence is None

    def test_applied_status(self):
        result = RefactorResult(
            refactor_id="ref_456",
            status="applied",
            applied=MagicMock(),
        )
        assert result.status == "applied"
        assert result.applied is not None

    def test_cancelled_status(self):
        result = RefactorResult(
            refactor_id="ref_789",
            status="cancelled",
        )
        assert result.status == "cancelled"

    def test_divergence_status(self):
        result = RefactorResult(
            refactor_id="ref_abc",
            status="divergence",
            divergence=RefactorDivergence(resolution_options=["abort"]),
        )
        assert result.status == "divergence"
        assert result.divergence is not None
