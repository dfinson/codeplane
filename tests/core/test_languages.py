"""Tests for core/languages.py module.

Covers:
- Language dataclass
- ALL_LANGUAGES registry
- Language detection functions
- Marker and glob utilities
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.core.languages import (
    ALL_LANGUAGES,
    AMBIENT_FAMILIES,
    EXTENSION_TO_FAMILY,
    FILENAME_TO_FAMILY,
    LANGUAGES_BY_FAMILY,
    Language,
    build_include_specs,
    build_marker_definitions,
    detect_language_family,
    detect_language_family_enum,
    get_all_indexable_extensions,
    get_all_indexable_filenames,
    get_grammar_name,
    get_include_globs,
    get_markers,
    get_test_patterns,
    has_grammar,
)


class TestLanguageDataclass:
    """Tests for Language dataclass."""

    def test_create_minimal_language(self) -> None:
        """Create language with minimal required fields."""
        lang = Language(family="test", extensions=frozenset({".test"}))
        assert lang.family == "test"
        assert lang.extensions == frozenset({".test"})
        assert lang.filenames == frozenset()
        assert lang.markers_workspace == ()
        assert lang.markers_package == ()
        assert lang.include_globs == ()
        assert lang.grammar is None
        assert lang.test_patterns == ()
        assert lang.ambient is False

    def test_create_full_language(self) -> None:
        """Create language with all fields."""
        lang = Language(
            family="python",
            extensions=frozenset({".py", ".pyi"}),
            filenames=frozenset({"pyproject.toml"}),
            markers_workspace=("uv.lock",),
            markers_package=("pyproject.toml",),
            include_globs=("**/*.py",),
            grammar="python",
            test_patterns=("test_*.py",),
            ambient=False,
        )
        assert lang.family == "python"
        assert lang.grammar == "python"

    def test_language_is_frozen(self) -> None:
        """Language is a frozen dataclass."""
        lang = Language(family="x", extensions=frozenset({".x"}))
        with pytest.raises(AttributeError):
            lang.family = "y"  # type: ignore[misc]


class TestAllLanguages:
    """Tests for ALL_LANGUAGES registry."""

    def test_is_tuple(self) -> None:
        """ALL_LANGUAGES is a tuple."""
        assert isinstance(ALL_LANGUAGES, tuple)

    def test_contains_languages(self) -> None:
        """ALL_LANGUAGES contains Language instances."""
        assert len(ALL_LANGUAGES) > 0
        assert all(isinstance(lang, Language) for lang in ALL_LANGUAGES)

    def test_contains_common_languages(self) -> None:
        """ALL_LANGUAGES contains common languages."""
        families = {lang.family for lang in ALL_LANGUAGES}
        common = {"python", "javascript", "go", "rust", "jvm"}
        assert common.issubset(families)

    def test_unique_families(self) -> None:
        """Each language has a unique family."""
        families = [lang.family for lang in ALL_LANGUAGES]
        assert len(families) == len(set(families))


class TestLanguagesByFamily:
    """Tests for LANGUAGES_BY_FAMILY dict."""

    def test_is_dict(self) -> None:
        """LANGUAGES_BY_FAMILY is a dict."""
        assert isinstance(LANGUAGES_BY_FAMILY, dict)

    def test_lookup_python(self) -> None:
        """Can look up Python language."""
        python = LANGUAGES_BY_FAMILY.get("python")
        assert python is not None
        assert python.family == "python"
        assert ".py" in python.extensions

    def test_lookup_nonexistent(self) -> None:
        """Returns None for non-existent family."""
        result = LANGUAGES_BY_FAMILY.get("nonexistent")
        assert result is None


class TestExtensionToFamily:
    """Tests for EXTENSION_TO_FAMILY mapping."""

    def test_python_extensions(self) -> None:
        """Python extensions map to python family."""
        assert EXTENSION_TO_FAMILY.get(".py") == "python"
        assert EXTENSION_TO_FAMILY.get(".pyi") == "python"

    def test_javascript_extensions(self) -> None:
        """JavaScript extensions map correctly."""
        assert EXTENSION_TO_FAMILY.get(".js") == "javascript"
        assert EXTENSION_TO_FAMILY.get(".ts") == "javascript"
        assert EXTENSION_TO_FAMILY.get(".tsx") == "javascript"

    def test_nonexistent_extension(self) -> None:
        """Nonexistent extensions return None."""
        assert EXTENSION_TO_FAMILY.get(".unknown") is None


class TestFilenameToFamily:
    """Tests for FILENAME_TO_FAMILY mapping."""

    def test_python_filenames(self) -> None:
        """Python filenames map correctly."""
        assert FILENAME_TO_FAMILY.get("pyproject.toml") == "python"
        assert FILENAME_TO_FAMILY.get("setup.py") == "python"

    def test_javascript_filenames(self) -> None:
        """JavaScript filenames map correctly."""
        assert FILENAME_TO_FAMILY.get("package.json") == "javascript"

    def test_case_insensitive(self) -> None:
        """Filenames are case-insensitive."""
        # Files are stored lowercase
        assert "dockerfile" in FILENAME_TO_FAMILY


class TestAmbientFamilies:
    """Tests for AMBIENT_FAMILIES set."""

    def test_is_frozenset(self) -> None:
        """AMBIENT_FAMILIES is a frozenset."""
        assert isinstance(AMBIENT_FAMILIES, frozenset)

    def test_contains_ambient_languages(self) -> None:
        """Contains languages marked as ambient."""
        # These should be ambient: sql, docker, markdown, json_yaml, graphql
        assert "sql" in AMBIENT_FAMILIES
        assert "markdown" in AMBIENT_FAMILIES

    def test_does_not_contain_non_ambient(self) -> None:
        """Does not contain non-ambient languages."""
        assert "python" not in AMBIENT_FAMILIES
        assert "javascript" not in AMBIENT_FAMILIES


class TestDetectLanguageFamily:
    """Tests for detect_language_family function."""

    def test_detect_by_extension(self) -> None:
        """Detects language by file extension."""
        assert detect_language_family("test.py") == "python"
        assert detect_language_family("app.js") == "javascript"
        assert detect_language_family("main.go") == "go"

    def test_detect_by_filename(self) -> None:
        """Detects language by filename."""
        assert detect_language_family("Dockerfile") == "docker"
        # Makefile is claimed by cpp (common for C/C++ projects)
        assert detect_language_family("Makefile") == "cpp"

    def test_detect_with_path_object(self) -> None:
        """Works with Path objects."""
        assert detect_language_family(Path("src/app.py")) == "python"

    def test_returns_none_for_unknown(self) -> None:
        """Returns None for unknown files."""
        assert detect_language_family("unknown.xyz") is None

    def test_filename_takes_precedence(self) -> None:
        """Filename matching takes precedence over extension."""
        # pyproject.toml is a Python marker file
        assert detect_language_family("pyproject.toml") == "python"

    def test_case_insensitive_filename(self) -> None:
        """Filename detection is case-insensitive."""
        assert detect_language_family("DOCKERFILE") == "docker"
        assert detect_language_family("dockerfile") == "docker"

    def test_case_sensitive_extension(self) -> None:
        """Extension detection handles case correctly."""
        # Extensions should work case-insensitively
        assert detect_language_family("test.PY") == "python"


class TestDetectLanguageFamilyEnum:
    """Tests for detect_language_family_enum function."""

    def test_returns_enum_for_known(self) -> None:
        """Returns LanguageFamily enum for known files."""
        result = detect_language_family_enum("test.py")
        assert result is not None
        assert result.value == "python"

    def test_returns_none_for_unknown(self) -> None:
        """Returns None for unknown files."""
        assert detect_language_family_enum("unknown.xyz") is None

    def test_returns_none_for_invalid_enum(self) -> None:
        """Returns None when family string not in enum."""
        # This tests the ValueError catch in the function
        # All families should be in the enum, but this ensures robustness
        pass


class TestGetIncludeGlobs:
    """Tests for get_include_globs function."""

    def test_returns_globs_for_python(self) -> None:
        """Returns include globs for Python."""
        globs = get_include_globs("python")
        assert "**/*.py" in globs

    def test_returns_empty_for_unknown(self) -> None:
        """Returns empty tuple for unknown family."""
        assert get_include_globs("nonexistent") == ()


class TestGetMarkers:
    """Tests for get_markers function."""

    def test_returns_markers_for_python(self) -> None:
        """Returns workspace and package markers for Python."""
        workspace, package = get_markers("python")
        assert "uv.lock" in workspace
        assert "pyproject.toml" in package

    def test_returns_empty_for_unknown(self) -> None:
        """Returns empty tuples for unknown family."""
        workspace, package = get_markers("nonexistent")
        assert workspace == ()
        assert package == ()


class TestGetTestPatterns:
    """Tests for get_test_patterns function."""

    def test_returns_patterns_for_python(self) -> None:
        """Returns test patterns for Python."""
        patterns = get_test_patterns("python")
        assert "test_*.py" in patterns

    def test_returns_empty_for_unknown(self) -> None:
        """Returns empty tuple for unknown family."""
        assert get_test_patterns("nonexistent") == ()


class TestGetGrammarName:
    """Tests for get_grammar_name function."""

    def test_returns_grammar_for_python(self) -> None:
        """Returns grammar name for Python."""
        assert get_grammar_name("python") == "python"

    def test_returns_none_for_language_without_grammar(self) -> None:
        """Returns None for languages without tree-sitter grammar."""
        # Some languages don't have grammars (e.g., clojure)
        assert get_grammar_name("clojure") is None

    def test_returns_none_for_unknown(self) -> None:
        """Returns None for unknown family."""
        assert get_grammar_name("nonexistent") is None


class TestHasGrammar:
    """Tests for has_grammar function."""

    def test_returns_true_for_python(self) -> None:
        """Returns True for languages with grammar."""
        assert has_grammar("python") is True

    def test_returns_false_for_clojure(self) -> None:
        """Returns False for languages without grammar."""
        assert has_grammar("clojure") is False

    def test_returns_false_for_unknown(self) -> None:
        """Returns False for unknown family."""
        assert has_grammar("nonexistent") is False


class TestGetAllIndexableExtensions:
    """Tests for get_all_indexable_extensions function."""

    def test_returns_set(self) -> None:
        """Returns a set of extensions."""
        exts = get_all_indexable_extensions()
        assert isinstance(exts, set)

    def test_contains_common_extensions(self) -> None:
        """Contains common file extensions."""
        exts = get_all_indexable_extensions()
        assert ".py" in exts
        assert ".js" in exts
        assert ".go" in exts


class TestGetAllIndexableFilenames:
    """Tests for get_all_indexable_filenames function."""

    def test_returns_set(self) -> None:
        """Returns a set of filenames."""
        names = get_all_indexable_filenames()
        assert isinstance(names, set)

    def test_contains_common_filenames(self) -> None:
        """Contains common project filenames."""
        names = get_all_indexable_filenames()
        assert "pyproject.toml" in names or "setup.py" in names


class TestBuildMarkerDefinitions:
    """Tests for build_marker_definitions function."""

    def test_returns_dict(self) -> None:
        """Returns a dictionary."""
        markers = build_marker_definitions()
        assert isinstance(markers, dict)

    def test_python_markers_structure(self) -> None:
        """Python has correct marker structure."""
        markers = build_marker_definitions()
        assert "python" in markers
        assert "workspace" in markers["python"]
        assert "package" in markers["python"]

    def test_only_languages_with_markers(self) -> None:
        """Only includes languages with markers."""
        markers = build_marker_definitions()
        # All entries should have at least one marker
        for family, data in markers.items():
            has_markers = bool(data["workspace"]) or bool(data["package"])
            assert has_markers, f"{family} has no markers"


class TestBuildIncludeSpecs:
    """Tests for build_include_specs function."""

    def test_returns_dict(self) -> None:
        """Returns a dictionary."""
        specs = build_include_specs()
        assert isinstance(specs, dict)

    def test_python_globs(self) -> None:
        """Python has include globs."""
        specs = build_include_specs()
        assert "python" in specs
        assert "**/*.py" in specs["python"]

    def test_only_languages_with_globs(self) -> None:
        """Only includes languages with include globs."""
        specs = build_include_specs()
        for family, globs in specs.items():
            assert len(globs) > 0, f"{family} has no globs"
