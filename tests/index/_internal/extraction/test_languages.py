"""Tests for language pack type extraction configurations."""

from __future__ import annotations

import pytest

from codeplane.index._internal.parsing.packs import (
    PACKS,
    TypeExtractionConfig,
    get_pack,
)

# Convenience aliases — access type_config from each pack
PYTHON_CONFIG = PACKS["python"].type_config
TYPESCRIPT_CONFIG = PACKS["typescript"].type_config
GO_CONFIG = PACKS["go"].type_config
RUST_CONFIG = PACKS["rust"].type_config
JAVA_CONFIG = PACKS["java"].type_config
KOTLIN_CONFIG = PACKS["kotlin"].type_config
SCALA_CONFIG = PACKS["scala"].type_config
CSHARP_CONFIG = PACKS["csharp"].type_config
CPP_CONFIG = PACKS["cpp"].type_config
RUBY_CONFIG = PACKS["ruby"].type_config
PHP_CONFIG = PACKS["php"].type_config
SWIFT_CONFIG = PACKS["swift"].type_config
ELIXIR_CONFIG = PACKS["elixir"].type_config
HASKELL_CONFIG = PACKS["haskell"].type_config
OCAML_CONFIG = PACKS["ocaml"].type_config
ZIG_CONFIG = PACKS["zig"].type_config


class TestPackTypeConfigs:
    """Tests for PACKS type config registry."""

    def test_packs_is_dict(self) -> None:
        """PACKS is a dictionary."""
        assert isinstance(PACKS, dict)

    def test_packs_with_type_config_have_valid_configs(self) -> None:
        """All packs with type_config have TypeExtractionConfig instances."""
        for name, pack in PACKS.items():
            if pack.type_config is not None:
                assert isinstance(pack.type_config, TypeExtractionConfig), (
                    f"{name} has invalid type_config"
                )

    def test_common_languages_present(self) -> None:
        """Common languages are in PACKS with type configs."""
        expected = {"python", "javascript", "typescript", "go", "rust", "java"}
        for lang in expected:
            pack = get_pack(lang)
            assert pack is not None, f"{lang} not in PACKS"
            assert pack.type_config is not None, f"{lang} has no type_config"

    def test_aliases_map_to_same_config(self) -> None:
        """Language aliases map to same type configs."""
        assert PACKS["javascript"].type_config is PACKS["typescript"].type_config
        assert PACKS["c"].type_config is PACKS["cpp"].type_config


class TestGetPackTypeConfig:
    """Tests for get_pack type config lookup."""

    @pytest.mark.parametrize(
        "language,expected",
        [
            ("python", PYTHON_CONFIG),
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
            ("elixir", ELIXIR_CONFIG),
            ("haskell", HASKELL_CONFIG),
            ("ocaml", OCAML_CONFIG),
            ("zig", ZIG_CONFIG),
        ],
    )
    def test_returns_correct_config(
        self, language: str, expected: TypeExtractionConfig | None
    ) -> None:
        """Returns correct type config for language."""
        pack = get_pack(language)
        assert pack is not None
        assert pack.type_config is expected

    def test_returns_none_for_unknown(self) -> None:
        """Returns None for unknown languages."""
        assert get_pack("unknown") is None
        assert get_pack("brainfuck") is None


class TestPythonConfig:
    """Tests for PYTHON_CONFIG."""

    def test_language_family(self) -> None:
        """Python config has correct language name."""
        assert PYTHON_CONFIG.language_family == "python"

    def test_grammar_name(self) -> None:
        """Python pack has correct grammar name."""
        assert PACKS["python"].grammar_name == "python"

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
        """TypeScript pack has correct grammar name."""
        assert PACKS["typescript"].grammar_name == "typescript"

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
        """Go pack has correct grammar name."""
        assert PACKS["go"].grammar_name == "go"

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
        """Java pack has correct grammar name."""
        assert PACKS["java"].grammar_name == "java"

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
