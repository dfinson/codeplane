"""Tests for git/_internal/parsing.py module.

Covers:
- extract_local_branch_from_remote()
- extract_tag_name()
- extract_branch_name()
- first_line()
- make_tag_ref()
- make_branch_ref()
"""

from __future__ import annotations

from codeplane.git._internal.parsing import (
    extract_branch_name,
    extract_local_branch_from_remote,
    extract_tag_name,
    first_line,
    make_branch_ref,
    make_tag_ref,
)


class TestExtractLocalBranchFromRemote:
    """Tests for extract_local_branch_from_remote function."""

    def test_extracts_from_origin(self) -> None:
        """Extracts branch name from origin/branch format."""
        assert extract_local_branch_from_remote("origin/main") == "main"
        assert extract_local_branch_from_remote("origin/feature-x") == "feature-x"

    def test_extracts_from_other_remotes(self) -> None:
        """Extracts branch name from other remote formats."""
        assert extract_local_branch_from_remote("upstream/develop") == "develop"
        assert extract_local_branch_from_remote("fork/branch") == "branch"

    def test_handles_nested_slashes(self) -> None:
        """Handles branch names with slashes."""
        assert extract_local_branch_from_remote("origin/feature/sub") == "feature/sub"
        assert extract_local_branch_from_remote("origin/a/b/c") == "a/b/c"

    def test_handles_no_slash(self) -> None:
        """Returns input when no slash present."""
        assert extract_local_branch_from_remote("main") == "main"


class TestExtractTagName:
    """Tests for extract_tag_name function."""

    def test_extracts_tag_name(self) -> None:
        """Extracts tag name from refs/tags/ prefix."""
        assert extract_tag_name("refs/tags/v1.0") == "v1.0"
        assert extract_tag_name("refs/tags/release-2.0") == "release-2.0"

    def test_returns_none_for_non_tag(self) -> None:
        """Returns None for non-tag refs."""
        assert extract_tag_name("refs/heads/main") is None
        assert extract_tag_name("HEAD") is None
        assert extract_tag_name("origin/main") is None

    def test_handles_complex_tag_names(self) -> None:
        """Handles tag names with special characters."""
        assert extract_tag_name("refs/tags/v1.0.0-rc1") == "v1.0.0-rc1"
        assert extract_tag_name("refs/tags/release/v1") == "release/v1"


class TestExtractBranchName:
    """Tests for extract_branch_name function."""

    def test_extracts_branch_name(self) -> None:
        """Extracts branch name from refs/heads/ prefix."""
        assert extract_branch_name("refs/heads/main") == "main"
        assert extract_branch_name("refs/heads/feature-x") == "feature-x"

    def test_returns_none_for_non_branch(self) -> None:
        """Returns None for non-branch refs."""
        assert extract_branch_name("refs/tags/v1.0") is None
        assert extract_branch_name("HEAD") is None
        assert extract_branch_name("origin/main") is None

    def test_handles_nested_branch_names(self) -> None:
        """Handles branch names with slashes."""
        assert extract_branch_name("refs/heads/feature/sub") == "feature/sub"
        assert extract_branch_name("refs/heads/user/feature/name") == "user/feature/name"


class TestFirstLine:
    """Tests for first_line function."""

    def test_single_line(self) -> None:
        """Returns the line for single-line text."""
        assert first_line("hello world") == "hello world"

    def test_multiple_lines(self) -> None:
        """Returns first line from multi-line text."""
        assert first_line("first\nsecond\nthird") == "first"

    def test_empty_string(self) -> None:
        """Returns empty string for empty input."""
        assert first_line("") == ""

    def test_preserves_whitespace(self) -> None:
        """Preserves whitespace in first line."""
        assert first_line("  indented  \nnext") == "  indented  "

    def test_handles_crlf(self) -> None:
        """Handles CRLF line endings."""
        assert first_line("first\r\nsecond") == "first"

    def test_only_newline(self) -> None:
        """Handles text that is just a newline."""
        assert first_line("\n") == ""


class TestMakeTagRef:
    """Tests for make_tag_ref function."""

    def test_creates_tag_ref(self) -> None:
        """Creates full tag ref from name."""
        assert make_tag_ref("v1.0") == "refs/tags/v1.0"
        assert make_tag_ref("release-2.0") == "refs/tags/release-2.0"

    def test_handles_complex_names(self) -> None:
        """Handles tag names with special characters."""
        assert make_tag_ref("v1.0.0-rc1") == "refs/tags/v1.0.0-rc1"


class TestMakeBranchRef:
    """Tests for make_branch_ref function."""

    def test_creates_branch_ref(self) -> None:
        """Creates full branch ref from name."""
        assert make_branch_ref("main") == "refs/heads/main"
        assert make_branch_ref("feature-x") == "refs/heads/feature-x"

    def test_handles_nested_names(self) -> None:
        """Handles branch names with slashes."""
        assert make_branch_ref("feature/sub") == "refs/heads/feature/sub"


class TestRoundTrip:
    """Tests for round-trip consistency."""

    def test_tag_round_trip(self) -> None:
        """make_tag_ref and extract_tag_name are inverses."""
        tag_name = "v1.0.0"
        ref = make_tag_ref(tag_name)
        extracted = extract_tag_name(ref)
        assert extracted == tag_name

    def test_branch_round_trip(self) -> None:
        """make_branch_ref and extract_branch_name are inverses."""
        branch_name = "feature/my-branch"
        ref = make_branch_ref(branch_name)
        extracted = extract_branch_name(ref)
        assert extracted == branch_name
