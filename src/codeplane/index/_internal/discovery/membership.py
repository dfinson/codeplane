"""Membership and exclusion resolution for context discovery.

This module implements Phase B of SPEC.md §8.4.4: Membership & Exclusion.
It applies the "hole-punch" rule to ensure nested contexts exclude from
their parent, and assigns include/exclude specs.

Key rules:
1. Hole-punch: Nested contexts exclude from parent
2. Include spec: File extensions per name
3. Exclude spec: Universal excludes + hole-punches
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from codeplane.index._internal.discovery.scanner import INCLUDE_SPECS, UNIVERSAL_EXCLUDES
from codeplane.index.models import CandidateContext, LanguageFamily

if TYPE_CHECKING:
    pass


@dataclass
class MembershipResult:
    """Result of membership resolution."""

    contexts: list[CandidateContext] = field(default_factory=list)


class MembershipResolver:
    """
    Resolves membership and exclusion for contexts.

    Implements Phase B of SPEC.md §8.4.4.

    The key rule is "hole-punching": For every context C of name F,
    all nested contexts of the same name must be excluded from C.
    This ensures each file has exactly one owner per name.

    Usage::

        resolver = MembershipResolver()
        result = resolver.resolve(candidates)

        for ctx in result.contexts:
            print(f"{ctx.root_path}: excludes {ctx.exclude_spec}")
    """

    def resolve(self, candidates: list[CandidateContext]) -> MembershipResult:
        """
        Apply membership and exclusion rules to candidates.

        Args:
            candidates: List of candidate contexts (after authority filter)

        Returns:
            MembershipResult with updated contexts.
        """
        result = MembershipResult()

        # Group by name
        by_name: dict[LanguageFamily, list[CandidateContext]] = {}
        for c in candidates:
            if c.language_family not in by_name:
                by_name[c.language_family] = []
            by_name[c.language_family].append(c)

        # Process each name
        for name, name_contexts in by_name.items():
            resolved = self._resolve_family(name, name_contexts)
            result.contexts.extend(resolved)

        return result

    def _resolve_family(
        self, name: LanguageFamily, contexts: list[CandidateContext]
    ) -> list[CandidateContext]:
        """Resolve membership for a single name."""
        # Sort by root path depth (shallowest first)
        sorted_contexts = sorted(contexts, key=lambda c: c.root_path.count("/"))

        # Apply include specs
        include_spec = INCLUDE_SPECS.get(name, [])
        for ctx in sorted_contexts:
            if ctx.include_spec is None:
                ctx.include_spec = list(include_spec)

        # Apply hole-punch rule
        for i, parent in enumerate(sorted_contexts):
            parent_excludes = list(UNIVERSAL_EXCLUDES)

            for child in sorted_contexts[i + 1 :]:
                if self._is_inside(child.root_path, parent.root_path):
                    # Child is nested in parent - add hole-punch
                    rel_path = self._relative_to(child.root_path, parent.root_path)
                    if rel_path:
                        parent_excludes.append(f"{rel_path}/**")

            parent.exclude_spec = parent_excludes

        return sorted_contexts

    def _is_inside(self, file_path: str, root_path: str) -> bool:
        """Segment-safe containment check."""
        if root_path == "":
            return True
        if file_path == root_path:
            return True
        return file_path.startswith(root_path + "/")

    def _relative_to(self, path: str, root: str) -> str:
        """Get path relative to root."""
        if root == "":
            return path
        if path == root:
            return ""
        if path.startswith(root + "/"):
            return path[len(root) + 1 :]
        return path


def is_inside(file_path: str, root_path: str) -> bool:
    """
    Segment-safe containment check (exported for use by Router).

    From SPEC.md §8.4.1:
    - "apps" does NOT contain "apps-legacy"
    - Use path + "/" prefix check

    Args:
        file_path: Path to check
        root_path: Root path to check against ("" = repo root)

    Returns:
        True if file_path is inside root_path.
    """
    if root_path == "":
        return True
    if file_path == root_path:
        return True
    return file_path.startswith(root_path + "/")
