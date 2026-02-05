"""Tests for index extraction dataclasses and helpers.

Covers:
- TypeAnnotationData
- TypeMemberData
- MemberAccessData
- InterfaceImplData
- ReceiverShapeData
"""

from codeplane.index._internal.extraction import (
    InterfaceImplData,
    MemberAccessData,
    ReceiverShapeData,
    TypeAnnotationData,
    TypeMemberData,
)


class TestTypeAnnotationData:
    """Tests for TypeAnnotationData dataclass."""

    def test_basic_creation(self):
        data = TypeAnnotationData(
            target_kind="parameter",
            target_name="x",
            raw_annotation="int",
            canonical_type="int",
            base_type="int",
        )
        assert data.target_kind == "parameter"
        assert data.target_name == "x"
        assert data.raw_annotation == "int"
        assert data.canonical_type == "int"
        assert data.base_type == "int"
        assert data.is_optional is False
        assert data.is_array is False
        assert data.is_generic is False
        assert data.is_reference is False
        assert data.is_mutable is True
        assert data.type_args == []
        assert data.scope_id is None
        assert data.start_line == 0
        assert data.start_col == 0

    def test_optional_type(self):
        data = TypeAnnotationData(
            target_kind="variable",
            target_name="name",
            raw_annotation="str | None",
            canonical_type="Optional[str]",
            base_type="str",
            is_optional=True,
        )
        assert data.is_optional is True

    def test_array_type(self):
        data = TypeAnnotationData(
            target_kind="return",
            target_name="result",
            raw_annotation="list[int]",
            canonical_type="list[int]",
            base_type="list",
            is_array=True,
            type_args=["int"],
        )
        assert data.is_array is True
        assert data.type_args == ["int"]

    def test_generic_type(self):
        data = TypeAnnotationData(
            target_kind="field",
            target_name="items",
            raw_annotation="Dict[str, int]",
            canonical_type="Dict[str, int]",
            base_type="Dict",
            is_generic=True,
            type_args=["str", "int"],
        )
        assert data.is_generic is True
        assert data.type_args == ["str", "int"]

    def test_with_location(self):
        data = TypeAnnotationData(
            target_kind="parameter",
            target_name="x",
            raw_annotation="int",
            canonical_type="int",
            base_type="int",
            scope_id=42,
            start_line=10,
            start_col=4,
        )
        assert data.scope_id == 42
        assert data.start_line == 10
        assert data.start_col == 4


class TestTypeMemberData:
    """Tests for TypeMemberData dataclass."""

    def test_basic_field(self):
        data = TypeMemberData(
            parent_def_uid="def:MyClass",
            parent_type_name="MyClass",
            parent_kind="class",
            member_kind="field",
            member_name="value",
        )
        assert data.parent_def_uid == "def:MyClass"
        assert data.parent_type_name == "MyClass"
        assert data.parent_kind == "class"
        assert data.member_kind == "field"
        assert data.member_name == "value"
        assert data.member_def_uid is None
        assert data.type_annotation is None
        assert data.canonical_type is None
        assert data.base_type is None
        assert data.visibility is None
        assert data.is_static is False
        assert data.is_abstract is False

    def test_method_with_type(self):
        data = TypeMemberData(
            parent_def_uid="def:MyClass",
            parent_type_name="MyClass",
            parent_kind="class",
            member_kind="method",
            member_name="get_value",
            member_def_uid="def:MyClass.get_value",
            type_annotation="int",
            canonical_type="int",
            base_type="int",
            visibility="public",
        )
        assert data.member_def_uid == "def:MyClass.get_value"
        assert data.type_annotation == "int"
        assert data.visibility == "public"

    def test_static_method(self):
        data = TypeMemberData(
            parent_def_uid="def:MyClass",
            parent_type_name="MyClass",
            parent_kind="class",
            member_kind="method",
            member_name="create",
            is_static=True,
        )
        assert data.is_static is True

    def test_abstract_method(self):
        data = TypeMemberData(
            parent_def_uid="def:Interface",
            parent_type_name="Interface",
            parent_kind="interface",
            member_kind="method",
            member_name="do_something",
            is_abstract=True,
        )
        assert data.is_abstract is True

    def test_struct_field(self):
        data = TypeMemberData(
            parent_def_uid="def:Point",
            parent_type_name="Point",
            parent_kind="struct",
            member_kind="field",
            member_name="x",
            type_annotation="float",
            start_line=5,
            start_col=4,
        )
        assert data.parent_kind == "struct"
        assert data.start_line == 5
        assert data.start_col == 4


class TestMemberAccessData:
    """Tests for MemberAccessData dataclass."""

    def test_simple_dot_access(self):
        data = MemberAccessData(
            access_style="dot",
            full_expression="obj.value",
            receiver_name="obj",
            member_chain="value",
            final_member="value",
            chain_depth=1,
        )
        assert data.access_style == "dot"
        assert data.full_expression == "obj.value"
        assert data.receiver_name == "obj"
        assert data.member_chain == "value"
        assert data.final_member == "value"
        assert data.chain_depth == 1
        assert data.is_invocation is False
        assert data.arg_count is None

    def test_chained_access(self):
        data = MemberAccessData(
            access_style="dot",
            full_expression="obj.prop.nested.value",
            receiver_name="obj",
            member_chain="prop.nested.value",
            final_member="value",
            chain_depth=3,
        )
        assert data.chain_depth == 3
        assert data.final_member == "value"

    def test_method_invocation(self):
        data = MemberAccessData(
            access_style="dot",
            full_expression="obj.method(a, b)",
            receiver_name="obj",
            member_chain="method",
            final_member="method",
            chain_depth=1,
            is_invocation=True,
            arg_count=2,
        )
        assert data.is_invocation is True
        assert data.arg_count == 2

    def test_arrow_access(self):
        data = MemberAccessData(
            access_style="arrow",
            full_expression="ptr->field",
            receiver_name="ptr",
            member_chain="field",
            final_member="field",
            chain_depth=1,
        )
        assert data.access_style == "arrow"

    def test_scope_access(self):
        data = MemberAccessData(
            access_style="scope",
            full_expression="MyClass::static_method()",
            receiver_name="MyClass",
            member_chain="static_method",
            final_member="static_method",
            chain_depth=1,
            is_invocation=True,
            arg_count=0,
        )
        assert data.access_style == "scope"

    def test_with_location(self):
        data = MemberAccessData(
            access_style="dot",
            full_expression="x.y",
            receiver_name="x",
            member_chain="y",
            final_member="y",
            chain_depth=1,
            scope_id=10,
            start_line=5,
            start_col=8,
            end_line=5,
            end_col=11,
        )
        assert data.scope_id == 10
        assert data.start_line == 5
        assert data.end_line == 5


class TestInterfaceImplData:
    """Tests for InterfaceImplData dataclass."""

    def test_explicit_implementation(self):
        data = InterfaceImplData(
            implementor_def_uid="def:MyClass",
            implementor_name="MyClass",
            interface_name="ISerializable",
        )
        assert data.implementor_def_uid == "def:MyClass"
        assert data.implementor_name == "MyClass"
        assert data.interface_name == "ISerializable"
        assert data.interface_def_uid is None
        assert data.impl_style == "explicit"
        assert data.start_line == 0

    def test_structural_implementation(self):
        data = InterfaceImplData(
            implementor_def_uid="def:Handler",
            implementor_name="Handler",
            interface_name="http.Handler",
            interface_def_uid="def:http.Handler",
            impl_style="structural",
        )
        assert data.impl_style == "structural"
        assert data.interface_def_uid == "def:http.Handler"

    def test_inferred_implementation(self):
        data = InterfaceImplData(
            implementor_def_uid="def:Impl",
            implementor_name="Impl",
            interface_name="Protocol",
            impl_style="inferred",
            start_line=10,
            start_col=0,
        )
        assert data.impl_style == "inferred"
        assert data.start_line == 10


class TestReceiverShapeData:
    """Tests for ReceiverShapeData dataclass."""

    def test_basic_creation(self):
        data = ReceiverShapeData(
            receiver_name="obj",
            declared_type=None,
            observed_fields=[],
            observed_methods=[],
        )
        assert data.receiver_name == "obj"
        assert data.declared_type is None
        assert data.observed_fields == []
        assert data.observed_methods == []
        assert data.scope_id is None

    def test_with_observed_members(self):
        data = ReceiverShapeData(
            receiver_name="handler",
            declared_type="HttpHandler",
            observed_fields=["config", "client"],
            observed_methods=["handle", "validate"],
            scope_id=5,
        )
        assert data.declared_type == "HttpHandler"
        assert "config" in data.observed_fields
        assert "handle" in data.observed_methods
        assert data.scope_id == 5

    def test_shape_hash_is_deterministic(self):
        data = ReceiverShapeData(
            receiver_name="x",
            declared_type=None,
            observed_fields=["a", "b"],
            observed_methods=["c"],
        )
        hash1 = data.shape_hash
        hash2 = data.shape_hash
        assert hash1 == hash2
        assert len(hash1) == 16  # sha256 truncated to 16 chars

    def test_shape_hash_varies_with_members(self):
        data1 = ReceiverShapeData(
            receiver_name="x",
            declared_type=None,
            observed_fields=["a"],
            observed_methods=[],
        )
        data2 = ReceiverShapeData(
            receiver_name="x",
            declared_type=None,
            observed_fields=["b"],
            observed_methods=[],
        )
        assert data1.shape_hash != data2.shape_hash

    def test_shape_hash_independent_of_order(self):
        data1 = ReceiverShapeData(
            receiver_name="x",
            declared_type=None,
            observed_fields=["b", "a"],
            observed_methods=["z", "y"],
        )
        data2 = ReceiverShapeData(
            receiver_name="x",
            declared_type=None,
            observed_fields=["a", "b"],
            observed_methods=["y", "z"],
        )
        # Hash should be deterministic regardless of input order
        assert data1.shape_hash == data2.shape_hash

    def test_observed_members_json(self):
        data = ReceiverShapeData(
            receiver_name="obj",
            declared_type=None,
            observed_fields=["z", "a"],
            observed_methods=["y", "b"],
        )
        json_str = data.observed_members_json
        # JSON should have sorted members
        assert '"fields": ["a", "z"]' in json_str
        assert '"methods": ["b", "y"]' in json_str
