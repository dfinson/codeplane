"""Type extraction protocol and registry.

Defines the interface for language-specific type extractors and provides
a registry for looking up extractors by language family.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tree_sitter import Node, Tree


# =============================================================================
# Extraction Result Dataclasses
# =============================================================================


@dataclass
class TypeAnnotationData:
    """Extracted type annotation data."""

    target_kind: str  # parameter, variable, field, return
    target_name: str
    raw_annotation: str
    canonical_type: str
    base_type: str
    is_optional: bool = False
    is_array: bool = False
    is_generic: bool = False
    is_reference: bool = False
    is_mutable: bool = True
    type_args: list[str] = field(default_factory=list)
    scope_id: int | None = None
    start_line: int = 0
    start_col: int = 0


@dataclass
class TypeMemberData:
    """Extracted type member data."""

    parent_def_uid: str
    parent_type_name: str
    parent_kind: str  # class, struct, interface, trait
    member_kind: str  # field, method, property
    member_name: str
    member_def_uid: str | None = None
    type_annotation: str | None = None
    canonical_type: str | None = None
    base_type: str | None = None
    visibility: str | None = None
    is_static: bool = False
    is_abstract: bool = False
    start_line: int = 0
    start_col: int = 0


@dataclass
class MemberAccessData:
    """Extracted member access chain data."""

    access_style: str  # dot, arrow, scope
    full_expression: str
    receiver_name: str
    member_chain: str
    final_member: str
    chain_depth: int
    is_invocation: bool = False
    arg_count: int | None = None
    receiver_declared_type: str | None = None
    scope_id: int | None = None
    start_line: int = 0
    start_col: int = 0
    end_line: int = 0
    end_col: int = 0


@dataclass
class InterfaceImplData:
    """Extracted interface implementation data."""

    implementor_def_uid: str
    implementor_name: str
    interface_name: str
    interface_def_uid: str | None = None
    impl_style: str = "explicit"  # explicit, structural, inferred
    start_line: int = 0
    start_col: int = 0


@dataclass
class ReceiverShapeData:
    """Computed receiver shape data."""

    receiver_name: str
    declared_type: str | None
    observed_fields: list[str]
    observed_methods: list[str]
    scope_id: int | None = None

    @property
    def shape_hash(self) -> str:
        """Compute hash of observed shape."""
        members = sorted(self.observed_fields + self.observed_methods)
        return hashlib.sha256(json.dumps(members).encode()).hexdigest()[:16]

    @property
    def observed_members_json(self) -> str:
        """Serialize observed members to JSON."""
        return json.dumps(
            {
                "fields": sorted(self.observed_fields),
                "methods": sorted(self.observed_methods),
            }
        )


@dataclass
class TypeExtractionResult:
    """Complete extraction result from a single file."""

    file_path: str
    type_annotations: list[TypeAnnotationData] = field(default_factory=list)
    type_members: list[TypeMemberData] = field(default_factory=list)
    member_accesses: list[MemberAccessData] = field(default_factory=list)
    interface_impls: list[InterfaceImplData] = field(default_factory=list)
    receiver_shapes: list[ReceiverShapeData] = field(default_factory=list)
    error: str | None = None


# =============================================================================
# Type Extractor Protocol
# =============================================================================


@runtime_checkable
class TypeExtractor(Protocol):
    """Protocol for language-specific type extraction.

    Each language family implements this to extract type information
    from its specific syntax.
    """

    @property
    def language_family(self) -> str:
        """The LanguageFamily this extractor handles."""
        ...

    @property
    def supports_type_annotations(self) -> bool:
        """Whether this language has extractable type annotations."""
        ...

    @property
    def supports_interfaces(self) -> bool:
        """Whether this language has interface/trait concepts."""
        ...

    @property
    def access_styles(self) -> list[str]:
        """Member access styles this language uses (dot, arrow, scope)."""
        ...

    def extract_type_annotations(
        self,
        tree: Tree,
        file_path: str,
        scopes: list[dict[str, Any]],
    ) -> list[TypeAnnotationData]:
        """Extract type annotations from the AST."""
        ...

    def extract_type_members(
        self,
        tree: Tree,
        file_path: str,
        defs: list[dict[str, Any]],
    ) -> list[TypeMemberData]:
        """Extract class/struct members with type info."""
        ...

    def extract_member_accesses(
        self,
        tree: Tree,
        file_path: str,
        scopes: list[dict[str, Any]],
        type_annotations: list[TypeAnnotationData],
    ) -> list[MemberAccessData]:
        """Extract member access chains."""
        ...

    def extract_interface_impls(
        self,
        tree: Tree,
        file_path: str,
        defs: list[dict[str, Any]],
    ) -> list[InterfaceImplData]:
        """Extract interface/trait implementations."""
        ...


# =============================================================================
# Base Extractor Implementation
# =============================================================================


class BaseTypeExtractor(ABC):
    """Base class for type extractors with common utilities."""

    @property
    @abstractmethod
    def language_family(self) -> str:
        """The LanguageFamily this extractor handles."""
        ...

    @property
    def supports_type_annotations(self) -> bool:
        """Override in subclass if language has type annotations."""
        return False

    @property
    def supports_interfaces(self) -> bool:
        """Override in subclass if language has interfaces/traits."""
        return False

    @property
    def access_styles(self) -> list[str]:
        """Default to dot access."""
        return ["dot"]

    def extract_type_annotations(
        self,
        tree: Tree,  # noqa: ARG002
        file_path: str,  # noqa: ARG002
        scopes: list[dict[str, Any]],  # noqa: ARG002
    ) -> list[TypeAnnotationData]:
        """Default: no type annotations."""
        return []

    def extract_type_members(
        self,
        tree: Tree,  # noqa: ARG002
        file_path: str,  # noqa: ARG002
        defs: list[dict[str, Any]],  # noqa: ARG002
    ) -> list[TypeMemberData]:
        """Default: no type members."""
        return []

    def extract_member_accesses(
        self,
        tree: Tree,
        file_path: str,  # noqa: ARG002
        scopes: list[dict[str, Any]],
        type_annotations: list[TypeAnnotationData],
    ) -> list[MemberAccessData]:
        """Extract member accesses - implemented in base class."""
        return self._extract_dot_accesses(tree, scopes, type_annotations)

    def extract_interface_impls(
        self,
        tree: Tree,  # noqa: ARG002
        file_path: str,  # noqa: ARG002
        defs: list[dict[str, Any]],  # noqa: ARG002
    ) -> list[InterfaceImplData]:
        """Default: no interface implementations."""
        return []

    def _extract_dot_accesses(
        self,
        tree: Tree,
        scopes: list[dict[str, Any]],
        type_annotations: list[TypeAnnotationData],
    ) -> list[MemberAccessData]:
        """Extract dot-style member accesses from AST.

        This is generic enough to work for most languages.
        """
        accesses: list[MemberAccessData] = []

        # Build type map from annotations for receiver type lookup
        type_map: dict[tuple[str, int | None], str] = {}
        for ann in type_annotations:
            type_map[(ann.target_name, ann.scope_id)] = ann.base_type

        def visit(node: Node, scope_id: int | None = None) -> None:
            # Check if this is an attribute/member access
            if node.type in ("attribute", "member_expression", "field_expression"):
                chain = self._build_access_chain(node)
                if chain:
                    receiver_type = type_map.get((chain.receiver_name, scope_id))
                    chain.receiver_declared_type = receiver_type
                    chain.scope_id = scope_id
                    accesses.append(chain)

            # Update scope when entering scope-creating nodes
            new_scope = self._get_scope_id_for_node(node, scopes)
            if new_scope is not None:
                scope_id = new_scope

            # Recurse
            for child in node.children:
                visit(child, scope_id)

        visit(tree.root_node)
        return accesses

    def _build_access_chain(self, node: Node) -> MemberAccessData | None:
        """Build full access chain from an attribute node."""
        parts: list[str] = []
        current = node

        # Walk up to get full chain
        while current.type in ("attribute", "member_expression", "field_expression"):
            # Get the member name (rightmost child that's an identifier)
            member_node = None
            for child in current.children:
                if (
                    child.type in ("identifier", "property_identifier", "field_identifier")
                    and child != current.children[0]
                ):  # Not the object
                    member_node = child
                    break
            if member_node:
                parts.insert(0, member_node.text.decode() if member_node.text else "")

            # Move to object (receiver)
            obj_node = current.children[0] if current.children else None
            if obj_node and obj_node.type in ("attribute", "member_expression", "field_expression"):
                current = obj_node
            else:
                break

        if not parts:
            return None

        # Get the root receiver
        receiver_node = current.children[0] if current.children else None
        if not receiver_node:
            return None

        receiver_name = receiver_node.text.decode() if receiver_node.text else ""

        # Check if this is a call expression
        is_call = node.parent and node.parent.type in ("call", "call_expression")
        arg_count = None
        if is_call and node.parent:
            args_node = None
            for child in node.parent.children:
                if child.type in ("arguments", "argument_list"):
                    args_node = child
                    break
            if args_node:
                arg_count = sum(1 for c in args_node.children if c.type not in (",", "(", ")"))

        return MemberAccessData(
            access_style="dot",
            full_expression=f"{receiver_name}.{'.'.join(parts)}",
            receiver_name=receiver_name,
            member_chain=".".join(parts),
            final_member=parts[-1],
            chain_depth=len(parts),
            is_invocation=bool(is_call),
            arg_count=arg_count,
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

    def _get_scope_id_for_node(self, node: Node, scopes: list[dict[str, Any]]) -> int | None:
        """Find the scope_id for a node based on line/column position."""
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

    def _canonicalize_type(self, raw_type: str) -> str:
        """Normalize type annotation to canonical form.

        Override in language-specific extractors for proper normalization.
        """
        return raw_type.strip()

    def _extract_base_type(self, raw_type: str) -> str:
        """Extract base type from annotation (strip generics, optionals, etc.)."""
        # Remove common wrappers
        t = raw_type.strip()

        # Handle Optional[X], X | None, X?
        if t.startswith("Optional[") and t.endswith("]"):
            t = t[9:-1]
        if " | None" in t:
            t = t.replace(" | None", "").strip()
        if t.endswith("?"):
            t = t[:-1]

        # Handle List[X], Dict[K, V], etc.
        bracket_idx = t.find("[")
        if bracket_idx > 0:
            t = t[:bracket_idx]

        # Handle generics with <>
        angle_idx = t.find("<")
        if angle_idx > 0:
            t = t[:angle_idx]

        return t.strip()


# =============================================================================
# Shape-Only Extractor (Fallback)
# =============================================================================


class ShapeOnlyExtractor(BaseTypeExtractor):
    """Extractor for languages without type annotation support.

    Only extracts member accesses for shape inference.
    """

    @property
    def language_family(self) -> str:
        return "unknown"

    @property
    def supports_type_annotations(self) -> bool:
        return False

    @property
    def supports_interfaces(self) -> bool:
        return False


# =============================================================================
# Extractor Registry
# =============================================================================


class ExtractorRegistry:
    """Registry of language-specific type extractors."""

    def __init__(self) -> None:
        self._extractors: dict[str, BaseTypeExtractor] = {}
        self._fallback = ShapeOnlyExtractor()

    def register(self, extractor: BaseTypeExtractor) -> None:
        """Register an extractor for its language family."""
        self._extractors[extractor.language_family] = extractor

    def get(self, language_family: str) -> BaseTypeExtractor | None:
        """Get extractor for language family, or None."""
        return self._extractors.get(language_family)

    def get_or_fallback(self, language_family: str) -> BaseTypeExtractor:
        """Get extractor for language family, or fallback."""
        return self._extractors.get(language_family, self._fallback)

    def supported_languages(self) -> list[str]:
        """List all supported language families."""
        return list(self._extractors.keys())


# Global registry instance
_registry: ExtractorRegistry | None = None


def get_registry() -> ExtractorRegistry:
    """Get the global extractor registry, initializing if needed."""
    global _registry
    if _registry is None:
        _registry = ExtractorRegistry()
        _register_builtin_extractors(_registry)
    return _registry


def _register_builtin_extractors(registry: ExtractorRegistry) -> None:
    """Register all built-in extractors."""
    # Import here to avoid circular imports
    from codeplane.index._internal.extraction.languages import ALL_LANGUAGE_CONFIGS
    from codeplane.index._internal.extraction.query_based import QueryBasedExtractor

    # Register query-based extractors for all configured languages
    for config in set(ALL_LANGUAGE_CONFIGS.values()):
        try:
            extractor = QueryBasedExtractor(config)
            registry.register(extractor)
        except ValueError:
            # Grammar not available - skip this language
            pass
