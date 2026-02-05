"""Tests for language-specific query configurations."""

from __future__ import annotations

import pytest

from codeplane.index._internal.extraction.languages import (
    ALL_LANGUAGE_CONFIGS,
    CPP_CONFIG,
    CSHARP_CONFIG,
    DART_CONFIG,
    ELIXIR_CONFIG,
    GO_CONFIG,
    HASKELL_CONFIG,
    JAVA_CONFIG,
    KOTLIN_CONFIG,
    NIM_CONFIG,
    OCAML_CONFIG,
    PHP_CONFIG,
    PYTHON_CONFIG,
    RUBY_CONFIG,
    RUST_CONFIG,
    SCALA_CONFIG,
    SWIFT_CONFIG,
    TYPESCRIPT_CONFIG,
    ZIG_CONFIG,
    get_config_for_language,
)
from codeplane.index._internal.extraction.query_based import LanguageQueryConfig


class TestAllLanguageConfigs:
    """Tests for ALL_LANGUAGE_CONFIGS registry."""

    def test_is_dict(self) -> None:
        """ALL_LANGUAGE_CONFIGS is a dictionary."""
        assert isinstance(ALL_LANGUAGE_CONFIGS, dict)

    def test_values_are_language_query_configs(self) -> None:
        """All values are LanguageQueryConfig instances."""
        for name, config in ALL_LANGUAGE_CONFIGS.items():
            assert isinstance(config, LanguageQueryConfig), f"{name} has invalid config"

    def test_common_languages_present(self) -> None:
        """Common languages are in registry."""
        expected = {"python", "javascript", "typescript", "go", "rust", "java"}
        assert expected.issubset(set(ALL_LANGUAGE_CONFIGS.keys()))

    def test_aliases_map_to_same_config(self) -> None:
        """Language aliases map to same configs."""
        assert ALL_LANGUAGE_CONFIGS["javascript"] is ALL_LANGUAGE_CONFIGS["typescript"]
        assert ALL_LANGUAGE_CONFIGS["c"] is ALL_LANGUAGE_CONFIGS["cpp"]
        # Split JVM/dotnet languages share similar structure with their primary config
        assert ALL_LANGUAGE_CONFIGS["groovy"] is ALL_LANGUAGE_CONFIGS["java"]
        assert ALL_LANGUAGE_CONFIGS["fsharp"] is ALL_LANGUAGE_CONFIGS["csharp"]
        assert ALL_LANGUAGE_CONFIGS["vbnet"] is ALL_LANGUAGE_CONFIGS["csharp"]


class TestGetConfigForLanguage:
    """Tests for get_config_for_language function."""

    @pytest.mark.parametrize(
        "language,expected",
        [
            ("python", PYTHON_CONFIG),
            ("Python", PYTHON_CONFIG),
            ("PYTHON", PYTHON_CONFIG),
            ("javascript", TYPESCRIPT_CONFIG),
            ("typescript", TYPESCRIPT_CONFIG),
            ("go", GO_CONFIG),
            ("rust", RUST_CONFIG),
            ("java", JAVA_CONFIG),
            ("csharp", CSHARP_CONFIG),
            ("cpp", CPP_CONFIG),
            ("ruby", RUBY_CONFIG),
            ("php", PHP_CONFIG),
            ("kotlin", KOTLIN_CONFIG),
            ("scala", SCALA_CONFIG),
            ("swift", SWIFT_CONFIG),
            ("dart", DART_CONFIG),
            ("elixir", ELIXIR_CONFIG),
            ("haskell", HASKELL_CONFIG),
            ("ocaml", OCAML_CONFIG),
            ("zig", ZIG_CONFIG),
            ("nim", NIM_CONFIG),
        ],
    )
    def test_returns_correct_config(self, language: str, expected: LanguageQueryConfig) -> None:
        """Returns correct config for language."""
        result = get_config_for_language(language)
        assert result is expected

    def test_returns_none_for_unknown(self) -> None:
        """Returns None for unknown languages."""
        assert get_config_for_language("unknown") is None
        assert get_config_for_language("brainfuck") is None

    def test_case_insensitive(self) -> None:
        """Language lookup is case-insensitive."""
        assert get_config_for_language("Python") is get_config_for_language("python")
        assert get_config_for_language("GO") is get_config_for_language("go")


class TestPythonConfig:
    """Tests for PYTHON_CONFIG."""

    def test_language_family(self) -> None:
        """Python config has correct language name."""
        assert PYTHON_CONFIG.language_family == "python"

    def test_grammar_name(self) -> None:
        """Python config has correct grammar name."""
        assert PYTHON_CONFIG.grammar_name == "python"

    def test_scope_node_types(self) -> None:
        """Python config has expected scope node types."""
        assert "function_definition" in PYTHON_CONFIG.scope_node_types
        assert "class_definition" in PYTHON_CONFIG.scope_node_types

    def test_has_type_annotation_query(self) -> None:
        """Python config has type annotation query."""
        assert PYTHON_CONFIG.type_annotation_query
        assert "typed_parameter" in PYTHON_CONFIG.type_annotation_query

    def test_has_type_member_query(self) -> None:
        """Python config has type member query."""
        assert PYTHON_CONFIG.type_member_query
        assert "class_definition" in PYTHON_CONFIG.type_member_query

    def test_has_member_access_query(self) -> None:
        """Python config has member access query."""
        assert PYTHON_CONFIG.member_access_query
        assert "attribute" in PYTHON_CONFIG.member_access_query

    def test_no_interface_support(self) -> None:
        """Python config has no interface support."""
        assert PYTHON_CONFIG.supports_interfaces is False


class TestTypescriptConfig:
    """Tests for TYPESCRIPT_CONFIG."""

    def test_language_family(self) -> None:
        """TypeScript config has correct language name."""
        assert TYPESCRIPT_CONFIG.language_family == "javascript"

    def test_grammar_name(self) -> None:
        """TypeScript config has correct grammar name."""
        assert TYPESCRIPT_CONFIG.grammar_name == "typescript"

    def test_supports_interfaces(self) -> None:
        """TypeScript config supports interfaces."""
        assert TYPESCRIPT_CONFIG.supports_interfaces is True

    def test_has_interface_impl_query(self) -> None:
        """TypeScript config has interface implementation query."""
        assert TYPESCRIPT_CONFIG.interface_impl_query
        assert "implements_clause" in TYPESCRIPT_CONFIG.interface_impl_query

    def test_optional_patterns(self) -> None:
        """TypeScript config has optional patterns."""
        assert "| null" in TYPESCRIPT_CONFIG.optional_patterns
        assert "?" in TYPESCRIPT_CONFIG.optional_patterns

    def test_array_patterns(self) -> None:
        """TypeScript config has array patterns."""
        assert "[]" in TYPESCRIPT_CONFIG.array_patterns
        assert "Array<" in TYPESCRIPT_CONFIG.array_patterns


class TestGoConfig:
    """Tests for GO_CONFIG."""

    def test_language_family(self) -> None:
        """Go config has correct language name."""
        assert GO_CONFIG.language_family == "go"

    def test_grammar_name(self) -> None:
        """Go config has correct grammar name."""
        assert GO_CONFIG.grammar_name == "go"

    def test_reference_indicator(self) -> None:
        """Go config has pointer reference indicator."""
        assert GO_CONFIG.reference_indicator == "*"

    def test_no_interface_impl_query(self) -> None:
        """Go config has empty interface impl query (structural typing)."""
        assert GO_CONFIG.interface_impl_query == ""


class TestRustConfig:
    """Tests for RUST_CONFIG."""

    def test_language_family(self) -> None:
        """Rust config has correct language name."""
        assert RUST_CONFIG.language_family == "rust"

    def test_access_styles(self) -> None:
        """Rust config supports dot and scope access."""
        assert "dot" in RUST_CONFIG.access_styles
        assert "scope" in RUST_CONFIG.access_styles

    def test_optional_patterns(self) -> None:
        """Rust config has Option pattern."""
        assert "Option<" in RUST_CONFIG.optional_patterns

    def test_has_interface_impl_query(self) -> None:
        """Rust config has trait implementation query."""
        assert RUST_CONFIG.interface_impl_query
        assert "impl_item" in RUST_CONFIG.interface_impl_query


class TestJavaConfig:
    """Tests for JAVA_CONFIG."""

    def test_language_family(self) -> None:
        """Java config has correct language name."""
        assert JAVA_CONFIG.language_family == "jvm"

    def test_grammar_name(self) -> None:
        """Java config has correct grammar name."""
        assert JAVA_CONFIG.grammar_name == "java"

    def test_supports_interfaces(self) -> None:
        """Java config supports interfaces."""
        assert JAVA_CONFIG.supports_interfaces is True


class TestRubyConfig:
    """Tests for RUBY_CONFIG."""

    def test_language_family(self) -> None:
        """Ruby config has correct language name."""
        assert RUBY_CONFIG.language_family == "ruby"

    def test_no_type_annotations(self) -> None:
        """Ruby config has no type annotation support."""
        assert RUBY_CONFIG.supports_type_annotations is False

    def test_no_interface_support(self) -> None:
        """Ruby config has no interface support."""
        assert RUBY_CONFIG.supports_interfaces is False

    def test_empty_type_annotation_query(self) -> None:
        """Ruby config has empty type annotation query."""
        assert RUBY_CONFIG.type_annotation_query == ""


class TestCppConfig:
    """Tests for CPP_CONFIG."""

    def test_language_family(self) -> None:
        """C++ config has correct language name."""
        assert CPP_CONFIG.language_family == "cpp"

    def test_access_styles(self) -> None:
        """C++ config supports dot, arrow, and scope access."""
        assert "dot" in CPP_CONFIG.access_styles
        assert "arrow" in CPP_CONFIG.access_styles
        assert "scope" in CPP_CONFIG.access_styles

    def test_reference_indicator(self) -> None:
        """C++ config has reference indicator."""
        assert CPP_CONFIG.reference_indicator == "&"
