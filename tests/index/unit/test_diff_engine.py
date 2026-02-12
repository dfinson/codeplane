"""Unit tests for structural diff engine (engine.py).

Tests cover:
- Basic diff operations (added, removed, signature_changed, body_changed)
- Rename detection
- Variable filtering (internal vs external)
- Hunk intersection
- Non-structural file detection
- Empty diff
"""

from __future__ import annotations

from codeplane.index._internal.diff.engine import (
    _detect_renames,
    _intersects_hunks,
    _is_internal_variable,
    compute_structural_diff,
)
from codeplane.index._internal.diff.models import ChangedFile, DefSnapshot

# ============================================================================
# Fixtures
# ============================================================================


def _snap(
    kind: str = "function",
    name: str = "foo",
    lexical_path: str | None = None,
    sig_hash: str | None = "abc123",
    display_name: str | None = "def foo()",
    start: int = 1,
    end: int = 10,
) -> DefSnapshot:
    return DefSnapshot(
        kind=kind,
        name=name,
        lexical_path=lexical_path or name,
        signature_hash=sig_hash,
        display_name=display_name,
        start_line=start,
        end_line=end,
    )


# ============================================================================
# Tests: Hunk Intersection
# ============================================================================


class TestHunkIntersection:
    """Tests for _intersects_hunks."""

    def test_intersects_with_overlap(self) -> None:
        assert _intersects_hunks(5, 15, [(10, 20)]) is True

    def test_no_intersection(self) -> None:
        assert _intersects_hunks(1, 5, [(10, 20)]) is False

    def test_exact_boundary(self) -> None:
        assert _intersects_hunks(10, 20, [(20, 30)]) is True

    def test_empty_hunks(self) -> None:
        assert _intersects_hunks(5, 15, []) is False

    def test_none_hunks_epoch_mode(self) -> None:
        assert _intersects_hunks(5, 15, None) is True

    def test_multiple_hunks_one_match(self) -> None:
        assert _intersects_hunks(5, 8, [(1, 3), (6, 10), (20, 30)]) is True


# ============================================================================
# Tests: Internal Variable Detection
# ============================================================================


class TestInternalVariable:
    """Tests for _is_internal_variable."""

    def test_variable_inside_function(self) -> None:
        func = _snap(kind="function", name="foo", start=1, end=20)
        var = _snap(kind="variable", name="x", start=5, end=5)
        assert _is_internal_variable(var, [func, var]) is True

    def test_variable_outside_function(self) -> None:
        func = _snap(kind="function", name="foo", start=10, end=20)
        var = _snap(kind="variable", name="x", start=1, end=1)
        assert _is_internal_variable(var, [func, var]) is False

    def test_function_not_internal(self) -> None:
        func = _snap(kind="function", name="foo", start=1, end=20)
        assert _is_internal_variable(func, [func]) is False

    def test_class_attribute_not_internal(self) -> None:
        cls = _snap(kind="class", name="Foo", start=1, end=20)
        var = _snap(kind="variable", name="x", start=5, end=5)
        assert _is_internal_variable(var, [cls, var]) is False


# ============================================================================
# Tests: Rename Detection
# ============================================================================


class TestRenameDetection:
    """Tests for _detect_renames."""

    def test_detects_rename(self) -> None:
        old = _snap(kind="function", name="old_func", sig_hash="same")
        new = _snap(kind="function", name="new_func", sig_hash="same")
        renames = _detect_renames(
            [(("function", "old_func"), old)],
            [(("function", "new_func"), new)],
        )
        assert len(renames) == 1
        assert renames[0] == (old, new)

    def test_no_rename_different_sig(self) -> None:
        old = _snap(kind="function", name="old_func", sig_hash="sig1")
        new = _snap(kind="function", name="new_func", sig_hash="sig2")
        renames = _detect_renames(
            [(("function", "old_func"), old)],
            [(("function", "new_func"), new)],
        )
        assert len(renames) == 0

    def test_no_rename_different_kind(self) -> None:
        old = _snap(kind="function", name="foo", sig_hash="same")
        new = _snap(kind="class", name="Foo", sig_hash="same")
        renames = _detect_renames(
            [(("function", "foo"), old)],
            [(("class", "Foo"), new)],
        )
        assert len(renames) == 0

    def test_no_rename_null_sig(self) -> None:
        old = _snap(kind="variable", name="x", sig_hash=None)
        new = _snap(kind="variable", name="y", sig_hash=None)
        renames = _detect_renames(
            [(("variable", "x"), old)],
            [(("variable", "y"), new)],
        )
        assert len(renames) == 0


# ============================================================================
# Tests: Full Structural Diff
# ============================================================================


class TestComputeStructuralDiff:
    """Tests for compute_structural_diff."""

    def test_added_function(self) -> None:
        target = _snap(kind="function", name="new_func")
        result = compute_structural_diff(
            base_facts={"src/a.py": []},
            target_facts={"src/a.py": [target]},
            changed_files=[ChangedFile("src/a.py", "modified", True)],
        )
        assert len(result.changes) == 1
        assert result.changes[0].change == "added"
        assert result.changes[0].name == "new_func"
        assert result.changes[0].structural_severity == "non_breaking"

    def test_removed_function(self) -> None:
        base = _snap(kind="function", name="old_func")
        result = compute_structural_diff(
            base_facts={"src/a.py": [base]},
            target_facts={"src/a.py": []},
            changed_files=[ChangedFile("src/a.py", "modified", True)],
        )
        assert len(result.changes) == 1
        assert result.changes[0].change == "removed"
        assert result.changes[0].structural_severity == "breaking"

    def test_signature_changed(self) -> None:
        base = _snap(kind="function", name="foo", sig_hash="old")
        target = _snap(kind="function", name="foo", sig_hash="new")
        result = compute_structural_diff(
            base_facts={"src/a.py": [base]},
            target_facts={"src/a.py": [target]},
            changed_files=[ChangedFile("src/a.py", "modified", True)],
        )
        assert len(result.changes) == 1
        assert result.changes[0].change == "signature_changed"
        assert result.changes[0].structural_severity == "breaking"

    def test_body_changed_with_hunks(self) -> None:
        snap = _snap(kind="function", name="foo", sig_hash="same", start=5, end=15)
        result = compute_structural_diff(
            base_facts={"src/a.py": [snap]},
            target_facts={"src/a.py": [snap]},
            changed_files=[ChangedFile("src/a.py", "modified", True)],
            hunks={"src/a.py": [(10, 12)]},
        )
        assert len(result.changes) == 1
        assert result.changes[0].change == "body_changed"
        assert result.changes[0].structural_severity == "non_breaking"

    def test_body_changed_no_hunks_epoch_mode(self) -> None:
        snap = _snap(kind="function", name="foo", sig_hash="same")
        result = compute_structural_diff(
            base_facts={"src/a.py": [snap]},
            target_facts={"src/a.py": [snap]},
            changed_files=[ChangedFile("src/a.py", "modified", True)],
            hunks=None,
        )
        assert len(result.changes) == 1
        assert result.changes[0].change == "body_changed"

    def test_no_change_when_hunks_dont_intersect(self) -> None:
        snap = _snap(kind="function", name="foo", sig_hash="same", start=5, end=15)
        result = compute_structural_diff(
            base_facts={"src/a.py": [snap]},
            target_facts={"src/a.py": [snap]},
            changed_files=[ChangedFile("src/a.py", "modified", True)],
            hunks={"src/a.py": [(50, 60)]},
        )
        assert len(result.changes) == 0
        assert len(result.non_structural_files) == 1

    def test_rename_detection(self) -> None:
        old = _snap(kind="function", name="old_func", sig_hash="same")
        new = _snap(kind="function", name="new_func", sig_hash="same")
        result = compute_structural_diff(
            base_facts={"src/a.py": [old]},
            target_facts={"src/a.py": [new]},
            changed_files=[ChangedFile("src/a.py", "modified", True)],
        )
        assert len(result.changes) == 1
        assert result.changes[0].change == "renamed"
        assert result.changes[0].structural_severity == "breaking"

    def test_empty_diff(self) -> None:
        result = compute_structural_diff(
            base_facts={},
            target_facts={},
            changed_files=[],
        )
        assert len(result.changes) == 0
        assert result.files_analyzed == 0

    def test_non_structural_file(self) -> None:
        result = compute_structural_diff(
            base_facts={},
            target_facts={},
            changed_files=[ChangedFile("README.md", "modified", False)],
        )
        assert len(result.changes) == 0
        assert result.non_structural_files[0].path == "README.md"

    def test_multiple_files(self) -> None:
        base_a = _snap(kind="function", name="foo")
        target_b = _snap(kind="class", name="Bar")
        result = compute_structural_diff(
            base_facts={"src/a.py": [base_a], "src/b.py": []},
            target_facts={"src/a.py": [], "src/b.py": [target_b]},
            changed_files=[
                ChangedFile("src/a.py", "modified", True),
                ChangedFile("src/b.py", "modified", True),
            ],
        )
        assert len(result.changes) == 2
        assert result.files_analyzed == 2

    def test_method_qualified_name(self) -> None:
        target = _snap(kind="method", name="foo", lexical_path="MyClass.foo")
        result = compute_structural_diff(
            base_facts={"src/a.py": []},
            target_facts={"src/a.py": [target]},
            changed_files=[ChangedFile("src/a.py", "modified", True)],
        )
        assert result.changes[0].qualified_name == "MyClass.foo"


# ============================================================================
# Tests: Delta Tags
# ============================================================================


class TestDeltaTags:
    """Tests for _compute_delta_tags and derived tag functions."""

    def test_added_symbol_tag(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        tags = _compute_delta_tags("added", None, _snap())
        assert tags == ["symbol_added"]

    def test_removed_symbol_tag(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        tags = _compute_delta_tags("removed", _snap(), None)
        assert tags == ["symbol_removed"]

    def test_renamed_symbol_tag(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        old = _snap(name="old_func", display_name="def old_func()")
        new = _snap(name="new_func", display_name="def new_func()")
        tags = _compute_delta_tags("renamed", old, new)
        assert "symbol_renamed" in tags

    def test_signature_parameters_changed(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        old = _snap(display_name="def foo(x: int)")
        new = _snap(display_name="def foo(x: int, y: str)")
        tags = _compute_delta_tags("signature_changed", old, new)
        assert "parameters_changed" in tags

    def test_signature_return_type_changed(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        old = _snap(display_name="def foo(x: int) -> int")
        new = _snap(display_name="def foo(x: int) -> str")
        tags = _compute_delta_tags("signature_changed", old, new)
        assert "return_type_changed" in tags
        assert "parameters_changed" not in tags

    def test_signature_both_changed(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        old = _snap(display_name="def foo(x: int) -> int")
        new = _snap(display_name="def foo(x: str) -> str")
        tags = _compute_delta_tags("signature_changed", old, new)
        assert "parameters_changed" in tags
        assert "return_type_changed" in tags

    def test_body_minor_change(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        tags = _compute_delta_tags("body_changed", _snap(), _snap(), lines_changed=2)
        assert "minor_change" in tags
        assert "possibly_comment_or_whitespace" in tags

    def test_body_logic_change(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        tags = _compute_delta_tags("body_changed", _snap(), _snap(), lines_changed=10)
        assert tags == ["body_logic_changed"]

    def test_body_major_change(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        tags = _compute_delta_tags("body_changed", _snap(), _snap(), lines_changed=25)
        assert tags == ["major_change"]

    def test_body_no_lines_defaults_to_logic(self) -> None:
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        tags = _compute_delta_tags("body_changed", _snap(), _snap())
        assert tags == ["body_logic_changed"]

    def test_body_minor_3_lines_no_comment_guard(self) -> None:
        """3 lines_changed gets minor_change but NOT possibly_comment_or_whitespace."""
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        tags = _compute_delta_tags("body_changed", _snap(), _snap(), lines_changed=3)
        assert tags == ["minor_change"]
        assert "possibly_comment_or_whitespace" not in tags

    def test_body_1_line_gets_comment_guard(self) -> None:
        """1 line change triggers the comment-only misclassification guard."""
        from codeplane.index._internal.diff.engine import _compute_delta_tags

        tags = _compute_delta_tags("body_changed", _snap(), _snap(), lines_changed=1)
        assert "minor_change" in tags
        assert "possibly_comment_or_whitespace" in tags


class TestExtractParams:
    """Tests for _extract_params."""

    def test_simple_params(self) -> None:
        from codeplane.index._internal.diff.engine import _extract_params

        assert _extract_params("def foo(x, y)") == "(x, y)"

    def test_no_params(self) -> None:
        from codeplane.index._internal.diff.engine import _extract_params

        assert _extract_params("class Foo") == ""

    def test_nested_parens(self) -> None:
        from codeplane.index._internal.diff.engine import _extract_params

        assert _extract_params("def foo(x: tuple[int, str])") == "(x: tuple[int, str])"

    def test_empty_parens(self) -> None:
        from codeplane.index._internal.diff.engine import _extract_params

        assert _extract_params("def foo()") == "()"


class TestExtractReturnType:
    """Tests for _extract_return_type."""

    def test_python_style(self) -> None:
        from codeplane.index._internal.diff.engine import _extract_return_type

        assert _extract_return_type("def foo(x: int) -> bool") == "bool"

    def test_no_return_type(self) -> None:
        from codeplane.index._internal.diff.engine import _extract_return_type

        assert _extract_return_type("def foo(x: int)") == ""

    def test_complex_return_type(self) -> None:
        from codeplane.index._internal.diff.engine import _extract_return_type

        result = _extract_return_type("def foo(x) -> list[int]")
        assert result == "list[int]"
