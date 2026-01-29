"""Symbol graph operations - reference resolution and call graph."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlmodel import col, select

from codeplane.index.models import File, Occurrence, Symbol, SymbolEdge

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlmodel import Session


@dataclass
class SymbolNode:
    """Node in the symbol graph."""

    symbol_id: int
    name: str
    qualified_name: str
    kind: str
    file_id: int
    line: int

    # Relationships (populated by queries)
    definitions: list[SymbolNode] = field(default_factory=list)
    references: list[SymbolNode] = field(default_factory=list)
    callers: list[SymbolNode] = field(default_factory=list)
    callees: list[SymbolNode] = field(default_factory=list)


@dataclass
class CallPath:
    """A path in the call graph."""

    nodes: list[SymbolNode]
    depth: int


class SymbolGraph:
    """
    Symbol graph for reference resolution and call graph analysis.

    Uses the SymbolEdge table to navigate relationships between symbols.
    Supports:
    - Find definitions of a symbol
    - Find all references to a symbol
    - Call graph traversal (callers/callees)
    - Transitive closure for impact analysis
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_symbol(
        self, name: str, *, context_id: int | None = None, kind: str | None = None
    ) -> list[Symbol]:
        """Find symbols by name with optional filters."""
        stmt = select(Symbol).where(Symbol.name == name)

        if context_id is not None:
            stmt = stmt.where(Symbol.context_id == context_id)
        if kind is not None:
            stmt = stmt.where(Symbol.kind == kind)

        return list(self._session.exec(stmt).all())

    def find_by_qualified_name(
        self, qualified_name: str, *, context_id: int | None = None
    ) -> Symbol | None:
        """Find a symbol by its fully qualified name."""
        stmt = select(Symbol).where(Symbol.qualified_name == qualified_name)

        if context_id is not None:
            stmt = stmt.where(Symbol.context_id == context_id)

        return self._session.exec(stmt).first()

    def get_definitions(self, symbol_id: int) -> list[Symbol]:
        """Get all definitions for a symbol (via edges or same qualified name)."""
        symbol = self._session.get(Symbol, symbol_id)
        if not symbol:
            return []

        results: list[Symbol] = []

        # Find via edges (definition relationships) - two-step query
        edge_stmt = select(SymbolEdge.dst_symbol_id).where(
            SymbolEdge.src_symbol_id == symbol_id,
            SymbolEdge.relation == "definition",
        )
        dst_ids = list(self._session.exec(edge_stmt))
        if dst_ids:
            sym_stmt = select(Symbol).where(col(Symbol.id).in_(dst_ids))
            results.extend(self._session.exec(sym_stmt).all())

        # Also find symbols with same qualified name
        if symbol.qualified_name:
            qname_stmt = (
                select(Symbol)
                .where(Symbol.qualified_name == symbol.qualified_name)
                .where(Symbol.id != symbol_id)
            )
            qname_results = list(self._session.exec(qname_stmt).all())

            seen_ids = {s.id for s in results}
            for s in qname_results:
                if s.id not in seen_ids:
                    results.append(s)

        return results

    def get_references(self, symbol_id: int) -> list[Occurrence]:
        """Get all occurrences that reference this symbol."""
        stmt = (
            select(Occurrence)
            .where(Occurrence.symbol_id == symbol_id)
            .where(Occurrence.role == "reference")
        )
        return list(self._session.exec(stmt).all())

    def get_callers(self, symbol_id: int) -> list[Symbol]:
        """Get symbols that call this symbol."""
        # Two-step: get edge source IDs, then fetch symbols
        edge_stmt = select(SymbolEdge.src_symbol_id).where(
            SymbolEdge.dst_symbol_id == symbol_id,
            SymbolEdge.relation == "calls",
        )
        src_ids = list(self._session.exec(edge_stmt))
        if not src_ids:
            return []

        sym_stmt = select(Symbol).where(col(Symbol.id).in_(src_ids))
        return list(self._session.exec(sym_stmt).all())

    def get_callees(self, symbol_id: int) -> list[Symbol]:
        """Get symbols that this symbol calls."""
        edge_stmt = select(SymbolEdge.dst_symbol_id).where(
            SymbolEdge.src_symbol_id == symbol_id,
            SymbolEdge.relation == "calls",
        )
        dst_ids = list(self._session.exec(edge_stmt))
        if not dst_ids:
            return []

        sym_stmt = select(Symbol).where(col(Symbol.id).in_(dst_ids))
        return list(self._session.exec(sym_stmt).all())

    def get_implementors(self, symbol_id: int) -> list[Symbol]:
        """Get symbols that implement this symbol (interface/trait implementations)."""
        edge_stmt = select(SymbolEdge.src_symbol_id).where(
            SymbolEdge.dst_symbol_id == symbol_id,
            SymbolEdge.relation == "implements",
        )
        src_ids = list(self._session.exec(edge_stmt))
        if not src_ids:
            return []

        sym_stmt = select(Symbol).where(col(Symbol.id).in_(src_ids))
        return list(self._session.exec(sym_stmt).all())

    def get_type_hierarchy(self, symbol_id: int, *, direction: str = "up") -> list[Symbol]:
        """
        Get type hierarchy (inheritance chain).

        Args:
            symbol_id: Starting symbol
            direction: 'up' for supertypes, 'down' for subtypes
        """
        if direction == "up":
            # Get what this symbol extends/implements
            edge_stmt = select(SymbolEdge.dst_symbol_id).where(
                SymbolEdge.src_symbol_id == symbol_id,
                col(SymbolEdge.relation).in_(["extends", "implements"]),
            )
            target_ids = list(self._session.exec(edge_stmt))
        else:
            # Get what extends/implements this symbol
            edge_stmt = select(SymbolEdge.src_symbol_id).where(
                SymbolEdge.dst_symbol_id == symbol_id,
                col(SymbolEdge.relation).in_(["extends", "implements"]),
            )
            target_ids = list(self._session.exec(edge_stmt))

        if not target_ids:
            return []

        sym_stmt = select(Symbol).where(col(Symbol.id).in_(target_ids))
        return list(self._session.exec(sym_stmt).all())

    def find_call_paths(
        self, from_symbol_id: int, to_symbol_id: int, *, max_depth: int = 5
    ) -> list[CallPath]:
        """Find call paths between two symbols using BFS."""
        paths: list[CallPath] = []
        queue: list[tuple[int, list[int]]] = [(from_symbol_id, [from_symbol_id])]
        visited: set[tuple[int, ...]] = set()

        while queue:
            current_id, path = queue.pop(0)

            if len(path) > max_depth:
                continue

            path_tuple = tuple(path)
            if path_tuple in visited:
                continue
            visited.add(path_tuple)

            if current_id == to_symbol_id and len(path) > 1:
                nodes = []
                for sid in path:
                    symbol = self._session.get(Symbol, sid)
                    if symbol:
                        nodes.append(self._symbol_to_node(symbol))
                if len(nodes) == len(path):
                    paths.append(CallPath(nodes=nodes, depth=len(nodes) - 1))
                continue

            for callee in self.get_callees(current_id):
                if callee.id is not None and callee.id not in path:
                    queue.append((callee.id, [*path, callee.id]))

        return paths

    def impact_analysis(self, symbol_id: int, *, max_depth: int = 3) -> dict[int, set[int]]:
        """Compute transitive closure of callers for impact analysis."""
        affected: dict[int, set[int]] = defaultdict(set)
        seen: set[int] = {symbol_id}
        current_level: set[int] = {symbol_id}

        for depth in range(1, max_depth + 1):
            next_level: set[int] = set()

            for sid in current_level:
                callers = self.get_callers(sid)

                for s in callers:
                    if s.id is not None and s.id not in seen:
                        affected[depth].add(s.id)
                        next_level.add(s.id)
                        seen.add(s.id)

            if not next_level:
                break
            current_level = next_level

        return dict(affected)

    def symbols_in_file(self, file_id: int, *, context_id: int | None = None) -> list[Symbol]:
        """Get all symbols in a file."""
        stmt = select(Symbol).where(Symbol.file_id == file_id)

        if context_id is not None:
            stmt = stmt.where(Symbol.context_id == context_id)

        return list(self._session.exec(stmt).all())

    def symbol_at_location(
        self, file_id: int, line: int, column: int | None = None
    ) -> Symbol | None:
        """Find symbol at a specific location."""
        stmt = select(Symbol).where(Symbol.file_id == file_id, Symbol.line == line)

        if column is not None:
            stmt = stmt.where(Symbol.column == column)

        return self._session.exec(stmt).first()

    def get_file_path(self, file_id: int) -> str | None:
        """Resolve file_id to path."""
        file = self._session.get(File, file_id)
        return file.path if file else None

    def iter_all_edges(self, *, relation: str | None = None) -> Iterator[SymbolEdge]:
        """Iterate all edges with optional relation filter."""
        stmt = select(SymbolEdge)

        if relation is not None:
            stmt = stmt.where(SymbolEdge.relation == relation)

        yield from self._session.exec(stmt)

    def get_statistics(self, context_id: int | None = None) -> dict[str, int]:
        """Get graph statistics."""
        # Count symbols
        sym_stmt = select(Symbol)
        if context_id is not None:
            sym_stmt = sym_stmt.where(Symbol.context_id == context_id)
        total_symbols = len(list(self._session.exec(sym_stmt)))

        # Count edges
        edge_stmt = select(SymbolEdge)
        total_edges = len(list(self._session.exec(edge_stmt)))

        # Count definition occurrences
        def_stmt = select(Occurrence).where(Occurrence.role == "definition")
        if context_id is not None:
            def_stmt = def_stmt.where(Occurrence.context_id == context_id)
        definitions = len(list(self._session.exec(def_stmt)))

        # Count reference occurrences
        ref_stmt = select(Occurrence).where(Occurrence.role == "reference")
        if context_id is not None:
            ref_stmt = ref_stmt.where(Occurrence.context_id == context_id)
        references = len(list(self._session.exec(ref_stmt)))

        return {
            "total_symbols": total_symbols,
            "total_edges": total_edges,
            "definitions": definitions,
            "references": references,
        }

    def _symbol_to_node(self, symbol: Symbol) -> SymbolNode:
        """Convert Symbol to SymbolNode."""
        return SymbolNode(
            symbol_id=symbol.id if symbol.id is not None else 0,
            name=symbol.name,
            qualified_name=symbol.qualified_name or symbol.name,
            kind=symbol.kind,
            file_id=symbol.file_id,
            line=symbol.line,
        )
