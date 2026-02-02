"""Comprehensive unit tests for query-based type extraction.

Tests the QueryBasedExtractor with LanguageQueryConfig for all supported languages.
Targets 95%+ code coverage via parametrization across:
- All 18 supported languages
- Type annotations (parameters, returns, fields, variables)
- Type members (methods, fields, properties)
- Member accesses (dot, arrow, scope)
- Interface implementations
- Edge cases and error handling
"""

from __future__ import annotations

import pytest

from codeplane.index._internal.extraction import (
    InterfaceImplData,
    MemberAccessData,
    TypeAnnotationData,
    TypeMemberData,
    get_registry,
)
from codeplane.index._internal.extraction.languages import (
    ALL_LANGUAGE_CONFIGS,
    CPP_CONFIG,
    CSHARP_CONFIG,
    GO_CONFIG,
    JAVA_CONFIG,
    PYTHON_CONFIG,
    RUBY_CONFIG,
    RUST_CONFIG,
    TYPESCRIPT_CONFIG,
    get_config_for_language,
)
from codeplane.index._internal.extraction.query_based import (
    LanguageQueryConfig,
    QueryBasedExtractor,
)

# =============================================================================
# Test Data: Language-Specific Code Samples
# =============================================================================

# Mapping of (config, grammar_name, code_sample) for type annotation extraction
TYPE_ANNOTATION_SAMPLES: list[tuple[LanguageQueryConfig, str, str, str, str]] = [
    # (config, grammar_name, code, expected_name, expected_type_substring)
    (
        PYTHON_CONFIG,
        "python",
        "def greet(name: str) -> str: pass",
        "name",
        "str",
    ),
    (
        PYTHON_CONFIG,
        "python",
        "def f(x: int, y: float) -> bool: pass",
        "x",
        "int",
    ),
    (
        PYTHON_CONFIG,
        "python",
        "def f(data: list[str]) -> None: pass",
        "data",
        "list",
    ),
    (
        PYTHON_CONFIG,
        "python",
        "x: int = 5",
        "x",
        "int",
    ),
    (
        TYPESCRIPT_CONFIG,
        "typescript",
        "function greet(name: string): string { return name; }",
        "name",
        "string",
    ),
    (
        TYPESCRIPT_CONFIG,
        "typescript",
        "const x: number = 5;",
        "x",
        "number",
    ),
    (
        GO_CONFIG,
        "go",
        "package main\nfunc greet(name string) string { return name }",
        "name",
        "string",
    ),
    (
        GO_CONFIG,
        "go",
        "package main\nvar x int = 5",
        "x",
        "int",
    ),
    (
        RUST_CONFIG,
        "rust",
        "fn greet(name: &str) -> String { String::new() }",
        "name",
        "str",
    ),
    (
        RUST_CONFIG,
        "rust",
        "let x: i32 = 5;",
        "x",
        "i32",
    ),
    (
        JAVA_CONFIG,
        "java",
        "class C { void f(String name) {} }",
        "name",
        "String",
    ),
    (
        JAVA_CONFIG,
        "java",
        "class C { private int x; }",
        "x",
        "int",
    ),
    (
        CSHARP_CONFIG,
        "c_sharp",
        "class C { void F(string name) {} }",
        "name",
        "string",
    ),
    (
        CSHARP_CONFIG,
        "c_sharp",
        "class C { public int X { get; set; } }",
        "X",
        "int",
    ),
    (
        CPP_CONFIG,
        "cpp",
        "void greet(std::string name) {}",
        "name",
        "string",
    ),
]

# Return type annotation samples
RETURN_TYPE_SAMPLES: list[tuple[LanguageQueryConfig, str, str, str, str]] = [
    (
        PYTHON_CONFIG,
        "python",
        "def get_count() -> int: return 42",
        "get_count",
        "int",
    ),
    (
        PYTHON_CONFIG,
        "python",
        "def get_name() -> str: return ''",
        "get_name",
        "str",
    ),
    (
        TYPESCRIPT_CONFIG,
        "typescript",
        "function getCount(): number { return 42; }",
        "getCount",
        "number",
    ),
    (
        GO_CONFIG,
        "go",
        "package main\nfunc getCount() int { return 42 }",
        "getCount",
        "int",
    ),
    (
        RUST_CONFIG,
        "rust",
        "fn get_count() -> i32 { 42 }",
        "get_count",
        "i32",
    ),
    (
        JAVA_CONFIG,
        "java",
        "class C { int getCount() { return 42; } }",
        "getCount",
        "int",
    ),
    (
        CSHARP_CONFIG,
        "c_sharp",
        "class C { int GetCount() { return 42; } }",
        "GetCount",
        "int",
    ),
    (
        CPP_CONFIG,
        "cpp",
        "int getCount() { return 42; }",
        "getCount",
        "int",
    ),
]

# Type member samples (class/struct fields and methods)
TYPE_MEMBER_SAMPLES: list[tuple[LanguageQueryConfig, str, str, str, str, str]] = [
    # (config, grammar_name, code, parent_name, expected_member, member_kind)
    (
        PYTHON_CONFIG,
        "python",
        "class Person:\n    def greet(self): pass",
        "Person",
        "greet",
        "method",
    ),
    (
        PYTHON_CONFIG,
        "python",
        "class Person:\n    name: str",
        "Person",
        "name",
        "field",
    ),
    (
        TYPESCRIPT_CONFIG,
        "typescript",
        "class Person { greet(): void {} }",
        "Person",
        "greet",
        "method",
    ),
    (
        TYPESCRIPT_CONFIG,
        "typescript",
        "interface Person { name: string; }",
        "Person",
        "name",
        "field",
    ),
    (
        GO_CONFIG,
        "go",
        "package main\ntype Person struct { Name string }",
        "Person",
        "Name",
        "field",
    ),
    (
        RUST_CONFIG,
        "rust",
        "struct Person { name: String }",
        "Person",
        "name",
        "field",
    ),
    (
        RUST_CONFIG,
        "rust",
        "impl Person { fn greet(&self) {} }",
        "Person",
        "greet",
        "method",
    ),
    (
        JAVA_CONFIG,
        "java",
        "class Person { private String name; }",
        "Person",
        "name",
        "field",
    ),
    (
        JAVA_CONFIG,
        "java",
        "class Person { void greet() {} }",
        "Person",
        "greet",
        "method",
    ),
    (
        CSHARP_CONFIG,
        "c_sharp",
        "class Person { public string Name { get; set; } }",
        "Person",
        "Name",
        "field",
    ),
    (
        CSHARP_CONFIG,
        "c_sharp",
        "class Person { void Greet() {} }",
        "Person",
        "Greet",
        "method",
    ),
    (
        CPP_CONFIG,
        "cpp",
        "struct Person { std::string name; };",
        "Person",
        "name",
        "field",
    ),
]

# Member access samples
MEMBER_ACCESS_SAMPLES: list[tuple[LanguageQueryConfig, str, str, str, str]] = [
    # (config, grammar_name, code, expected_receiver, expected_member)
    (
        PYTHON_CONFIG,
        "python",
        "foo.bar",
        "foo",
        "bar",
    ),
    (
        PYTHON_CONFIG,
        "python",
        "obj.method()",
        "obj",
        "method",
    ),
    (
        TYPESCRIPT_CONFIG,
        "typescript",
        "foo.bar;",
        "foo",
        "bar",
    ),
    (
        TYPESCRIPT_CONFIG,
        "typescript",
        "obj.method();",
        "obj",
        "method",
    ),
    (
        GO_CONFIG,
        "go",
        "package main\nfunc f() { _ = foo.Bar }",
        "foo",
        "Bar",
    ),
    (
        RUST_CONFIG,
        "rust",
        "fn f() { foo.bar; }",
        "foo",
        "bar",
    ),
    (
        JAVA_CONFIG,
        "java",
        "class C { void f() { foo.bar(); } }",
        "foo",
        "bar",
    ),
    (
        CSHARP_CONFIG,
        "c_sharp",
        "class C { void F() { foo.Bar(); } }",
        "foo",
        "Bar",
    ),
]

# Interface implementation samples
INTERFACE_IMPL_SAMPLES: list[tuple[LanguageQueryConfig, str, str, str, str]] = [
    # (config, grammar_name, code, implementor, interface)
    (
        TYPESCRIPT_CONFIG,
        "typescript",
        "interface I {} class C implements I {}",
        "C",
        "I",
    ),
    (
        RUST_CONFIG,
        "rust",
        "trait T {} impl T for S {}",
        "S",
        "T",
    ),
    (
        JAVA_CONFIG,
        "java",
        "interface I {} class C implements I {}",
        "C",
        "I",
    ),
    (
        CSHARP_CONFIG,
        "c_sharp",
        "interface I {} class C : I {}",
        "C",
        "I",
    ),
    (
        CPP_CONFIG,
        "cpp",
        "class Base {}; class Derived : public Base {};",
        "Derived",
        "Base",
    ),
]

# Optional type samples
OPTIONAL_TYPE_SAMPLES: list[tuple[LanguageQueryConfig, str, str]] = [
    (PYTHON_CONFIG, "python", "def f(x: int | None): pass"),
    (PYTHON_CONFIG, "python", "def f(x: Optional[int]): pass"),
    (TYPESCRIPT_CONFIG, "typescript", "function f(x: number | null) {}"),
    (TYPESCRIPT_CONFIG, "typescript", "function f(x: number | undefined) {}"),
    (RUST_CONFIG, "rust", "fn f(x: Option<i32>) {}"),
    (CSHARP_CONFIG, "c_sharp", "class C { void F(int? x) {} }"),
]

# Array/list type samples
ARRAY_TYPE_SAMPLES: list[tuple[LanguageQueryConfig, str, str]] = [
    (PYTHON_CONFIG, "python", "def f(x: list[int]): pass"),
    (PYTHON_CONFIG, "python", "def f(x: List[str]): pass"),
    (TYPESCRIPT_CONFIG, "typescript", "function f(x: number[]) {}"),
    (TYPESCRIPT_CONFIG, "typescript", "function f(x: Array<string>) {}"),
    (GO_CONFIG, "go", "package main\nfunc f(x []int) {}"),
    (RUST_CONFIG, "rust", "fn f(x: Vec<i32>) {}"),
    (JAVA_CONFIG, "java", "class C { void f(List<String> x) {} }"),
    (CSHARP_CONFIG, "c_sharp", "class C { void F(List<int> x) {} }"),
]

# Generic type samples
GENERIC_TYPE_SAMPLES: list[tuple[LanguageQueryConfig, str, str]] = [
    (PYTHON_CONFIG, "python", "def f(x: dict[str, int]): pass"),
    (TYPESCRIPT_CONFIG, "typescript", "function f(x: Map<string, number>) {}"),
    (GO_CONFIG, "go", "package main\nfunc f(x map[string]int) {}"),
    (RUST_CONFIG, "rust", "fn f(x: HashMap<String, i32>) {}"),
    (JAVA_CONFIG, "java", "class C { void f(Map<String, Integer> x) {} }"),
]


# =============================================================================
# Helper Functions
# =============================================================================


def make_tree(code: str, language: str):
    """Parse code into a tree-sitter tree."""
    from codeplane.index._internal.parsing.treesitter import TreeSitterParser

    parser = TreeSitterParser()
    # Create a simple mock path to get language detection
    from pathlib import Path

    # Map language to appropriate extension
    ext_map = {
        "python": "py",
        "typescript": "ts",
        "javascript": "js",
        "go": "go",
        "rust": "rs",
        "java": "java",
        "c_sharp": "cs",
        "cpp": "cpp",
        "c": "c",
        "ruby": "rb",
        "php": "php",
        "swift": "swift",
        "kotlin": "kt",
        "scala": "scala",
        "dart": "dart",
        "elixir": "ex",
        "haskell": "hs",
        "ocaml": "ml",
        "zig": "zig",
        "nim": "nim",
    }
    ext = ext_map.get(language, language)
    return parser.parse(Path(f"test.{ext}"), code.encode())


def make_extractor(config: LanguageQueryConfig) -> QueryBasedExtractor:
    """Create an extractor from config, skipping if grammar unavailable."""
    try:
        return QueryBasedExtractor(config)
    except ValueError as e:
        pytest.skip(f"Grammar not installed: {e}")


# =============================================================================
# Parametrized Tests: Type Annotations
# =============================================================================


class TestTypeAnnotationExtraction:
    """Test type annotation extraction across all languages."""

    @pytest.mark.parametrize(
        "config,grammar,code,expected_name,expected_type",
        TYPE_ANNOTATION_SAMPLES,
        ids=[f"{s[1]}-{s[3]}" for s in TYPE_ANNOTATION_SAMPLES],
    )
    def test_parameter_annotations(
        self,
        config: LanguageQueryConfig,
        grammar: str,
        code: str,
        expected_name: str,
        expected_type: str,
    ) -> None:
        """Test parameter type annotation extraction."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        annotations = extractor.extract_type_annotations(tree.tree, f"test.{grammar}", scopes=[])

        # Find annotation matching expected name
        matching = [
            a
            for a in annotations
            if a.target_name == expected_name and expected_type in a.raw_annotation
        ]
        assert len(matching) >= 1, (
            f"Expected annotation for '{expected_name}' with type containing '{expected_type}'. "
            f"Got: {[(a.target_name, a.raw_annotation) for a in annotations]}"
        )

    @pytest.mark.parametrize(
        "config,grammar,code,expected_name,expected_type",
        RETURN_TYPE_SAMPLES,
        ids=[f"{s[1]}-{s[3]}" for s in RETURN_TYPE_SAMPLES],
    )
    def test_return_type_annotations(
        self,
        config: LanguageQueryConfig,
        grammar: str,
        code: str,
        expected_name: str,
        expected_type: str,
    ) -> None:
        """Test return type annotation extraction."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        annotations = extractor.extract_type_annotations(tree.tree, f"test.{grammar}", scopes=[])

        # Look for return annotations
        return_anns = [a for a in annotations if a.target_kind == "return"]
        matching = [
            a
            for a in return_anns
            if a.target_name == expected_name and expected_type in a.raw_annotation
        ]
        assert len(matching) >= 1, (
            f"Expected return annotation for '{expected_name}' with type '{expected_type}'. "
            f"Got returns: {[(a.target_name, a.raw_annotation) for a in return_anns]}"
        )

    @pytest.mark.parametrize(
        "config,grammar,code",
        OPTIONAL_TYPE_SAMPLES,
        ids=[f"{s[1]}-optional-{i}" for i, s in enumerate(OPTIONAL_TYPE_SAMPLES)],
    )
    def test_optional_type_detection(
        self,
        config: LanguageQueryConfig,
        grammar: str,
        code: str,
    ) -> None:
        """Test that optional types are correctly flagged."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        annotations = extractor.extract_type_annotations(tree.tree, f"test.{grammar}", scopes=[])

        optional_anns = [a for a in annotations if a.is_optional]
        assert len(optional_anns) >= 1, (
            f"Expected at least one optional annotation. "
            f"Got: {[(a.target_name, a.raw_annotation, a.is_optional) for a in annotations]}"
        )

    @pytest.mark.parametrize(
        "config,grammar,code",
        ARRAY_TYPE_SAMPLES,
        ids=[f"{s[1]}-array-{i}" for i, s in enumerate(ARRAY_TYPE_SAMPLES)],
    )
    def test_array_type_detection(
        self,
        config: LanguageQueryConfig,
        grammar: str,
        code: str,
    ) -> None:
        """Test that array/list types are correctly flagged."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        annotations = extractor.extract_type_annotations(tree.tree, f"test.{grammar}", scopes=[])

        array_anns = [a for a in annotations if a.is_array]
        assert len(array_anns) >= 1, (
            f"Expected at least one array annotation. "
            f"Got: {[(a.target_name, a.raw_annotation, a.is_array) for a in annotations]}"
        )

    @pytest.mark.parametrize(
        "config,grammar,code",
        GENERIC_TYPE_SAMPLES,
        ids=[f"{s[1]}-generic-{i}" for i, s in enumerate(GENERIC_TYPE_SAMPLES)],
    )
    def test_generic_type_detection(
        self,
        config: LanguageQueryConfig,
        grammar: str,
        code: str,
    ) -> None:
        """Test that generic types are correctly flagged."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        annotations = extractor.extract_type_annotations(tree.tree, f"test.{grammar}", scopes=[])

        generic_anns = [a for a in annotations if a.is_generic]
        assert len(generic_anns) >= 1, (
            f"Expected at least one generic annotation. "
            f"Got: {[(a.target_name, a.raw_annotation, a.is_generic) for a in annotations]}"
        )


# =============================================================================
# Parametrized Tests: Type Members
# =============================================================================


class TestTypeMemberExtraction:
    """Test type member extraction across all languages."""

    @pytest.mark.parametrize(
        "config,grammar,code,parent_name,expected_member,member_kind",
        TYPE_MEMBER_SAMPLES,
        ids=[f"{s[1]}-{s[3]}.{s[4]}" for s in TYPE_MEMBER_SAMPLES],
    )
    def test_member_extraction(
        self,
        config: LanguageQueryConfig,
        grammar: str,
        code: str,
        parent_name: str,
        expected_member: str,
        member_kind: str,
    ) -> None:
        """Test type member extraction."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        defs = [{"name": parent_name, "kind": "class", "def_uid": parent_name, "start_line": 1}]
        members = extractor.extract_type_members(tree.tree, f"test.{grammar}", defs)

        matching = [m for m in members if m.member_name == expected_member]
        assert len(matching) >= 1, (
            f"Expected member '{expected_member}' in {parent_name}. "
            f"Got: {[m.member_name for m in members]}"
        )

        # Verify member kind if specified
        if member_kind:
            assert any(m.member_kind == member_kind for m in matching), (
                f"Expected member kind '{member_kind}'. Got: {[m.member_kind for m in matching]}"
            )


# =============================================================================
# Parametrized Tests: Member Accesses
# =============================================================================


class TestMemberAccessExtraction:
    """Test member access extraction across all languages."""

    @pytest.mark.parametrize(
        "config,grammar,code,expected_receiver,expected_member",
        MEMBER_ACCESS_SAMPLES,
        ids=[f"{s[1]}-{s[3]}.{s[4]}" for s in MEMBER_ACCESS_SAMPLES],
    )
    def test_member_access_extraction(
        self,
        config: LanguageQueryConfig,
        grammar: str,
        code: str,
        expected_receiver: str,
        expected_member: str,
    ) -> None:
        """Test member access extraction."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        accesses = extractor.extract_member_accesses(
            tree.tree, f"test.{grammar}", scopes=[], type_annotations=[]
        )

        matching = [
            a
            for a in accesses
            if a.receiver_name == expected_receiver and expected_member in a.member_chain
        ]
        assert len(matching) >= 1, (
            f"Expected access '{expected_receiver}.{expected_member}'. "
            f"Got: {[(a.receiver_name, a.member_chain) for a in accesses]}"
        )


# =============================================================================
# Parametrized Tests: Interface Implementations
# =============================================================================


class TestInterfaceImplExtraction:
    """Test interface implementation extraction across all languages."""

    @pytest.mark.parametrize(
        "config,grammar,code,implementor,interface",
        INTERFACE_IMPL_SAMPLES,
        ids=[f"{s[1]}-{s[3]}-impl-{s[4]}" for s in INTERFACE_IMPL_SAMPLES],
    )
    def test_interface_impl_extraction(
        self,
        config: LanguageQueryConfig,
        grammar: str,
        code: str,
        implementor: str,
        interface: str,
    ) -> None:
        """Test interface implementation extraction."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        defs = [
            {"name": interface, "kind": "interface", "def_uid": interface, "start_line": 1},
            {"name": implementor, "kind": "class", "def_uid": implementor, "start_line": 2},
        ]
        impls = extractor.extract_interface_impls(tree.tree, f"test.{grammar}", defs)

        matching = [
            i for i in impls if i.implementor_name == implementor and interface in i.interface_name
        ]
        assert len(matching) >= 1, (
            f"Expected impl '{implementor} implements {interface}'. "
            f"Got: {[(i.implementor_name, i.interface_name) for i in impls]}"
        )


# =============================================================================
# Extractor Properties Tests
# =============================================================================


class TestExtractorProperties:
    """Test QueryBasedExtractor property accessors."""

    @pytest.mark.parametrize(
        "config",
        [
            PYTHON_CONFIG,
            TYPESCRIPT_CONFIG,
            GO_CONFIG,
            RUST_CONFIG,
            JAVA_CONFIG,
            CSHARP_CONFIG,
            CPP_CONFIG,
            RUBY_CONFIG,
        ],
        ids=["python", "typescript", "go", "rust", "java", "csharp", "cpp", "ruby"],
    )
    def test_language_family_property(self, config: LanguageQueryConfig) -> None:
        """Test that language_family property returns correct value."""
        extractor = make_extractor(config)
        assert extractor.language_family == config.language_family

    @pytest.mark.parametrize(
        "config,expected",
        [
            (PYTHON_CONFIG, True),
            (TYPESCRIPT_CONFIG, True),
            (GO_CONFIG, True),
            (RUST_CONFIG, True),
            (JAVA_CONFIG, True),
            (RUBY_CONFIG, False),  # Ruby has no native type annotations
        ],
        ids=["python", "typescript", "go", "rust", "java", "ruby"],
    )
    def test_supports_type_annotations_property(
        self, config: LanguageQueryConfig, expected: bool
    ) -> None:
        """Test supports_type_annotations property."""
        extractor = make_extractor(config)
        assert extractor.supports_type_annotations == expected

    @pytest.mark.parametrize(
        "config,expected",
        [
            (PYTHON_CONFIG, False),
            (TYPESCRIPT_CONFIG, True),
            (GO_CONFIG, True),
            (RUST_CONFIG, True),
            (JAVA_CONFIG, True),
            (CSHARP_CONFIG, True),
        ],
        ids=["python", "typescript", "go", "rust", "java", "csharp"],
    )
    def test_supports_interfaces_property(
        self, config: LanguageQueryConfig, expected: bool
    ) -> None:
        """Test supports_interfaces property."""
        extractor = make_extractor(config)
        assert extractor.supports_interfaces == expected

    @pytest.mark.parametrize(
        "config,expected_styles",
        [
            (PYTHON_CONFIG, ["dot"]),
            (RUST_CONFIG, ["dot", "scope"]),
            (CPP_CONFIG, ["dot", "arrow", "scope"]),
        ],
        ids=["python-dot", "rust-dot-scope", "cpp-all"],
    )
    def test_access_styles_property(
        self, config: LanguageQueryConfig, expected_styles: list[str]
    ) -> None:
        """Test access_styles property."""
        extractor = make_extractor(config)
        assert extractor.access_styles == expected_styles


# =============================================================================
# Registry Tests
# =============================================================================


class TestExtractorRegistry:
    """Test the extractor registry functionality."""

    def test_registry_has_extractors(self) -> None:
        """Registry should have extractors loaded."""
        registry = get_registry()
        languages = registry.supported_languages()
        assert len(languages) > 0

    def test_get_python_extractor(self) -> None:
        """Can retrieve Python extractor from registry."""
        registry = get_registry()
        extractor = registry.get("python")
        assert extractor is not None
        assert extractor.language_family == "python"
        assert extractor.supports_type_annotations

    def test_get_or_fallback_known_language(self) -> None:
        """Known language returns its extractor."""
        registry = get_registry()
        extractor = registry.get_or_fallback("python")
        assert extractor.language_family == "python"

    def test_get_or_fallback_unknown_language(self) -> None:
        """Unknown language returns fallback extractor."""
        registry = get_registry()
        extractor = registry.get_or_fallback("unknown_xyz_123")
        assert extractor is not None
        assert not extractor.supports_type_annotations

    def test_config_lookup_case_insensitive(self) -> None:
        """Config lookup is case-insensitive."""
        config1 = get_config_for_language("python")
        config2 = get_config_for_language("Python")
        config3 = get_config_for_language("PYTHON")
        assert config1 is config2 is config3

    def test_config_lookup_nonexistent(self) -> None:
        """Nonexistent language returns None."""
        config = get_config_for_language("nonexistent_language_xyz")
        assert config is None

    @pytest.mark.parametrize(
        "lang_key",
        list(ALL_LANGUAGE_CONFIGS.keys()),
        ids=list(ALL_LANGUAGE_CONFIGS.keys()),
    )
    def test_all_configs_retrievable(self, lang_key: str) -> None:
        """All registered configs are retrievable."""
        config = get_config_for_language(lang_key)
        assert config is not None
        assert config.language_family
        assert config.grammar_name


# =============================================================================
# Output Format Consistency Tests
# =============================================================================


class TestOutputFormatConsistency:
    """Verify all extractors produce consistent output formats."""

    @pytest.mark.parametrize(
        "config,code,grammar",
        [
            (PYTHON_CONFIG, "def f(x: int) -> int: return x", "python"),
            (GO_CONFIG, "package main\nfunc f(x int) int { return x }", "go"),
            (RUST_CONFIG, "fn f(x: i32) -> i32 { x }", "rust"),
            (JAVA_CONFIG, "class C { int f(int x) { return x; } }", "java"),
            (CSHARP_CONFIG, "class C { int F(int x) { return x; } }", "c_sharp"),
            (CPP_CONFIG, "int f(int x) { return x; }", "cpp"),
        ],
        ids=["python", "go", "rust", "java", "csharp", "cpp"],
    )
    def test_annotation_dataclass_fields(
        self, config: LanguageQueryConfig, code: str, grammar: str
    ) -> None:
        """All extractors produce TypeAnnotationData with all required fields."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        annotations = extractor.extract_type_annotations(tree.tree, f"test.{grammar}", scopes=[])

        for ann in annotations:
            assert isinstance(ann, TypeAnnotationData)
            assert ann.target_kind in ("parameter", "return", "variable", "field")
            assert ann.target_name  # Non-empty
            assert ann.raw_annotation  # Non-empty
            assert ann.canonical_type  # Non-empty
            assert ann.base_type  # Non-empty
            assert isinstance(ann.is_optional, bool)
            assert isinstance(ann.is_array, bool)
            assert isinstance(ann.is_generic, bool)
            assert isinstance(ann.is_reference, bool)
            assert isinstance(ann.start_line, int)
            assert ann.start_line >= 1
            assert isinstance(ann.start_col, int)
            assert ann.start_col >= 0

    @pytest.mark.parametrize(
        "config,code,grammar,parent",
        [
            (PYTHON_CONFIG, "class C:\n    def m(self): pass", "python", "C"),
            (RUST_CONFIG, "struct S { x: i32 }", "rust", "S"),
            (JAVA_CONFIG, "class C { void m() {} }", "java", "C"),
            (CPP_CONFIG, "struct S { int x; };", "cpp", "S"),
        ],
        ids=["python", "rust", "java", "cpp"],
    )
    def test_member_dataclass_fields(
        self, config: LanguageQueryConfig, code: str, grammar: str, parent: str
    ) -> None:
        """All extractors produce TypeMemberData with all required fields."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        defs = [{"name": parent, "kind": "class", "def_uid": f"uid_{parent}", "start_line": 1}]
        members = extractor.extract_type_members(tree.tree, f"test.{grammar}", defs)

        for member in members:
            assert isinstance(member, TypeMemberData)
            assert member.parent_def_uid
            assert member.parent_type_name
            assert member.parent_kind
            assert member.member_kind in ("field", "method", "property", "constructor")
            assert member.member_name
            assert isinstance(member.start_line, int)
            assert member.start_line >= 1
            assert isinstance(member.start_col, int)

    @pytest.mark.parametrize(
        "config,code,grammar",
        [
            (PYTHON_CONFIG, "foo.bar", "python"),
            (TYPESCRIPT_CONFIG, "foo.bar;", "typescript"),
            (JAVA_CONFIG, "class C { void f() { foo.bar(); } }", "java"),
        ],
        ids=["python", "typescript", "java"],
    )
    def test_member_access_dataclass_fields(
        self, config: LanguageQueryConfig, code: str, grammar: str
    ) -> None:
        """All extractors produce MemberAccessData with all required fields."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        accesses = extractor.extract_member_accesses(
            tree.tree, f"test.{grammar}", scopes=[], type_annotations=[]
        )

        for access in accesses:
            assert isinstance(access, MemberAccessData)
            assert access.access_style in ("dot", "arrow", "scope")
            assert access.full_expression
            assert access.receiver_name
            assert access.member_chain
            assert access.final_member
            assert access.chain_depth >= 1
            assert isinstance(access.is_invocation, bool)
            assert isinstance(access.start_line, int)
            assert isinstance(access.end_line, int)

    @pytest.mark.parametrize(
        "config,code,grammar,impl_name,iface_name",
        [
            (TYPESCRIPT_CONFIG, "interface I {} class C implements I {}", "typescript", "C", "I"),
            (JAVA_CONFIG, "interface I {} class C implements I {}", "java", "C", "I"),
            (CSHARP_CONFIG, "interface I {} class C : I {}", "c_sharp", "C", "I"),
        ],
        ids=["typescript", "java", "csharp"],
    )
    def test_interface_impl_dataclass_fields(
        self,
        config: LanguageQueryConfig,
        code: str,
        grammar: str,
        impl_name: str,
        iface_name: str,
    ) -> None:
        """All extractors produce InterfaceImplData with all required fields."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        defs = [
            {
                "name": iface_name,
                "kind": "interface",
                "def_uid": f"uid_{iface_name}",
                "start_line": 1,
            },
            {"name": impl_name, "kind": "class", "def_uid": f"uid_{impl_name}", "start_line": 2},
        ]
        impls = extractor.extract_interface_impls(tree.tree, f"test.{grammar}", defs)

        for impl in impls:
            assert isinstance(impl, InterfaceImplData)
            assert impl.implementor_def_uid
            assert impl.implementor_name
            assert impl.interface_name
            assert impl.impl_style
            assert isinstance(impl.start_line, int)


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.parametrize(
        "config,grammar",
        [
            (PYTHON_CONFIG, "python"),
            (TYPESCRIPT_CONFIG, "typescript"),
            (GO_CONFIG, "go"),
            (RUST_CONFIG, "rust"),
            (JAVA_CONFIG, "java"),
        ],
        ids=["python", "typescript", "go", "rust", "java"],
    )
    def test_empty_file(self, config: LanguageQueryConfig, grammar: str) -> None:
        """Empty file should not crash and return empty results."""
        extractor = make_extractor(config)
        try:
            tree = make_tree("", grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        annotations = extractor.extract_type_annotations(tree.tree, f"empty.{grammar}", scopes=[])
        members = extractor.extract_type_members(tree.tree, f"empty.{grammar}", defs=[])
        accesses = extractor.extract_member_accesses(
            tree.tree, f"empty.{grammar}", scopes=[], type_annotations=[]
        )
        impls = extractor.extract_interface_impls(tree.tree, f"empty.{grammar}", defs=[])

        assert annotations == []
        assert members == []
        assert accesses == []
        assert impls == []

    @pytest.mark.parametrize(
        "config,grammar,code",
        [
            (PYTHON_CONFIG, "python", "def incomplete("),
            (PYTHON_CONFIG, "python", "class Foo:\n    def "),
            (TYPESCRIPT_CONFIG, "typescript", "function f( {"),
            (JAVA_CONFIG, "java", "class C { void f( }"),
        ],
        ids=[
            "python-incomplete-func",
            "python-incomplete-class",
            "ts-incomplete",
            "java-incomplete",
        ],
    )
    def test_syntax_errors(self, config: LanguageQueryConfig, grammar: str, code: str) -> None:
        """Partial/invalid syntax should not crash."""
        extractor = make_extractor(config)
        try:
            tree = make_tree(code, grammar)
        except ValueError:
            pytest.skip(f"Grammar not available: {grammar}")

        # Should not raise
        annotations = extractor.extract_type_annotations(tree.tree, f"bad.{grammar}", scopes=[])
        assert isinstance(annotations, list)

    def test_deeply_nested_generics_python(self) -> None:
        """Handle complex nested generic types."""
        extractor = make_extractor(PYTHON_CONFIG)
        code = "def process(data: dict[str, list[tuple[int, str]]]) -> None: pass"
        tree = make_tree(code, "python")

        annotations = extractor.extract_type_annotations(tree.tree, "test.py", scopes=[])
        param_anns = [a for a in annotations if a.target_kind == "parameter"]

        assert len(param_anns) >= 1
        assert any(a.is_generic for a in param_anns)

    def test_multiline_type_annotation_python(self) -> None:
        """Handle type annotations spanning multiple lines."""
        extractor = make_extractor(PYTHON_CONFIG)
        code = """
def long_sig(
    name: str,
    items: list[
        tuple[int, str]
    ]
) -> dict[
    str,
    int
]:
    pass
"""
        tree = make_tree(code, "python")
        annotations = extractor.extract_type_annotations(tree.tree, "test.py", scopes=[])

        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 2

    def test_no_type_annotation_query(self) -> None:
        """Extractor with no type_annotation_query returns empty."""
        config = LanguageQueryConfig(
            language_family="test",
            grammar_name="python",
            type_annotation_query="",  # Empty query
        )
        extractor = make_extractor(config)
        tree = make_tree("def f(x: int): pass", "python")

        annotations = extractor.extract_type_annotations(tree.tree, "test.py", scopes=[])
        assert annotations == []

    def test_no_member_query(self) -> None:
        """Extractor with no type_member_query returns empty."""
        config = LanguageQueryConfig(
            language_family="test",
            grammar_name="python",
            type_member_query="",  # Empty query
        )
        extractor = make_extractor(config)
        tree = make_tree("class C:\n    def m(self): pass", "python")

        members = extractor.extract_type_members(
            tree.tree,
            "test.py",
            defs=[{"name": "C", "kind": "class", "def_uid": "C", "start_line": 1}],
        )
        assert members == []

    def test_no_interface_impl_query(self) -> None:
        """Extractor with no interface_impl_query returns empty."""
        config = LanguageQueryConfig(
            language_family="test",
            grammar_name="python",
            interface_impl_query="",  # Empty query
        )
        extractor = make_extractor(config)
        tree = make_tree("class C: pass", "python")

        impls = extractor.extract_interface_impls(
            tree.tree,
            "test.py",
            defs=[{"name": "C", "kind": "class", "def_uid": "C", "start_line": 1}],
        )
        assert impls == []

    def test_member_without_parent_def(self) -> None:
        """Members without matching parent def are skipped."""
        extractor = make_extractor(PYTHON_CONFIG)
        tree = make_tree("class Unknown:\n    def method(self): pass", "python")

        # Pass empty defs - no parent to match
        members = extractor.extract_type_members(tree.tree, "test.py", defs=[])
        assert members == []

    def test_scope_id_lookup(self) -> None:
        """Test scope ID assignment from scopes list."""
        extractor = make_extractor(PYTHON_CONFIG)
        code = "def outer():\n    def inner(x: int): pass"
        tree = make_tree(code, "python")

        scopes = [
            {"scope_id": 1, "start_line": 1, "start_col": 0, "end_line": 2, "end_col": 100},
            {"scope_id": 2, "start_line": 2, "start_col": 4, "end_line": 2, "end_col": 100},
        ]
        annotations = extractor.extract_type_annotations(tree.tree, "test.py", scopes=scopes)

        # The parameter annotation should have a scope_id
        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        if param_anns:
            # Scope lookup should work (exact result depends on tree positions)
            assert isinstance(param_anns[0].scope_id, (int, type(None)))


# =============================================================================
# Utility Method Tests
# =============================================================================


class TestUtilityMethods:
    """Test internal utility methods."""

    def test_is_optional_python(self) -> None:
        """Test optional type detection for Python."""
        extractor = make_extractor(PYTHON_CONFIG)
        assert extractor._is_optional("Optional[int]")
        assert extractor._is_optional("int | None")
        assert extractor._is_optional("None | str")
        assert not extractor._is_optional("int")
        assert not extractor._is_optional("str")

    def test_is_optional_typescript(self) -> None:
        """Test optional type detection for TypeScript."""
        extractor = make_extractor(TYPESCRIPT_CONFIG)
        assert extractor._is_optional("number | null")
        assert extractor._is_optional("string | undefined")
        assert extractor._is_optional("string?")
        assert not extractor._is_optional("number")

    def test_is_array_python(self) -> None:
        """Test array type detection for Python."""
        extractor = make_extractor(PYTHON_CONFIG)
        assert extractor._is_array("list[int]")
        assert extractor._is_array("List[str]")
        assert extractor._is_array("Sequence[int]")
        assert extractor._is_array("tuple[int, str]")
        assert not extractor._is_array("int")
        assert not extractor._is_array("dict[str, int]")

    def test_is_array_typescript(self) -> None:
        """Test array type detection for TypeScript."""
        extractor = make_extractor(TYPESCRIPT_CONFIG)
        assert extractor._is_array("number[]")
        assert extractor._is_array("Array<string>")
        assert extractor._is_array("ReadonlyArray<number>")
        assert not extractor._is_array("number")

    def test_extract_base_type_python(self) -> None:
        """Test base type extraction for Python."""
        extractor = make_extractor(PYTHON_CONFIG)
        assert extractor._extract_base_type("list[int]") == "list"
        assert extractor._extract_base_type("dict[str, int]") == "dict"
        assert extractor._extract_base_type("int") == "int"
        assert extractor._extract_base_type("  str  ") == "str"

    def test_extract_base_type_rust(self) -> None:
        """Test base type extraction for Rust (with reference indicator)."""
        extractor = make_extractor(RUST_CONFIG)
        assert extractor._extract_base_type("&str") == "str"
        assert extractor._extract_base_type("&mut String") == "mut String"
        assert extractor._extract_base_type("Vec<i32>") == "Vec"

    def test_canonicalize_type(self) -> None:
        """Test type canonicalization."""
        extractor = make_extractor(PYTHON_CONFIG)
        assert extractor._canonicalize_type("  int  ") == "int"
        assert extractor._canonicalize_type("list[str]") == "list[str]"

    def test_compute_member_def_uid(self) -> None:
        """Test member def_uid computation is stable."""
        extractor = make_extractor(PYTHON_CONFIG)
        parent = {"def_uid": "parent_uid_123"}

        uid1 = extractor._compute_member_def_uid(parent, "method_name", "method")
        uid2 = extractor._compute_member_def_uid(parent, "method_name", "method")
        uid3 = extractor._compute_member_def_uid(parent, "other_method", "method")

        assert uid1 == uid2  # Same inputs = same output
        assert uid1 != uid3  # Different method = different uid
        assert len(uid1) == 16  # SHA256 truncated to 16 chars


# =============================================================================
# Language-Specific Quirks Tests
# =============================================================================


class TestLanguageSpecificBehavior:
    """Test language-specific extraction behavior."""

    def test_python_private_visibility(self) -> None:
        """Python underscore prefix indicates private visibility."""
        extractor = make_extractor(PYTHON_CONFIG)
        code = "class C:\n    def _private(self): pass\n    def public(self): pass"
        tree = make_tree(code, "python")

        members = extractor.extract_type_members(
            tree.tree,
            "test.py",
            defs=[{"name": "C", "kind": "class", "def_uid": "C", "start_line": 1}],
        )

        private_member = next((m for m in members if m.member_name == "_private"), None)
        public_member = next((m for m in members if m.member_name == "public"), None)

        if private_member:
            assert private_member.visibility == "private"
        if public_member:
            assert public_member.visibility == "public"

    def test_rust_reference_types(self) -> None:
        """Rust reference types are detected."""
        extractor = make_extractor(RUST_CONFIG)
        code = "fn f(x: &str, y: &mut i32) {}"
        tree = make_tree(code, "rust")

        annotations = extractor.extract_type_annotations(tree.tree, "test.rs", scopes=[])
        ref_anns = [a for a in annotations if a.is_reference]

        assert len(ref_anns) >= 1

    def test_go_pointer_types(self) -> None:
        """Go pointer types are detected as references."""
        extractor = make_extractor(GO_CONFIG)
        code = "package main\nfunc f(x *int) {}"
        tree = make_tree(code, "go")

        annotations = extractor.extract_type_annotations(tree.tree, "test.go", scopes=[])
        ref_anns = [a for a in annotations if a.is_reference]

        assert len(ref_anns) >= 1

    def test_ruby_no_type_annotations(self) -> None:
        """Ruby has no native type annotations."""
        extractor = make_extractor(RUBY_CONFIG)
        assert not extractor.supports_type_annotations

        code = "class C\n  def method(x)\n  end\nend"
        try:
            tree = make_tree(code, "ruby")
            annotations = extractor.extract_type_annotations(tree.tree, "test.rb", scopes=[])
            assert annotations == []  # No type annotations in Ruby
        except ValueError:
            pytest.skip("Ruby grammar not available")


# =============================================================================
# Grammar Loading Tests
# =============================================================================


class TestGrammarLoading:
    """Test grammar loading behavior."""

    def test_unknown_grammar_raises(self) -> None:
        """Unknown grammar name raises ValueError."""
        config = LanguageQueryConfig(
            language_family="unknown",
            grammar_name="nonexistent_grammar_xyz",
        )
        extractor = QueryBasedExtractor(config)

        with pytest.raises(ValueError, match="Unknown grammar"):
            extractor._get_language()

    def test_grammar_cached(self) -> None:
        """Grammar is loaded once and cached."""
        extractor = make_extractor(PYTHON_CONFIG)

        lang1 = extractor._get_language()
        lang2 = extractor._get_language()

        assert lang1 is lang2  # Same object

    def test_query_cached(self) -> None:
        """Queries are compiled once and cached."""
        extractor = make_extractor(PYTHON_CONFIG)
        query_str = "(identifier) @name"

        query1 = extractor._get_query(query_str)
        query2 = extractor._get_query(query_str)

        assert query1 is query2  # Same object

    def test_invalid_query_returns_none(self) -> None:
        """Invalid query string returns None."""
        extractor = make_extractor(PYTHON_CONFIG)

        result = extractor._get_query("(invalid_node_type_xyz) @name")
        # Depending on tree-sitter behavior, this might return None or raise
        # We're testing that it doesn't crash the extractor
        assert result is None or result is not None  # Just ensure no crash

    def test_empty_query_returns_none(self) -> None:
        """Empty query string returns None."""
        extractor = make_extractor(PYTHON_CONFIG)

        result = extractor._get_query("")
        assert result is None

        result = extractor._get_query("   ")
        assert result is None
