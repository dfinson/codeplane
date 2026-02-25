"""Query-based type extraction infrastructure.

Provides a declarative, query-driven approach to type extraction that is:
- More maintainable than manual AST traversal
- Less brittle to grammar version changes
- Significantly less code per language

Each language defines extraction patterns in .scm query files.
The QueryBasedExtractor executes these queries and normalizes results.
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
from codeplane.index._internal.parsing.packs import TypeExtractionConfig

if TYPE_CHECKING:
    from tree_sitter import Language, Node, Query, Tree


# =============================================================================
# Query-Based Extractor
# =============================================================================


class QueryBasedExtractor(BaseTypeExtractor):
    """Type extractor driven by tree-sitter queries.

    Instead of manual AST traversal, this uses declarative query patterns
    to extract type information. Each language provides a TypeExtractionConfig
    (from packs.py) with query strings and configuration.
    """

    def __init__(self, config: TypeExtractionConfig, grammar_name: str):
        self._config = config
        self._grammar_name = grammar_name
        self._queries: dict[str, Query] = {}
        self._language: Language | None = None

    @property
    def language_family(self) -> str:
        return self._config.language_family

    @property
    def supports_type_annotations(self) -> bool:
        return self._config.supports_type_annotations

    @property
    def supports_interfaces(self) -> bool:
        return self._config.supports_interfaces

    @property
    def access_styles(self) -> list[str]:
        return list(self._config.access_styles)

    def _get_language(self) -> Language:
        """Get the tree-sitter Language object using pack metadata."""
        if self._language is None:
            import importlib

            import tree_sitter

            from codeplane.index._internal.parsing.packs import get_pack

            pack = get_pack(self._grammar_name)
            if pack is None:
                raise ValueError(f"Unknown grammar: {self._grammar_name}")

            try:
                lang_module = importlib.import_module(pack.grammar_module)
                func_name = pack.language_func or "language"
                self._language = tree_sitter.Language(getattr(lang_module, func_name)())
            except ImportError as err:
                raise ValueError(f"Grammar not installed: {self._grammar_name}") from err

        return self._language

    def _get_query(self, query_string: str) -> Query | None:
        """Compile and cache a query."""
        if not query_string.strip():
            return None

        if query_string not in self._queries:
            try:
                from tree_sitter import Query

                lang = self._get_language()
                self._queries[query_string] = Query(lang, query_string)
            except Exception:
                # Query compilation failed - grammar mismatch
                return None

        return self._queries.get(query_string)

    def _run_query(self, query_string: str, tree: Tree) -> list[dict[str, Node]]:
        """Execute a query and return captures grouped by match."""
        query = self._get_query(query_string)
        if not query:
            return []

        from tree_sitter import QueryCursor

        cursor = QueryCursor(query)
        # matches() returns list of (pattern_index, captures_dict) tuples
        raw_matches = cursor.matches(tree.root_node)

        results: list[dict[str, Node]] = []
        for _pattern_idx, captures_dict in raw_matches:
            # captures_dict is dict[str, list[Node]] - flatten to single nodes
            match: dict[str, Node] = {}
            for capture_name, nodes in captures_dict.items():
                if nodes:  # Take first node for each capture
                    match[capture_name] = nodes[0]
            if match:
                results.append(match)

        return results

    def _is_descendant_of(self, node: Node, potential_ancestor: Node) -> bool:
        """Check if node is a descendant of potential_ancestor."""
        current = node.parent
        while current:
            if current == potential_ancestor:
                return True
            current = current.parent
        return False

    def extract_type_annotations(
        self,
        tree: Tree,
        file_path: str,  # noqa: ARG002
        scopes: list[dict[str, Any]],
    ) -> list[TypeAnnotationData]:
        """Extract type annotations using queries."""
        if not self._config.type_annotation_query:
            return []

        annotations: list[TypeAnnotationData] = []
        matches = self._run_query(self._config.type_annotation_query, tree)

        for match in matches:
            ann = self._match_to_annotation(match, scopes)
            if ann:
                annotations.append(ann)

        return annotations

    def _match_to_annotation(
        self, match: dict[str, Node], scopes: list[dict[str, Any]]
    ) -> TypeAnnotationData | None:
        """Convert a query match to TypeAnnotationData."""
        name_node = match.get("name")
        type_node = match.get("type")
        kind_node = match.get("kind")  # Optional: parameter, variable, return, field

        if not name_node:
            return None

        name = self._node_text(name_node)
        raw_type = self._node_text(type_node) if type_node else "any"

        # Determine target kind
        target_kind = "variable"
        if kind_node:
            kind_text = self._node_text(kind_node)
            if "param" in kind_text.lower():
                target_kind = "parameter"
            elif "return" in kind_text.lower():
                target_kind = "return"
            elif "field" in kind_text.lower() or "property" in kind_text.lower():
                target_kind = "field"
        elif match.get("param"):
            target_kind = "parameter"
        elif match.get("return"):
            target_kind = "return"
        elif match.get("field"):
            target_kind = "field"

        scope_id = self._find_scope_id(name_node, scopes)

        return TypeAnnotationData(
            target_kind=target_kind,
            target_name=name,
            raw_annotation=raw_type,
            canonical_type=self._canonicalize_type(raw_type),
            base_type=self._extract_base_type(raw_type),
            is_optional=self._is_optional(raw_type),
            is_array=self._is_array(raw_type),
            is_generic=self._config.generic_indicator in raw_type,
            is_reference=bool(
                self._config.reference_indicator and self._config.reference_indicator in raw_type
            ),
            scope_id=scope_id,
            start_line=name_node.start_point[0] + 1,
            start_col=name_node.start_point[1],
        )

    def extract_type_members(
        self,
        tree: Tree,
        file_path: str,  # noqa: ARG002
        defs: list[dict[str, Any]],
    ) -> list[TypeMemberData]:
        """Extract type members using queries."""
        if not self._config.type_member_query:
            return []

        members: list[TypeMemberData] = []
        matches = self._run_query(self._config.type_member_query, tree)

        def_by_name: dict[str, dict[str, Any]] = {d["name"]: d for d in defs}

        for match in matches:
            member = self._match_to_member(match, def_by_name)
            if member:
                members.append(member)

        return members

    def _match_to_member(
        self, match: dict[str, Node], def_by_name: dict[str, dict[str, Any]]
    ) -> TypeMemberData | None:
        """Convert a query match to TypeMemberData."""
        parent_node = match.get("parent")
        member_node = match.get("member") or match.get("name")
        type_node = match.get("type")
        kind_node = match.get("kind")

        if not parent_node or not member_node:
            return None

        parent_name = self._node_text(parent_node)
        member_name = self._node_text(member_node)
        parent_def = def_by_name.get(parent_name)

        if not parent_def:
            return None

        raw_type = self._node_text(type_node) if type_node else None

        # Determine member kind
        member_kind = "field"
        if kind_node:
            kind_text = self._node_text(kind_node)
            if "method" in kind_text.lower():
                member_kind = "method"
            elif "property" in kind_text.lower():
                member_kind = "property"
            elif "constructor" in kind_text.lower():
                member_kind = "constructor"
        elif match.get("method"):
            member_kind = "method"

        # Determine visibility
        visibility = "public"
        vis_node = match.get("visibility")
        if vis_node:
            vis_text = self._node_text(vis_node).lower()
            if "private" in vis_text:
                visibility = "private"
            elif "protected" in vis_text:
                visibility = "protected"
            elif "internal" in vis_text:
                visibility = "internal"
        elif member_name.startswith("_"):
            visibility = "private"

        # Check for static
        is_static = bool(match.get("static"))

        # Determine parent kind
        parent_kind = "class"
        parent_kind_node = match.get("parent_kind")
        if parent_kind_node:
            pk_text = self._node_text(parent_kind_node).lower()
            if "struct" in pk_text:
                parent_kind = "struct"
            elif "interface" in pk_text:
                parent_kind = "interface"
            elif "trait" in pk_text:
                parent_kind = "trait"
            elif "enum" in pk_text:
                parent_kind = "enum"

        return TypeMemberData(
            parent_def_uid=parent_def.get("def_uid", ""),
            parent_type_name=parent_name,
            parent_kind=parent_kind,
            member_kind=member_kind,
            member_name=member_name,
            member_def_uid=self._compute_member_def_uid(parent_def, member_name, member_kind),
            type_annotation=raw_type,
            canonical_type=self._canonicalize_type(raw_type) if raw_type else None,
            base_type=self._extract_base_type(raw_type) if raw_type else None,
            visibility=visibility,
            is_static=is_static,
            start_line=member_node.start_point[0] + 1,
            start_col=member_node.start_point[1],
        )

    def extract_member_accesses(
        self,
        tree: Tree,
        file_path: str,  # noqa: ARG002
        scopes: list[dict[str, Any]],
        type_annotations: list[TypeAnnotationData],
    ) -> list[MemberAccessData]:
        """Extract member accesses using queries or fallback traversal."""
        if self._config.member_access_query:
            return self._extract_accesses_via_query(tree, scopes, type_annotations)
        else:
            # Fallback to base class traversal
            return self._extract_dot_accesses(tree, scopes, type_annotations)

    def _extract_accesses_via_query(
        self,
        tree: Tree,
        scopes: list[dict[str, Any]],
        type_annotations: list[TypeAnnotationData],
    ) -> list[MemberAccessData]:
        """Extract member accesses using query."""
        accesses: list[MemberAccessData] = []
        matches = self._run_query(self._config.member_access_query, tree)

        type_map: dict[tuple[str, int | None], str] = {}
        for ann in type_annotations:
            type_map[(ann.target_name, ann.scope_id)] = ann.base_type

        for match in matches:
            access = self._match_to_access(match, type_map, scopes)
            if access:
                accesses.append(access)

        return accesses

    def _match_to_access(
        self,
        match: dict[str, Node],
        type_map: dict[tuple[str, int | None], str],
        scopes: list[dict[str, Any]],
    ) -> MemberAccessData | None:
        """Convert a query match to MemberAccessData."""
        receiver_node = match.get("receiver")
        member_node = match.get("member")
        expr_node = match.get("expr")

        if not receiver_node or not member_node:
            return None

        receiver_name = self._node_text(receiver_node)
        member_name = self._node_text(member_node)
        full_expr = self._node_text(expr_node) if expr_node else f"{receiver_name}.{member_name}"

        scope_id = self._find_scope_id(receiver_node, scopes)
        receiver_type = type_map.get((receiver_name, scope_id))
        if not receiver_type:
            receiver_type = type_map.get((receiver_name, None))

        # Check if invocation
        is_call = bool(match.get("call"))
        arg_count = None
        if is_call:
            args_node = match.get("args")
            if args_node:
                arg_count = sum(1 for c in args_node.children if c.type not in (",", "(", ")"))

        # Determine access style
        access_style = "dot"
        if match.get("arrow"):
            access_style = "arrow"
        elif match.get("scope"):
            access_style = "scope"

        node = expr_node or member_node

        return MemberAccessData(
            access_style=access_style,
            full_expression=full_expr,
            receiver_name=receiver_name,
            member_chain=member_name,
            final_member=member_name.split(".")[-1] if "." in member_name else member_name,
            chain_depth=member_name.count(".") + 1,
            is_invocation=is_call,
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
        file_path: str,  # noqa: ARG002
        defs: list[dict[str, Any]],
    ) -> list[InterfaceImplData]:
        """Extract interface implementations using queries."""
        if not self._config.interface_impl_query:
            return []

        impls: list[InterfaceImplData] = []
        matches = self._run_query(self._config.interface_impl_query, tree)

        def_by_name: dict[str, dict[str, Any]] = {d["name"]: d for d in defs}

        for match in matches:
            impl = self._match_to_impl(match, def_by_name)
            if impl:
                impls.append(impl)

        return impls

    def _match_to_impl(
        self, match: dict[str, Node], def_by_name: dict[str, dict[str, Any]]
    ) -> InterfaceImplData | None:
        """Convert a query match to InterfaceImplData."""
        impl_node = match.get("implementor")
        iface_node = match.get("interface")

        if not impl_node or not iface_node:
            return None

        impl_name = self._node_text(impl_node)
        iface_name = self._node_text(iface_node)
        impl_def = def_by_name.get(impl_name)

        if not impl_def:
            return None

        return InterfaceImplData(
            implementor_def_uid=impl_def.get("def_uid", ""),
            implementor_name=impl_name,
            interface_name=iface_name,
            interface_def_uid=def_by_name.get(self._extract_base_type(iface_name), {}).get(
                "def_uid"
            ),
            impl_style="explicit",
            start_line=impl_node.start_point[0] + 1,
            start_col=impl_node.start_point[1],
        )

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _node_text(self, node: Node | None) -> str:
        """Extract text from a node."""
        if not node or not node.text:
            return ""
        return node.text.decode()

    def _find_scope_id(self, node: Node, scopes: list[dict[str, Any]]) -> int | None:
        """Find the scope_id for a node position."""
        line = node.start_point[0] + 1
        col = node.start_point[1]

        for scope in scopes:
            if (
                scope["start_line"] <= line <= scope["end_line"]
                and (scope["start_line"] < line or scope["start_col"] <= col)
                and (scope["end_line"] > line or scope["end_col"] >= col)
            ):
                return scope.get("local_scope_id") or scope.get("scope_id")
        return None

    def _compute_member_def_uid(self, parent: dict[str, Any], member_name: str, kind: str) -> str:
        """Compute stable def_uid for a member."""
        parent_uid = parent.get("def_uid", "")
        raw = f"{parent_uid}:{kind}:{member_name}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _is_optional(self, raw_type: str) -> bool:
        """Check if type is optional/nullable."""
        return any(pattern in raw_type for pattern in self._config.optional_patterns)

    def _is_array(self, raw_type: str) -> bool:
        """Check if type is an array/list type."""
        return any(pattern in raw_type for pattern in self._config.array_patterns)

    def _canonicalize_type(self, raw_type: str) -> str:
        """Normalize type annotation."""
        return raw_type.strip()

    def _extract_base_type(self, raw_type: str) -> str:
        """Extract base type from annotation."""
        t = raw_type.strip()
        # Remove reference indicators
        if self._config.reference_indicator:
            t = t.lstrip(self._config.reference_indicator)
        # Remove generic parameters
        if self._config.generic_indicator in t:
            t = t[: t.index(self._config.generic_indicator)]
        return t.strip()
