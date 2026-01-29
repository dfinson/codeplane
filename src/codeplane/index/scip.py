"""SCIP (Source Code Index Protocol) parsing and integration.

This module handles parsing SCIP index files produced by external indexers
and populating the database with semantic information (symbols, references,
edges).

SCIP provides:
- Symbol definitions with full type information
- Cross-file references (binding identifiers to definitions)
- Export/import relationships
- Symbol documentation
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from codeplane.index.models import (
    Certainty,
    LanguageFamily,
    Layer,
    Occurrence,
    Role,
    Symbol,
    SymbolEdge,
)
from codeplane.index.tools import TOOL_RECIPES, ToolManager, ToolRecipe


@dataclass
class SCIPSymbol:
    """A symbol extracted from SCIP index."""

    symbol: str  # SCIP symbol string (unique identifier)
    display_name: str
    kind: str  # function, class, method, variable, etc.
    signature: str | None = None
    documentation: str | None = None
    file_path: str | None = None
    line: int = 0
    column: int = 0
    end_line: int = 0
    end_column: int = 0


@dataclass
class SCIPOccurrence:
    """A symbol occurrence from SCIP index."""

    symbol: str  # SCIP symbol string
    file_path: str
    line: int
    column: int
    end_line: int
    end_column: int
    role: Role  # definition, reference, import


@dataclass
class SCIPRelation:
    """A relationship between symbols from SCIP index."""

    from_symbol: str
    to_symbol: str
    relation_type: str  # calls, extends, implements, etc.


@dataclass
class SCIPDocument:
    """A parsed SCIP index document."""

    language: str
    relative_path: str
    symbols: list[SCIPSymbol] = field(default_factory=list)
    occurrences: list[SCIPOccurrence] = field(default_factory=list)
    relations: list[SCIPRelation] = field(default_factory=list)


@dataclass
class SCIPIndex:
    """Complete SCIP index for a project."""

    documents: list[SCIPDocument] = field(default_factory=list)
    external_symbols: list[SCIPSymbol] = field(default_factory=list)


@dataclass
class IndexerResult:
    """Result of running a SCIP indexer."""

    success: bool
    scip_path: Path | None = None
    error: str | None = None
    duration_ms: int = 0


class SCIPRunner:
    """
    Runs external SCIP indexers to produce index files.

    This class handles the invocation of language-specific SCIP indexers
    (scip-python, scip-go, etc.) and returns the path to the produced
    .scip file.

    Usage::

        runner = SCIPRunner(tool_manager)

        # Run indexer for a context
        result = runner.run(
            family=LanguageFamily.PYTHON,
            project_root=Path("/path/to/project"),
            output_dir=Path("/tmp/scip"),
        )

        if result.success:
            parser = SCIPParser()
            index = parser.parse(result.scip_path)
    """

    def __init__(self, tool_manager: ToolManager | None = None):
        self.tool_manager = tool_manager or ToolManager()

    def run(
        self,
        family: LanguageFamily,
        project_root: Path,
        output_dir: Path | None = None,
        timeout: int = 300,
    ) -> IndexerResult:
        """
        Run the SCIP indexer for a language family.

        Args:
            family: Language family to index
            project_root: Root directory of the project
            output_dir: Directory to write .scip file (default: temp dir)
            timeout: Maximum execution time in seconds

        Returns:
            IndexerResult with path to .scip file or error.
        """
        start = time.monotonic()

        # Check tool availability (fail-fast)
        if not self.tool_manager.is_available(family):
            return IndexerResult(
                success=False,
                error=f"Tool not available for {family.value}",
            )

        recipe = TOOL_RECIPES.get(family)
        if recipe is None:
            return IndexerResult(
                success=False,
                error=f"No recipe for {family.value}",
            )

        # Prepare output directory
        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="scip_"))
        output_dir.mkdir(parents=True, exist_ok=True)
        scip_path = output_dir / "index.scip"

        # Build command
        cmd = self._build_command(recipe, project_root, scip_path)
        if cmd is None:
            return IndexerResult(
                success=False,
                error=f"Cannot build command for {recipe.name}",
            )

        # Run indexer
        try:
            result = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            duration_ms = int((time.monotonic() - start) * 1000)

            if result.returncode != 0:
                return IndexerResult(
                    success=False,
                    error=f"Indexer failed: {result.stderr}",
                    duration_ms=duration_ms,
                )

            if not scip_path.exists():
                return IndexerResult(
                    success=False,
                    error="Indexer did not produce output file",
                    duration_ms=duration_ms,
                )

            return IndexerResult(
                success=True,
                scip_path=scip_path,
                duration_ms=duration_ms,
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            return IndexerResult(
                success=False,
                error=f"Indexer timed out after {timeout}s",
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            return IndexerResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

    def _build_command(
        self, recipe: ToolRecipe, project_root: Path, output_path: Path
    ) -> list[str] | None:
        """Build the command to run the indexer."""
        tool_info = self.tool_manager.get_tool_info(recipe.family)

        # Get tool path
        tool_path = str(tool_info.install_path) if tool_info.install_path else recipe.name

        # Build command based on tool
        if recipe.family == LanguageFamily.PYTHON:
            return [
                "scip-python",
                "index",
                str(project_root),
                "--output",
                str(output_path),
            ]

        if recipe.family == LanguageFamily.GO:
            return [
                tool_path,
                "--output",
                str(output_path),
                str(project_root),
            ]

        if recipe.family == LanguageFamily.RUST:
            return [
                tool_path,
                "scip",
                str(project_root),
                "--output",
                str(output_path),
            ]

        if recipe.family == LanguageFamily.JAVASCRIPT:
            return [
                "npx",
                "@sourcegraph/scip-typescript",
                "index",
                "--output",
                str(output_path),
            ]

        if recipe.family == LanguageFamily.JVM:
            jar_path = self.tool_manager.install_dir / "scip-java.jar"
            return [
                "java",
                "-jar",
                str(jar_path),
                "index",
                "--output",
                str(output_path),
            ]

        # Default pattern
        return [tool_path, "index", "--output", str(output_path)]


class SCIPParser:
    """
    Parses SCIP (Source Code Index Protocol) index files.

    SCIP files are protobuf-encoded and contain:
    - Metadata about the indexed project
    - Documents (one per source file)
    - Symbols with their definitions and documentation
    - Occurrences (where symbols appear)
    - Relationships between symbols

    Usage::

        parser = SCIPParser()
        index = parser.parse(Path("index.scip"))

        for doc in index.documents:
            print(f"File: {doc.relative_path}")
            for sym in doc.symbols:
                print(f"  Symbol: {sym.display_name}")
    """

    def parse(self, scip_path: Path) -> SCIPIndex:
        """
        Parse a SCIP index file.

        Args:
            scip_path: Path to .scip file

        Returns:
            SCIPIndex with parsed documents and symbols.
        """
        try:
            from scip import Index

            with open(scip_path, "rb") as f:
                proto_index = Index()
                proto_index.ParseFromString(f.read())

            return self._convert_index(proto_index)

        except ImportError:
            # scip package not installed - return empty index
            return SCIPIndex()
        except Exception:
            return SCIPIndex()

    def _convert_index(self, proto_index: Any) -> SCIPIndex:
        """Convert protobuf Index to our dataclass."""
        index = SCIPIndex()

        # Process documents
        for proto_doc in proto_index.documents:
            doc = self._convert_document(proto_doc)
            index.documents.append(doc)

        # Process external symbols
        for proto_sym in proto_index.external_symbols:
            sym = self._convert_symbol_info(proto_sym)
            if sym:
                index.external_symbols.append(sym)

        return index

    def _convert_document(self, proto_doc: Any) -> SCIPDocument:
        """Convert protobuf Document to our dataclass."""
        doc = SCIPDocument(
            language=proto_doc.language,
            relative_path=proto_doc.relative_path,
        )

        # Convert symbols
        for proto_sym in proto_doc.symbols:
            sym = self._convert_symbol_info(proto_sym)
            if sym:
                sym.file_path = proto_doc.relative_path
                doc.symbols.append(sym)

        # Convert occurrences
        for proto_occ in proto_doc.occurrences:
            occ = self._convert_occurrence(proto_occ, proto_doc.relative_path)
            if occ:
                doc.occurrences.append(occ)

        return doc

    def _convert_symbol_info(self, proto_sym: Any) -> SCIPSymbol | None:
        """Convert protobuf SymbolInformation to our dataclass."""
        if not proto_sym.symbol:
            return None

        # Parse display name from symbol string
        display_name = self._parse_display_name(proto_sym.symbol)

        # Determine kind from symbol
        kind = self._parse_symbol_kind(proto_sym.symbol)

        return SCIPSymbol(
            symbol=proto_sym.symbol,
            display_name=display_name,
            kind=kind,
            signature=getattr(proto_sym, "signature_documentation", None)
            or getattr(proto_sym, "type_", None),
            documentation="\n".join(proto_sym.documentation) if proto_sym.documentation else None,
        )

    def _convert_occurrence(self, proto_occ: Any, file_path: str) -> SCIPOccurrence | None:
        """Convert protobuf Occurrence to our dataclass."""
        if not proto_occ.symbol:
            return None

        # Parse range (SCIP uses [startLine, startCol, endLine, endCol])
        range_data = proto_occ.range
        if len(range_data) < 3:
            return None

        line = range_data[0]
        column = range_data[1]
        end_line = range_data[2] if len(range_data) > 2 else line
        end_column = range_data[3] if len(range_data) > 3 else column

        # Determine role from symbol_roles bitmask
        role = self._parse_role(proto_occ.symbol_roles)

        return SCIPOccurrence(
            symbol=proto_occ.symbol,
            file_path=file_path,
            line=line,
            column=column,
            end_line=end_line,
            end_column=end_column,
            role=role,
        )

    def _parse_display_name(self, symbol: str) -> str:
        """Extract display name from SCIP symbol string."""
        # SCIP symbols look like: "scip-python python MyClass#method()."
        # We want just "method"
        parts = symbol.split()
        if len(parts) >= 3:
            name_part = parts[-1]
            # Strip trailing punctuation
            name_part = name_part.rstrip(".#()[]")
            # Get last segment
            if "#" in name_part:
                name_part = name_part.split("#")[-1]
            return name_part
        return symbol

    def _parse_symbol_kind(self, symbol: str) -> str:
        """Infer symbol kind from SCIP symbol string."""
        if "()" in symbol or "(" in symbol:
            return "function"
        if "#" in symbol:
            # Nested in a class
            if "()" in symbol.split("#")[-1]:
                return "method"
            return "field"
        if symbol.endswith("."):
            return "module"
        return "variable"

    def _parse_role(self, symbol_roles: int) -> Role:
        """Parse role from SCIP symbol_roles bitmask."""
        # SCIP role bits:
        # 1 = Definition
        # 2 = Import
        # 4 = WriteAccess
        # 8 = ReadAccess
        # 16 = Generated
        # 32 = Test
        if symbol_roles & 1:
            return Role.DEFINITION
        if symbol_roles & 2:
            return Role.IMPORT
        return Role.REFERENCE


@dataclass
class PopulateResult:
    """Result of populating the database from SCIP index."""

    symbols_added: int = 0
    occurrences_added: int = 0
    edges_added: int = 0
    exports_added: int = 0
    errors: list[str] = field(default_factory=list)


class SCIPPopulator:
    """
    Populates the database with data from SCIP indexes.

    This class takes parsed SCIP data and creates/updates database
    records for symbols, occurrences, edges, and exports.

    Usage::

        from codeplane.index.db import Database

        db = Database(Path("index.db"))
        populator = SCIPPopulator(db)

        # Parse SCIP index
        parser = SCIPParser()
        index = parser.parse(Path("index.scip"))

        # Populate database
        result = populator.populate(index, context_id=1)
        print(f"Added {result.symbols_added} symbols")
    """

    def __init__(self, db: Any):  # Type is Database but avoid circular import
        self.db = db

    def populate(
        self,
        index: SCIPIndex,
        context_id: int,
        file_id_map: dict[str, int] | None = None,
    ) -> PopulateResult:
        """
        Populate the database with SCIP index data.

        Args:
            index: Parsed SCIP index
            context_id: ID of the context being indexed
            file_id_map: Optional mapping of file paths to file IDs

        Returns:
            PopulateResult with counts and errors.
        """
        result = PopulateResult()

        # Build symbol ID map for reference resolution
        symbol_id_map: dict[str, int] = {}

        with self.db.bulk_writer() as writer:
            # First pass: Insert all symbols (definitions)
            for doc in index.documents:
                file_id = file_id_map.get(doc.relative_path) if file_id_map else None

                for scip_sym in doc.symbols:
                    try:
                        sym = Symbol(
                            file_id=file_id or 0,
                            context_id=context_id,
                            name=scip_sym.display_name,
                            kind=scip_sym.kind,
                            line=scip_sym.line,
                            column=scip_sym.column,
                            end_line=scip_sym.end_line,
                            end_column=scip_sym.end_column,
                            signature=scip_sym.signature,
                            layer=Layer.SEMANTIC.value,
                            certainty=Certainty.CERTAIN.value,
                            scip_symbol=scip_sym.symbol,
                        )
                        # Insert and track ID
                        ids = writer.insert_many_returning_ids(Symbol, [sym])
                        if ids:
                            symbol_id_map[scip_sym.symbol] = ids[0]
                            result.symbols_added += 1
                    except Exception as e:
                        result.errors.append(f"Symbol error: {e}")

            # Second pass: Insert occurrences
            for doc in index.documents:
                file_id = file_id_map.get(doc.relative_path) if file_id_map else None

                for scip_occ in doc.occurrences:
                    try:
                        symbol_id = symbol_id_map.get(scip_occ.symbol)
                        if symbol_id is None:
                            continue

                        occ = Occurrence(
                            symbol_id=symbol_id,
                            file_id=file_id or 0,
                            context_id=context_id,
                            start_line=scip_occ.line,
                            start_col=scip_occ.column,
                            end_line=scip_occ.end_line,
                            end_col=scip_occ.end_column,
                            role=scip_occ.role.value,
                            layer=Layer.SEMANTIC.value,
                        )
                        writer.insert_many_returning_ids(Occurrence, [occ])
                        result.occurrences_added += 1
                    except Exception as e:
                        result.errors.append(f"Occurrence error: {e}")

            # Third pass: Insert symbol edges (relationships)
            for doc in index.documents:
                for rel in doc.relations:
                    try:
                        from_id = symbol_id_map.get(rel.from_symbol)
                        to_id = symbol_id_map.get(rel.to_symbol)
                        if from_id is None or to_id is None:
                            continue

                        edge = SymbolEdge(
                            src_symbol_id=from_id,
                            dst_symbol_id=to_id,
                            relation=rel.relation_type,
                            layer=Layer.SEMANTIC.value,
                            certainty=Certainty.CERTAIN.value,
                        )
                        writer.insert_many_returning_ids(SymbolEdge, [edge])
                        result.edges_added += 1
                    except Exception as e:
                        result.errors.append(f"Edge error: {e}")

        return result


def is_scip_available() -> bool:
    """Check if the scip protobuf package is available."""
    try:
        from scip import Index  # noqa: F401

        return True
    except ImportError:
        return False
