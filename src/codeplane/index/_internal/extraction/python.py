"""Python-specific type extractor.

Extracts type annotations, class members, and member accesses from Python code.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from codeplane.index._internal.extraction import (
    BaseTypeExtractor,
    InterfaceImplData,
    MemberAccessData,
    TypeAnnotationData,
    TypeMemberData,
)

if TYPE_CHECKING:
    from tree_sitter import Node, Tree


class PythonExtractor(BaseTypeExtractor):
    """Python type annotation extractor.

    Handles:
    - Function parameter annotations: def f(x: int) -> str
    - Variable annotations: x: int = 5
    - Class field annotations (dataclass style)
    - Class members (methods, properties)
    - Attribute access chains: obj.field.method()
    """

    @property
    def language_family(self) -> str:
        return "python"

    @property
    def supports_type_annotations(self) -> bool:
        return True

    @property
    def supports_interfaces(self) -> bool:
        # Python has protocols but they're not declared with 'implements'
        return False

    @property
    def access_styles(self) -> list[str]:
        return ["dot"]

    def extract_type_annotations(
        self,
        tree: Tree,
        file_path: str,
        scopes: list[dict[str, Any]],
    ) -> list[TypeAnnotationData]:
        """Extract Python type annotations."""
        annotations: list[TypeAnnotationData] = []

        def visit(node: Node, scope_id: int | None = None) -> None:
            # Function parameters with type annotations
            if node.type == "typed_parameter":
                ann = self._extract_typed_parameter(node, scope_id)
                if ann:
                    annotations.append(ann)

            # Default parameters with type annotations
            elif node.type == "typed_default_parameter":
                ann = self._extract_typed_default_parameter(node, scope_id)
                if ann:
                    annotations.append(ann)

            # Function return type
            elif node.type == "function_definition":
                ann = self._extract_return_annotation(node, scope_id)
                if ann:
                    annotations.append(ann)
                # Update scope for children
                scope_id = self._get_scope_id_for_node(node, scopes)

            # Variable annotation: x: int = 5
            elif node.type == "assignment":
                ann = self._extract_annotated_assignment(node, scope_id)
                if ann:
                    annotations.append(ann)

            # Standalone annotation: x: int
            elif node.type == "expression_statement":
                for child in node.children:
                    if child.type == "assignment" and self._has_annotation(child):
                        ann = self._extract_annotated_assignment(child, scope_id)
                        if ann:
                            annotations.append(ann)

            # Update scope for class definitions
            if node.type == "class_definition":
                scope_id = self._get_scope_id_for_node(node, scopes)

            # Recurse
            for child in node.children:
                visit(child, scope_id)

        visit(tree.root_node)
        return annotations

    def _extract_typed_parameter(self, node: Node, scope_id: int | None) -> TypeAnnotationData | None:
        """Extract type from typed_parameter: name: type"""
        name_node = None
        type_node = None

        for child in node.children:
            if child.type == "identifier":
                name_node = child
            elif child.type == "type":
                type_node = child

        if not name_node or not type_node:
            return None

        name = name_node.text.decode() if name_node.text else ""
        raw_type = type_node.text.decode() if type_node.text else ""

        return TypeAnnotationData(
            target_kind="parameter",
            target_name=name,
            raw_annotation=raw_type,
            canonical_type=self._canonicalize_type(raw_type),
            base_type=self._extract_base_type(raw_type),
            is_optional="None" in raw_type or "Optional" in raw_type,
            is_array=self._is_array_type(raw_type),
            is_generic="[" in raw_type,
            scope_id=scope_id,
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
        )

    def _extract_typed_default_parameter(
        self, node: Node, scope_id: int | None
    ) -> TypeAnnotationData | None:
        """Extract type from typed_default_parameter: name: type = default"""
        name_node = None
        type_node = None

        for child in node.children:
            if child.type == "identifier" and name_node is None:
                name_node = child
            elif child.type == "type":
                type_node = child

        if not name_node or not type_node:
            return None

        name = name_node.text.decode() if name_node.text else ""
        raw_type = type_node.text.decode() if type_node.text else ""

        return TypeAnnotationData(
            target_kind="parameter",
            target_name=name,
            raw_annotation=raw_type,
            canonical_type=self._canonicalize_type(raw_type),
            base_type=self._extract_base_type(raw_type),
            is_optional="None" in raw_type or "Optional" in raw_type,
            is_array=self._is_array_type(raw_type),
            is_generic="[" in raw_type,
            scope_id=scope_id,
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
        )

    def _extract_return_annotation(
        self, node: Node, scope_id: int | None
    ) -> TypeAnnotationData | None:
        """Extract return type annotation from function definition."""
        name_node = None
        return_type_node = None

        for child in node.children:
            if child.type == "identifier" and name_node is None:
                name_node = child
            elif child.type == "type":
                return_type_node = child

        if not name_node or not return_type_node:
            return None

        name = name_node.text.decode() if name_node.text else ""
        raw_type = return_type_node.text.decode() if return_type_node.text else ""

        return TypeAnnotationData(
            target_kind="return",
            target_name=name,
            raw_annotation=raw_type,
            canonical_type=self._canonicalize_type(raw_type),
            base_type=self._extract_base_type(raw_type),
            is_optional="None" in raw_type or "Optional" in raw_type,
            is_array=self._is_array_type(raw_type),
            is_generic="[" in raw_type,
            scope_id=scope_id,
            start_line=return_type_node.start_point[0] + 1,
            start_col=return_type_node.start_point[1],
        )

    def _extract_annotated_assignment(
        self, node: Node, scope_id: int | None
    ) -> TypeAnnotationData | None:
        """Extract annotation from assignment: x: int = 5"""
        # Look for pattern: identifier : type = value
        # The left side should be an identifier or have a type annotation
        left_node = None
        type_node = None

        for child in node.children:
            if child.type == "identifier" and left_node is None:
                left_node = child
            elif child.type == "type":
                type_node = child

        if not left_node or not type_node:
            return None

        name = left_node.text.decode() if left_node.text else ""
        raw_type = type_node.text.decode() if type_node.text else ""

        return TypeAnnotationData(
            target_kind="variable",
            target_name=name,
            raw_annotation=raw_type,
            canonical_type=self._canonicalize_type(raw_type),
            base_type=self._extract_base_type(raw_type),
            is_optional="None" in raw_type or "Optional" in raw_type,
            is_array=self._is_array_type(raw_type),
            is_generic="[" in raw_type,
            scope_id=scope_id,
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
        )

    def _has_annotation(self, node: Node) -> bool:
        """Check if an assignment has a type annotation."""
        return any(child.type == "type" for child in node.children)

    def _is_array_type(self, raw_type: str) -> bool:
        """Check if type represents an array/list."""
        array_prefixes = ("list", "List", "Sequence", "Iterable", "tuple", "Tuple", "set", "Set")
        return any(raw_type.startswith(p) for p in array_prefixes)

    def extract_type_members(
        self,
        tree: Tree,
        file_path: str,
        defs: list[dict[str, Any]],
    ) -> list[TypeMemberData]:
        """Extract class members with type information."""
        members: list[TypeMemberData] = []

        # Build def_uid lookup by name and line
        def_by_name: dict[str, dict[str, Any]] = {}
        class_defs: list[dict[str, Any]] = []

        for d in defs:
            if d.get("kind") == "class":
                class_defs.append(d)
            def_by_name[d["name"]] = d

        def visit(node: Node, current_class: dict[str, Any] | None = None) -> None:
            if node.type == "class_definition":
                # Find class name
                class_name = None
                for child in node.children:
                    if child.type == "identifier":
                        class_name = child.text.decode() if child.text else ""
                        break

                if class_name and class_name in def_by_name:
                    current_class = def_by_name[class_name]

            if current_class and node.type == "function_definition":
                # This is a method
                member = self._extract_method_member(node, current_class)
                if member:
                    members.append(member)

            if current_class and node.type == "expression_statement":
                # Look for class-level annotated assignment (field)
                for child in node.children:
                    if child.type == "assignment":
                        member = self._extract_field_member(child, current_class)
                        if member:
                            members.append(member)

            # Recurse
            for child in node.children:
                visit(child, current_class)

        visit(tree.root_node)
        return members

    def _extract_method_member(
        self, node: Node, current_class: dict[str, Any]
    ) -> TypeMemberData | None:
        """Extract a method as a type member."""
        name_node = None
        decorators: list[str] = []

        for child in node.children:
            if child.type == "identifier" and name_node is None:
                name_node = child
            elif child.type == "decorator":
                dec_text = child.text.decode() if child.text else ""
                decorators.append(dec_text)

        if not name_node:
            return None

        name = name_node.text.decode() if name_node.text else ""

        # Determine member kind based on decorators
        member_kind = "method"
        is_static = False
        is_abstract = False

        for dec in decorators:
            if "@staticmethod" in dec:
                member_kind = "static_method"
                is_static = True
            elif "@classmethod" in dec:
                member_kind = "class_method"
            elif "@property" in dec:
                member_kind = "property"
            elif "@abstractmethod" in dec:
                is_abstract = True

        # Compute def_uid for the method
        method_def_uid = self._compute_member_def_uid(
            current_class, name, "method"
        )

        return TypeMemberData(
            parent_def_uid=current_class.get("def_uid", ""),
            parent_type_name=current_class.get("name", ""),
            parent_kind="class",
            member_kind=member_kind,
            member_name=name,
            member_def_uid=method_def_uid,
            visibility="private" if name.startswith("_") else "public",
            is_static=is_static,
            is_abstract=is_abstract,
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
        )

    def _extract_field_member(
        self, node: Node, current_class: dict[str, Any]
    ) -> TypeMemberData | None:
        """Extract a field as a type member from annotated assignment."""
        name_node = None
        type_node = None

        for child in node.children:
            if child.type == "identifier" and name_node is None:
                name_node = child
            elif child.type == "type":
                type_node = child

        if not name_node:
            return None

        name = name_node.text.decode() if name_node.text else ""
        type_annotation = None
        base_type = None
        canonical_type = None

        if type_node:
            type_annotation = type_node.text.decode() if type_node.text else ""
            base_type = self._extract_base_type(type_annotation)
            canonical_type = self._canonicalize_type(type_annotation)

        return TypeMemberData(
            parent_def_uid=current_class.get("def_uid", ""),
            parent_type_name=current_class.get("name", ""),
            parent_kind="class",
            member_kind="field",
            member_name=name,
            type_annotation=type_annotation,
            canonical_type=canonical_type,
            base_type=base_type,
            visibility="private" if name.startswith("_") else "public",
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
        )

    def _compute_member_def_uid(
        self, parent_class: dict[str, Any], member_name: str, kind: str
    ) -> str:
        """Compute a stable def_uid for a class member."""
        parent_uid = parent_class.get("def_uid", "")
        raw = f"{parent_uid}:{kind}:{member_name}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def extract_member_accesses(
        self,
        tree: Tree,
        file_path: str,
        scopes: list[dict[str, Any]],
        type_annotations: list[TypeAnnotationData],
    ) -> list[MemberAccessData]:
        """Extract Python member accesses."""
        accesses: list[MemberAccessData] = []

        # Build type map from annotations
        type_map: dict[tuple[str, int | None], str] = {}
        for ann in type_annotations:
            type_map[(ann.target_name, ann.scope_id)] = ann.base_type

        def visit(node: Node, scope_id: int | None = None) -> None:
            # Attribute access: obj.attr
            if node.type == "attribute":
                chain = self._build_python_access_chain(node, type_map, scope_id)
                if chain:
                    accesses.append(chain)

            # Update scope for function/class definitions
            if node.type in ("function_definition", "class_definition"):
                new_scope = self._get_scope_id_for_node(node, scopes)
                if new_scope is not None:
                    scope_id = new_scope

            # Recurse
            for child in node.children:
                visit(child, scope_id)

        visit(tree.root_node)
        return accesses

    def _build_python_access_chain(
        self,
        node: Node,
        type_map: dict[tuple[str, int | None], str],
        scope_id: int | None,
    ) -> MemberAccessData | None:
        """Build access chain from Python attribute node."""
        parts: list[str] = []
        current = node

        # Walk up to get full chain
        while current.type == "attribute":
            # Get attribute name (rightmost identifier)
            attr_node = None
            for child in current.children:
                if child.type == "identifier":
                    attr_node = child
            if attr_node:
                parts.insert(0, attr_node.text.decode() if attr_node.text else "")

            # Get object (first child)
            obj_node = current.children[0] if current.children else None
            if obj_node and obj_node.type == "attribute":
                current = obj_node
            else:
                break

        if not parts:
            return None

        # Get receiver
        receiver_node = current.children[0] if current.children else None
        if not receiver_node:
            return None

        receiver_name = ""
        if receiver_node.type == "identifier":
            receiver_name = receiver_node.text.decode() if receiver_node.text else ""
        elif receiver_node.type == "call":
            # Handle chained calls like foo().bar
            return None  # Skip for now - complex case
        else:
            return None

        # Check if this is a call expression
        is_call = node.parent and node.parent.type == "call"
        arg_count = None
        if is_call and node.parent:
            for child in node.parent.children:
                if child.type == "argument_list":
                    arg_count = sum(
                        1 for c in child.children
                        if c.type not in (",", "(", ")")
                    )
                    break

        # Look up receiver type
        receiver_type = type_map.get((receiver_name, scope_id))
        if not receiver_type:
            # Try without scope
            receiver_type = type_map.get((receiver_name, None))

        return MemberAccessData(
            access_style="dot",
            full_expression=f"{receiver_name}.{'.'.join(parts)}",
            receiver_name=receiver_name,
            member_chain=".".join(parts),
            final_member=parts[-1],
            chain_depth=len(parts),
            is_invocation=bool(is_call),
            arg_count=arg_count,
            receiver_declared_type=receiver_type,
            scope_id=scope_id,
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

    def extract_interface_impls(
        self,
        tree: Tree,
        file_path: str,
        defs: list[dict[str, Any]],
    ) -> list[InterfaceImplData]:
        """Python doesn't have explicit interface implementation."""
        return []

    def _canonicalize_type(self, raw_type: str) -> str:
        """Normalize Python type to canonical form."""
        t = raw_type.strip()

        # Normalize common aliases
        replacements = {
            "List[": "list[",
            "Dict[": "dict[",
            "Set[": "set[",
            "Tuple[": "tuple[",
            "Optional[": "opt[",
            "Union[": "union[",
        }
        for old, new in replacements.items():
            t = t.replace(old, new)

        return t
