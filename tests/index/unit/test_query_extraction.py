"""Unit tests for query-based type extraction.

Tests the QueryBasedExtractor with LanguageQueryConfig for all supported languages.
"""

import pytest

from codeplane.index._internal.extraction import (
    TypeAnnotationData,
    TypeMemberData,
    get_registry,
)
from codeplane.index._internal.extraction.languages import (
    CPP_CONFIG,
    CSHARP_CONFIG,
    GO_CONFIG,
    JAVA_CONFIG,
    PYTHON_CONFIG,
    RUST_CONFIG,
    TYPESCRIPT_CONFIG,
    get_config_for_language,
)
from codeplane.index._internal.extraction.query_based import QueryBasedExtractor
from codeplane.index._internal.parsing.grammars import parse_code

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def python_extractor() -> QueryBasedExtractor:
    return QueryBasedExtractor(PYTHON_CONFIG)


@pytest.fixture
def typescript_extractor() -> QueryBasedExtractor:
    return QueryBasedExtractor(TYPESCRIPT_CONFIG)


@pytest.fixture
def go_extractor() -> QueryBasedExtractor:
    return QueryBasedExtractor(GO_CONFIG)


@pytest.fixture
def rust_extractor() -> QueryBasedExtractor:
    return QueryBasedExtractor(RUST_CONFIG)


@pytest.fixture
def java_extractor() -> QueryBasedExtractor:
    return QueryBasedExtractor(JAVA_CONFIG)


@pytest.fixture
def csharp_extractor() -> QueryBasedExtractor:
    return QueryBasedExtractor(CSHARP_CONFIG)


@pytest.fixture
def cpp_extractor() -> QueryBasedExtractor:
    return QueryBasedExtractor(CPP_CONFIG)


def make_tree(code: str, language: str):
    """Parse code into a tree-sitter tree."""
    return parse_code(code.encode(), language)


# =============================================================================
# Python Tests
# =============================================================================


class TestPythonExtraction:
    def test_function_parameter_annotation(self, python_extractor: QueryBasedExtractor) -> None:
        code = """
def greet(name: str) -> str:
    return f"Hello, {name}"
"""
        tree = make_tree(code, "python")
        annotations = python_extractor.extract_type_annotations(tree, "test.py", scopes=[])

        # Should extract parameter annotation
        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 1
        assert any(a.target_name == "name" and a.base_type == "str" for a in param_anns)

    def test_function_return_type(self, python_extractor: QueryBasedExtractor) -> None:
        code = """
def get_count() -> int:
    return 42
"""
        tree = make_tree(code, "python")
        annotations = python_extractor.extract_type_annotations(tree, "test.py", scopes=[])

        return_anns = [a for a in annotations if a.target_kind == "return"]
        assert len(return_anns) >= 1
        assert any(a.target_name == "get_count" and a.base_type == "int" for a in return_anns)

    def test_optional_type(self, python_extractor: QueryBasedExtractor) -> None:
        code = """
def maybe(x: int | None) -> int | None:
    return x
"""
        tree = make_tree(code, "python")
        annotations = python_extractor.extract_type_annotations(tree, "test.py", scopes=[])

        optional_anns = [a for a in annotations if a.is_optional]
        assert len(optional_anns) >= 1

    def test_class_method_extraction(self, python_extractor: QueryBasedExtractor) -> None:
        code = """
class Person:
    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        return f"Hello, {self.name}"
"""
        tree = make_tree(code, "python")
        members = python_extractor.extract_type_members(
            tree,
            "test.py",
            defs=[{"name": "Person", "kind": "class", "def_uid": "Person", "start_line": 2}],
        )

        method_members = [m for m in members if m.member_kind == "method"]
        assert len(method_members) >= 2
        method_names = [m.member_name for m in method_members]
        assert "__init__" in method_names
        assert "greet" in method_names

    def test_member_access_extraction(self, python_extractor: QueryBasedExtractor) -> None:
        code = """
class Foo:
    value: int

foo = Foo()
print(foo.value)
"""
        tree = make_tree(code, "python")
        accesses = python_extractor.extract_member_accesses(
            tree, "test.py", scopes=[], type_annotations=[]
        )

        assert len(accesses) >= 1
        assert any(a.receiver_name == "foo" and a.final_member == "value" for a in accesses)


# =============================================================================
# TypeScript Tests
# =============================================================================


class TestTypeScriptExtraction:
    def test_function_parameter_annotation(self, typescript_extractor: QueryBasedExtractor) -> None:
        code = """
function greet(name: string): string {
    return `Hello, ${name}`;
}
"""
        tree = make_tree(code, "typescript")
        annotations = typescript_extractor.extract_type_annotations(tree, "test.ts", scopes=[])

        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 1

    def test_interface_member_extraction(self, typescript_extractor: QueryBasedExtractor) -> None:
        code = """
interface Person {
    name: string;
    age: number;
    greet(): void;
}
"""
        tree = make_tree(code, "typescript")
        members = typescript_extractor.extract_type_members(
            tree,
            "test.ts",
            defs=[{"name": "Person", "kind": "interface", "def_uid": "Person", "start_line": 2}],
        )

        assert len(members) >= 3
        member_names = [m.member_name for m in members]
        assert "name" in member_names
        assert "age" in member_names
        assert "greet" in member_names

    def test_class_implements_interface(self, typescript_extractor: QueryBasedExtractor) -> None:
        code = """
interface Greeter {
    greet(): string;
}

class FriendlyPerson implements Greeter {
    greet(): string {
        return "Hello!";
    }
}
"""
        tree = make_tree(code, "typescript")
        impls = typescript_extractor.extract_interface_impls(
            tree,
            "test.ts",
            defs=[
                {"name": "Greeter", "kind": "interface", "def_uid": "Greeter", "start_line": 2},
                {
                    "name": "FriendlyPerson",
                    "kind": "class",
                    "def_uid": "FriendlyPerson",
                    "start_line": 6,
                },
            ],
        )

        assert len(impls) >= 1
        assert any(
            i.implementor_name == "FriendlyPerson" and i.interface_name == "Greeter" for i in impls
        )


# =============================================================================
# Go Tests
# =============================================================================


class TestGoExtraction:
    def test_function_parameter_annotation(self, go_extractor: QueryBasedExtractor) -> None:
        code = """
package main

func greet(name string) string {
    return "Hello, " + name
}
"""
        tree = make_tree(code, "go")
        annotations = go_extractor.extract_type_annotations(tree, "test.go", scopes=[])

        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 1
        assert any(a.target_name == "name" and a.base_type == "string" for a in param_anns)

    def test_struct_field_extraction(self, go_extractor: QueryBasedExtractor) -> None:
        code = """
package main

type Person struct {
    Name string
    Age  int
}
"""
        tree = make_tree(code, "go")
        members = go_extractor.extract_type_members(
            tree,
            "test.go",
            defs=[{"name": "Person", "kind": "type", "def_uid": "Person", "start_line": 4}],
        )

        assert len(members) >= 2
        member_names = [m.member_name for m in members]
        assert "Name" in member_names
        assert "Age" in member_names

    def test_pointer_type(self, go_extractor: QueryBasedExtractor) -> None:
        code = """
package main

func modify(p *int) {
    *p = 42
}
"""
        tree = make_tree(code, "go")
        annotations = go_extractor.extract_type_annotations(tree, "test.go", scopes=[])

        ref_anns = [a for a in annotations if a.is_reference]
        assert len(ref_anns) >= 1


# =============================================================================
# Rust Tests
# =============================================================================


class TestRustExtraction:
    def test_function_parameter_annotation(self, rust_extractor: QueryBasedExtractor) -> None:
        code = """
fn greet(name: &str) -> String {
    format!("Hello, {}", name)
}
"""
        tree = make_tree(code, "rust")
        annotations = rust_extractor.extract_type_annotations(tree, "test.rs", scopes=[])

        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 1

    def test_struct_field_extraction(self, rust_extractor: QueryBasedExtractor) -> None:
        code = """
struct Person {
    name: String,
    age: u32,
}
"""
        tree = make_tree(code, "rust")
        members = rust_extractor.extract_type_members(
            tree,
            "test.rs",
            defs=[{"name": "Person", "kind": "struct", "def_uid": "Person", "start_line": 1}],
        )

        assert len(members) >= 2
        member_names = [m.member_name for m in members]
        assert "name" in member_names
        assert "age" in member_names

    def test_trait_impl(self, rust_extractor: QueryBasedExtractor) -> None:
        code = """
trait Greeter {
    fn greet(&self) -> String;
}

struct Person;

impl Greeter for Person {
    fn greet(&self) -> String {
        String::from("Hello!")
    }
}
"""
        tree = make_tree(code, "rust")
        impls = rust_extractor.extract_interface_impls(
            tree,
            "test.rs",
            defs=[
                {"name": "Greeter", "kind": "trait", "def_uid": "Greeter", "start_line": 1},
                {"name": "Person", "kind": "struct", "def_uid": "Person", "start_line": 5},
            ],
        )

        assert len(impls) >= 1
        assert any(i.implementor_name == "Person" and i.interface_name == "Greeter" for i in impls)

    def test_optional_type(self, rust_extractor: QueryBasedExtractor) -> None:
        code = """
fn maybe(x: Option<i32>) -> Option<i32> {
    x
}
"""
        tree = make_tree(code, "rust")
        annotations = rust_extractor.extract_type_annotations(tree, "test.rs", scopes=[])

        optional_anns = [a for a in annotations if a.is_optional]
        assert len(optional_anns) >= 1


# =============================================================================
# Java Tests
# =============================================================================


class TestJavaExtraction:
    def test_method_parameter_annotation(self, java_extractor: QueryBasedExtractor) -> None:
        code = """
public class Greeter {
    public String greet(String name) {
        return "Hello, " + name;
    }
}
"""
        tree = make_tree(code, "java")
        annotations = java_extractor.extract_type_annotations(tree, "Test.java", scopes=[])

        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 1
        assert any(a.target_name == "name" and a.base_type == "String" for a in param_anns)

    def test_field_annotation(self, java_extractor: QueryBasedExtractor) -> None:
        code = """
public class Person {
    private String name;
    private int age;
}
"""
        tree = make_tree(code, "java")
        annotations = java_extractor.extract_type_annotations(tree, "Person.java", scopes=[])

        field_anns = [a for a in annotations if a.target_kind == "field"]
        assert len(field_anns) >= 2

    def test_class_member_extraction(self, java_extractor: QueryBasedExtractor) -> None:
        code = """
public class Person {
    private String name;

    public String getName() {
        return name;
    }
}
"""
        tree = make_tree(code, "java")
        members = java_extractor.extract_type_members(
            tree,
            "Person.java",
            defs=[{"name": "Person", "kind": "class", "def_uid": "Person", "start_line": 2}],
        )

        assert len(members) >= 2
        member_names = [m.member_name for m in members]
        assert "name" in member_names
        assert "getName" in member_names

    def test_interface_implementation(self, java_extractor: QueryBasedExtractor) -> None:
        code = """
interface Greeter {
    String greet();
}

class FriendlyPerson implements Greeter {
    public String greet() {
        return "Hello!";
    }
}
"""
        tree = make_tree(code, "java")
        impls = java_extractor.extract_interface_impls(
            tree,
            "Test.java",
            defs=[
                {"name": "Greeter", "kind": "interface", "def_uid": "Greeter", "start_line": 1},
                {
                    "name": "FriendlyPerson",
                    "kind": "class",
                    "def_uid": "FriendlyPerson",
                    "start_line": 5,
                },
            ],
        )

        assert len(impls) >= 1


# =============================================================================
# C# Tests
# =============================================================================


class TestCSharpExtraction:
    def test_method_parameter_annotation(self, csharp_extractor: QueryBasedExtractor) -> None:
        code = """
public class Greeter {
    public string Greet(string name) {
        return "Hello, " + name;
    }
}
"""
        tree = make_tree(code, "c_sharp")
        annotations = csharp_extractor.extract_type_annotations(tree, "Test.cs", scopes=[])

        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 1

    def test_property_annotation(self, csharp_extractor: QueryBasedExtractor) -> None:
        code = """
public class Person {
    public string Name { get; set; }
    public int Age { get; set; }
}
"""
        tree = make_tree(code, "c_sharp")
        annotations = csharp_extractor.extract_type_annotations(tree, "Person.cs", scopes=[])

        field_anns = [a for a in annotations if a.target_kind == "field"]
        assert len(field_anns) >= 2

    def test_interface_implementation(self, csharp_extractor: QueryBasedExtractor) -> None:
        code = """
interface IGreeter {
    string Greet();
}

class FriendlyPerson : IGreeter {
    public string Greet() {
        return "Hello!";
    }
}
"""
        tree = make_tree(code, "c_sharp")
        impls = csharp_extractor.extract_interface_impls(
            tree,
            "Test.cs",
            defs=[
                {"name": "IGreeter", "kind": "interface", "def_uid": "IGreeter", "start_line": 1},
                {
                    "name": "FriendlyPerson",
                    "kind": "class",
                    "def_uid": "FriendlyPerson",
                    "start_line": 5,
                },
            ],
        )

        assert len(impls) >= 1


# =============================================================================
# C++ Tests
# =============================================================================


class TestCppExtraction:
    def test_function_parameter_annotation(self, cpp_extractor: QueryBasedExtractor) -> None:
        code = """
#include <string>

std::string greet(std::string name) {
    return "Hello, " + name;
}
"""
        tree = make_tree(code, "cpp")
        annotations = cpp_extractor.extract_type_annotations(tree, "test.cpp", scopes=[])

        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 1

    def test_struct_field_extraction(self, cpp_extractor: QueryBasedExtractor) -> None:
        code = """
struct Person {
    std::string name;
    int age;
};
"""
        tree = make_tree(code, "cpp")
        members = cpp_extractor.extract_type_members(
            tree,
            "test.cpp",
            defs=[{"name": "Person", "kind": "struct", "def_uid": "Person", "start_line": 1}],
        )

        assert len(members) >= 2
        member_names = [m.member_name for m in members]
        assert "name" in member_names
        assert "age" in member_names

    def test_class_inheritance(self, cpp_extractor: QueryBasedExtractor) -> None:
        code = """
class Base {
    virtual void foo() = 0;
};

class Derived : public Base {
    void foo() override {}
};
"""
        tree = make_tree(code, "cpp")
        impls = cpp_extractor.extract_interface_impls(
            tree,
            "test.cpp",
            defs=[
                {"name": "Base", "kind": "class", "def_uid": "Base", "start_line": 1},
                {"name": "Derived", "kind": "class", "def_uid": "Derived", "start_line": 5},
            ],
        )

        assert len(impls) >= 1


# =============================================================================
# Registry Tests
# =============================================================================


class TestExtractorRegistry:
    def test_registry_has_extractors(self) -> None:
        registry = get_registry()
        languages = registry.supported_languages()

        # Should have at least the major languages
        assert len(languages) > 0

    def test_get_python_extractor(self) -> None:
        registry = get_registry()
        extractor = registry.get("python")

        assert extractor is not None
        assert extractor.language_family == "python"
        assert extractor.supports_type_annotations

    def test_get_or_fallback(self) -> None:
        registry = get_registry()

        # Known language should return its extractor
        python_ext = registry.get_or_fallback("python")
        assert python_ext.language_family == "python"

        # Unknown language should return fallback
        unknown_ext = registry.get_or_fallback("unknown_lang_xyz")
        assert unknown_ext is not None
        assert not unknown_ext.supports_type_annotations

    def test_config_lookup(self) -> None:
        # Test the language config lookup function
        config = get_config_for_language("python")
        assert config is not None
        assert config.language_family == "python"

        config = get_config_for_language("Python")  # Case insensitive
        assert config is not None

        config = get_config_for_language("nonexistent")
        assert config is None


# =============================================================================
# Cross-Language Consistency Tests
# =============================================================================


class TestCrossLanguageConsistency:
    """Verify that all extractors produce consistent output formats."""

    @pytest.mark.parametrize(
        "config,code,lang",
        [
            (PYTHON_CONFIG, "def f(x: int) -> int: return x", "python"),
            (GO_CONFIG, "package main\nfunc f(x int) int { return x }", "go"),
            (RUST_CONFIG, "fn f(x: i32) -> i32 { x }", "rust"),
        ],
    )
    def test_annotation_dataclass_fields(self, config, code: str, lang: str) -> None:
        """All extractors should produce TypeAnnotationData with required fields."""
        extractor = QueryBasedExtractor(config)
        tree = make_tree(code, lang)
        annotations = extractor.extract_type_annotations(tree, f"test.{lang}", scopes=[])

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

    @pytest.mark.parametrize(
        "config,code,lang",
        [
            (
                PYTHON_CONFIG,
                "class C:\n    def m(self): pass",
                "python",
            ),
            (
                RUST_CONFIG,
                "struct S { x: i32 }",
                "rust",
            ),
        ],
    )
    def test_member_dataclass_fields(self, config, code: str, lang: str) -> None:
        """All extractors should produce TypeMemberData with required fields."""
        extractor = QueryBasedExtractor(config)
        tree = make_tree(code, lang)
        members = extractor.extract_type_members(
            tree,
            f"test.{lang}",
            defs=[
                {
                    "name": "C" if lang == "python" else "S",
                    "kind": "class" if lang == "python" else "struct",
                    "def_uid": "test",
                    "start_line": 1,
                }
            ],
        )

        for member in members:
            assert isinstance(member, TypeMemberData)
            assert member.parent_def_uid
            assert member.parent_type_name
            assert member.member_kind in ("field", "method", "property")
            assert member.member_name


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    def test_empty_file(self, python_extractor: QueryBasedExtractor) -> None:
        """Empty file should not crash."""
        tree = make_tree("", "python")
        annotations = python_extractor.extract_type_annotations(tree, "empty.py", scopes=[])
        members = python_extractor.extract_type_members(tree, "empty.py", defs=[])
        accesses = python_extractor.extract_member_accesses(
            tree, "empty.py", scopes=[], type_annotations=[]
        )

        assert annotations == []
        assert members == []
        assert accesses == []

    def test_syntax_error_in_code(self, python_extractor: QueryBasedExtractor) -> None:
        """Partial/invalid syntax should still extract what it can."""
        code = """
def incomplete(
    # Missing closing paren
class Foo:
    x: int
"""
        tree = make_tree(code, "python")
        # Should not crash
        annotations = python_extractor.extract_type_annotations(tree, "bad.py", scopes=[])
        # May or may not find annotations depending on tree-sitter error recovery
        assert isinstance(annotations, list)

    def test_deeply_nested_generics(self, python_extractor: QueryBasedExtractor) -> None:
        """Handle complex nested generic types."""
        code = """
def process(data: dict[str, list[tuple[int, str]]]) -> None:
    pass
"""
        tree = make_tree(code, "python")
        annotations = python_extractor.extract_type_annotations(tree, "test.py", scopes=[])

        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 1
        # Should be marked as generic
        assert any(a.is_generic for a in param_anns)

    def test_multiline_type_annotation(self, python_extractor: QueryBasedExtractor) -> None:
        """Handle type annotations spanning multiple lines."""
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
        annotations = python_extractor.extract_type_annotations(tree, "test.py", scopes=[])

        # Should extract both parameter annotations
        param_anns = [a for a in annotations if a.target_kind == "parameter"]
        assert len(param_anns) >= 2
