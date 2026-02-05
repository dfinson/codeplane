"""Structural index for fact extraction.

This module provides the Tier 1 (syntactic) indexing pipeline that uses
Tree-sitter to extract facts from source files. It handles:
- Parallel file processing with worker pools
- DefFact extraction (function, class, method definitions)
- RefFact extraction (identifier occurrences)
- ScopeFact extraction (lexical scopes)
- ImportFact extraction (import statements)
- LocalBindFact extraction (same-file bindings)
- DynamicAccessSite extraction (dynamic access telemetry)

See SPEC.md §7.3 for the fact table definitions.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database

from codeplane.index._internal.parsing import (
    SyntacticScope,
    SyntacticSymbol,
    TreeSitterParser,
)
from codeplane.index.models import (
    BindReasonCode,
    BindTargetKind,
    Certainty,
    DefFact,
    DynamicAccessSite,
    File,
    ImportFact,
    InterfaceImplFact,
    LocalBindFact,
    MemberAccessFact,
    ReceiverShapeFact,
    RefFact,
    RefTier,
    Role,
    ScopeFact,
    TypeAnnotationFact,
    TypeMemberFact,
)


def _compute_def_uid(
    unit_id: int,
    file_path: str,
    kind: str,
    lexical_path: str,
    signature_hash: str | None,
    disambiguator: int = 0,
) -> str:
    """Compute stable def_uid per SPEC.md §7.4.

    Includes file_path to distinguish same-named symbols in different files.
    """
    sig = signature_hash or ""
    raw = f"{unit_id}:{file_path}:{kind}:{lexical_path}:{sig}:{disambiguator}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _has_grammar_for_file(file_path: str) -> bool:
    """Check if a tree-sitter grammar is available for this file's language.

    Returns True if the file's language has a grammar available on PyPI.
    Returns False for languages like F#, VB.NET, Erlang, etc. that lack PyPI grammars.
    Also returns False for unknown file types.
    """
    from codeplane.core.languages import detect_language_family, has_grammar

    language = detect_language_family(file_path)
    if language is None:
        return False
    return has_grammar(language)


@dataclass
class ExtractionResult:
    """Result of extracting facts from a single file."""

    file_path: str
    defs: list[dict[str, Any]] = field(default_factory=list)
    refs: list[dict[str, Any]] = field(default_factory=list)
    scopes: list[dict[str, Any]] = field(default_factory=list)
    imports: list[dict[str, Any]] = field(default_factory=list)
    binds: list[dict[str, Any]] = field(default_factory=list)
    dynamic_sites: list[dict[str, Any]] = field(default_factory=list)
    # Type-aware facts (Tier 2)
    type_annotations: list[dict[str, Any]] = field(default_factory=list)
    type_members: list[dict[str, Any]] = field(default_factory=list)
    member_accesses: list[dict[str, Any]] = field(default_factory=list)
    interface_impls: list[dict[str, Any]] = field(default_factory=list)
    receiver_shapes: list[dict[str, Any]] = field(default_factory=list)
    interface_hash: str | None = None
    content_hash: str | None = None
    line_count: int = 0
    error: str | None = None
    parse_time_ms: int = 0
    # Flag indicating file was skipped due to no grammar (not an error)
    skipped_no_grammar: bool = False


@dataclass
class BatchResult:
    """Result of processing a batch of files."""

    files_processed: int = 0
    defs_extracted: int = 0
    refs_extracted: int = 0
    scopes_extracted: int = 0
    imports_extracted: int = 0
    binds_extracted: int = 0
    dynamic_sites_extracted: int = 0
    # Type-aware facts (Tier 2)
    type_annotations_extracted: int = 0
    type_members_extracted: int = 0
    member_accesses_extracted: int = 0
    interface_impls_extracted: int = 0
    receiver_shapes_extracted: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    # Count of files skipped due to no grammar (not errors)
    files_skipped_no_grammar: int = 0


def _extract_file(file_path: str, repo_root: str, unit_id: int) -> ExtractionResult:
    """Extract all facts from a single file (worker function).

    Extracts: DefFact, RefFact, ScopeFact, ImportFact, LocalBindFact, DynamicAccessSite

    Files whose language has no tree-sitter grammar (e.g., F#, VB.NET, Erlang)
    are gracefully skipped - they will still be indexed in Tantivy for lexical
    search, but no structural facts are extracted.
    """
    start = time.monotonic()
    result = ExtractionResult(file_path=file_path)

    try:
        full_path = Path(repo_root) / file_path
        if not full_path.exists():
            result.error = "File not found"
            return result

        content = full_path.read_bytes()
        result.content_hash = hashlib.sha256(content).hexdigest()
        result.line_count = content.count(b"\n") + (
            1 if content and not content.endswith(b"\n") else 0
        )

        # Check if grammar is available BEFORE attempting to parse
        # This gracefully handles languages like F#, VB.NET, Erlang that
        # have language definitions but no PyPI-available tree-sitter grammar
        if not _has_grammar_for_file(file_path):
            result.skipped_no_grammar = True
            result.parse_time_ms = int((time.monotonic() - start) * 1000)
            return result

        parser = TreeSitterParser()
        try:
            parse_result = parser.parse(full_path, content)
        except ValueError as e:
            result.error = str(e)
            return result

        # Extract symbols (for DefFact)
        symbols = parser.extract_symbols(parse_result)
        result.interface_hash = parser.compute_interface_hash(symbols)

        # Extract scopes (for ScopeFact)
        scopes = parser.extract_scopes(parse_result)

        # Extract imports (for ImportFact)
        imports = parser.extract_imports(parse_result, file_path)

        # Extract dynamic accesses (for DynamicAccessSite)
        dynamics = parser.extract_dynamic_accesses(parse_result)

        # Build scope ID mapping (local file scope ID -> will be assigned DB scope_id later)
        # For now, we store the local scope_id and parent mapping in the dict
        for scope in scopes:
            scope_dict = {
                "unit_id": unit_id,
                "local_scope_id": scope.scope_id,  # File-local ID
                "parent_local_scope_id": scope.parent_scope_id,  # File-local parent ID
                "kind": scope.kind,
                "start_line": scope.start_line,
                "start_col": scope.start_col,
                "end_line": scope.end_line,
                "end_col": scope.end_col,
            }
            result.scopes.append(scope_dict)

        # Build def_uid -> scope mapping for binding resolution
        def_uid_by_name: dict[str, str] = {}  # name -> def_uid (latest in file)
        def_scope_by_name: dict[str, int] = {}  # name -> local_scope_id containing def

        # Track disambiguator for symbols with same (lexical_path, sig_hash)
        disambiguator_counts: dict[tuple[str, str | None], int] = {}

        # Convert symbols to DefFact dicts
        for sym in symbols:
            sig_hash = (
                hashlib.sha256((sym.signature or "").encode()).hexdigest()[:8]
                if sym.signature
                else None
            )
            lexical_path = _compute_lexical_path(sym, symbols)

            # Compute disambiguator for same-signature siblings
            key = (lexical_path, sig_hash)
            disambiguator = disambiguator_counts.get(key, 0)
            disambiguator_counts[key] = disambiguator + 1

            def_uid = _compute_def_uid(
                unit_id, file_path, sym.kind, lexical_path, sig_hash, disambiguator
            )

            # Find containing scope
            containing_scope = _find_containing_scope(scopes, sym.line, sym.column)

            def_dict = {
                "def_uid": def_uid,
                "unit_id": unit_id,
                "kind": sym.kind,
                "name": sym.name,
                "lexical_path": _compute_lexical_path(sym, symbols),
                "start_line": sym.line,
                "start_col": sym.column,
                "end_line": sym.end_line,
                "end_col": sym.end_column,
                "signature_hash": sig_hash,
                "display_name": sym.signature,
            }
            result.defs.append(def_dict)

            # Track for binding resolution
            def_uid_by_name[sym.name] = def_uid
            def_scope_by_name[sym.name] = containing_scope

            # Create a definition RefFact (definition sites are PROVEN refs to themselves)
            ref_dict = {
                "unit_id": unit_id,
                "token_text": sym.name,
                "start_line": sym.line,
                "start_col": sym.column,
                "end_line": sym.end_line,
                "end_col": sym.end_column,
                "role": Role.DEFINITION.value,
                "ref_tier": RefTier.PROVEN.value,
                "certainty": Certainty.CERTAIN.value,
                "target_def_uid": def_uid,
                "local_scope_id": containing_scope,
            }
            result.refs.append(ref_dict)

            # Create LocalBindFact for the definition binding (scope_id omitted - not tracking scopes in DB yet)
            bind_dict = {
                "unit_id": unit_id,
                "name": sym.name,
                "target_kind": BindTargetKind.DEF.value,
                "target_uid": def_uid,
                "certainty": Certainty.CERTAIN.value,
                "reason_code": BindReasonCode.DEF_IN_SCOPE.value,
            }
            result.binds.append(bind_dict)

        # Convert imports to ImportFact dicts and create bindings
        import_uid_by_alias: dict[str, str] = {}  # alias/name -> import_uid
        for imp in imports:
            import_dict = {
                "import_uid": imp.import_uid,
                "unit_id": unit_id,
                "scope_id": None,  # scope_id is nullable FK - will be set later if scopes are tracked
                "imported_name": imp.imported_name,
                "alias": imp.alias,
                "source_literal": imp.source_literal,
                "import_kind": imp.import_kind,
                "certainty": Certainty.CERTAIN.value,
                # Not stored in DB, but used for deduplication
                "_start_line": imp.start_line,
                "_start_col": imp.start_col,
            }
            result.imports.append(import_dict)

            # Track for binding resolution
            local_name = imp.alias or imp.imported_name
            import_uid_by_alias[local_name] = imp.import_uid

            # Create LocalBindFact for import binding (scope_id omitted - not tracking scopes in DB yet)
            bind_dict = {
                "unit_id": unit_id,
                "name": local_name,
                "target_kind": BindTargetKind.IMPORT.value,
                "target_uid": imp.import_uid,
                "certainty": Certainty.CERTAIN.value,
                "reason_code": BindReasonCode.IMPORT_ALIAS.value,
            }
            result.binds.append(bind_dict)

            # Create RefFact for the import statement
            ref_dict = {
                "unit_id": unit_id,
                "token_text": imp.imported_name,
                "start_line": imp.start_line,
                "start_col": imp.start_col,
                "end_line": imp.end_line,
                "end_col": imp.end_col,
                "role": Role.IMPORT.value,
                "ref_tier": RefTier.UNKNOWN.value,  # Cross-file resolution needed
                "certainty": Certainty.CERTAIN.value,
                "target_def_uid": None,
                "local_scope_id": imp.scope_id or 0,
            }
            result.refs.append(ref_dict)

        # Extract identifier occurrences for reference RefFacts
        occurrences = parser.extract_identifier_occurrences(parse_result)
        for occ in occurrences:
            # Skip if this is already a definition site
            is_def_site = any(
                d["name"] == occ.name
                and d["start_line"] == occ.line
                and d["start_col"] == occ.column
                for d in result.defs
            )
            if is_def_site:
                continue

            # Skip if this is an import site
            is_import_site = any(
                i["imported_name"] == occ.name and i["_start_line"] == occ.line
                for i in result.imports
            )
            if is_import_site:
                continue

            containing_scope = _find_containing_scope(scopes, occ.line, occ.column)

            # Determine ref_tier and target based on local bindings
            ref_tier = RefTier.UNKNOWN.value
            target_def_uid = None
            certainty = Certainty.UNCERTAIN.value

            # Check if name is bound in scope (same-file definition)
            if occ.name in def_uid_by_name:
                ref_tier = RefTier.PROVEN.value
                target_def_uid = def_uid_by_name[occ.name]
                certainty = Certainty.CERTAIN.value
            # Check if name is an import alias
            elif occ.name in import_uid_by_alias:
                ref_tier = RefTier.STRONG.value  # Cross-file with explicit trace
                certainty = Certainty.CERTAIN.value

            ref_dict = {
                "unit_id": unit_id,
                "token_text": occ.name,
                "start_line": occ.line,
                "start_col": occ.column,
                "end_line": occ.end_line,
                "end_col": occ.end_column,
                "role": Role.REFERENCE.value,
                "ref_tier": ref_tier,
                "certainty": certainty,
                "target_def_uid": target_def_uid,
                "local_scope_id": containing_scope,
            }
            result.refs.append(ref_dict)

        # Convert dynamic accesses to DynamicAccessSite dicts
        for dyn in dynamics:
            dyn_dict = {
                "unit_id": unit_id,
                "start_line": dyn.start_line,
                "start_col": dyn.start_col,
                "pattern_type": dyn.pattern_type,
                "extracted_literals": json.dumps(dyn.extracted_literals)
                if dyn.extracted_literals
                else None,
                "has_non_literal_key": dyn.has_non_literal_key,
            }
            result.dynamic_sites.append(dyn_dict)

        # Extract type-aware facts (Tier 2) using language-specific extractors
        _extract_type_aware_facts(result, parse_result, content, unit_id, file_path)

        result.parse_time_ms = int((time.monotonic() - start) * 1000)

    except Exception as e:
        result.error = str(e)

    return result


def _extract_type_aware_facts(
    extraction: ExtractionResult,
    tree: Any,
    _content: bytes,
    unit_id: int,
    file_path: str,
) -> None:
    """Extract type-aware facts using language-specific extractors.

    Populates extraction.type_annotations, type_members, member_accesses.
    This is called after the base extraction for Tier 2 indexing.
    """
    try:
        from codeplane.index._internal.extraction import get_registry

        # Get language name from file extension
        ext = Path(file_path).suffix.lower()
        ext_to_family = {
            ".py": "python",
            ".pyi": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".kt": "kotlin",
            ".scala": "scala",
            ".cs": "csharp",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "cpp",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
        }
        language = ext_to_family.get(ext)
        if not language:
            return

        registry = get_registry()
        extractor = registry.get_or_fallback(language)

        # Extract type annotations
        annotations = extractor.extract_type_annotations(tree, file_path, extraction.scopes)
        for ann in annotations:
            extraction.type_annotations.append(
                {
                    "unit_id": unit_id,
                    "target_kind": ann.target_kind,
                    "target_name": ann.target_name,
                    "raw_annotation": ann.raw_annotation,
                    "canonical_type": ann.canonical_type,
                    "base_type": ann.base_type,
                    "is_optional": ann.is_optional,
                    "is_array": ann.is_array,
                    "is_generic": ann.is_generic,
                    "is_reference": ann.is_reference,
                    "is_mutable": ann.is_mutable,
                    "start_line": ann.start_line,
                    "start_col": ann.start_col,
                }
            )

        # Extract type members
        members = extractor.extract_type_members(tree, file_path, extraction.defs)
        for mem in members:
            extraction.type_members.append(
                {
                    "unit_id": unit_id,
                    "parent_def_uid": mem.parent_def_uid,
                    "parent_type_name": mem.parent_type_name,
                    "parent_kind": mem.parent_kind,
                    "member_kind": mem.member_kind,
                    "member_name": mem.member_name,
                    "member_def_uid": mem.member_def_uid,
                    "type_annotation": mem.type_annotation,
                    "canonical_type": mem.canonical_type,
                    "base_type": mem.base_type,
                    "visibility": mem.visibility,
                    "is_static": mem.is_static,
                    "is_abstract": mem.is_abstract,
                    "start_line": mem.start_line,
                    "start_col": mem.start_col,
                }
            )

        # Extract member accesses
        accesses = extractor.extract_member_accesses(
            tree, file_path, extraction.scopes, annotations
        )
        for acc in accesses:
            extraction.member_accesses.append(
                {
                    "unit_id": unit_id,
                    "access_style": acc.access_style,
                    "full_expression": acc.full_expression,
                    "receiver_name": acc.receiver_name,
                    "member_chain": acc.member_chain,
                    "final_member": acc.final_member,
                    "chain_depth": acc.chain_depth,
                    "is_invocation": acc.is_invocation,
                    "arg_count": acc.arg_count,
                    "start_line": acc.start_line,
                    "start_col": acc.start_col,
                    "end_line": acc.end_line,
                    "end_col": acc.end_col,
                }
            )

        # Extract interface implementations (if extractor supports it)
        impls = extractor.extract_interface_impls(tree, file_path, extraction.defs)
        for impl in impls:
            extraction.interface_impls.append(
                {
                    "unit_id": unit_id,
                    "implementor_def_uid": impl.implementor_def_uid,
                    "implementor_name": impl.implementor_name,
                    "interface_name": impl.interface_name,
                    "interface_def_uid": impl.interface_def_uid,
                    "impl_style": impl.impl_style,
                    "start_line": impl.start_line,
                    "start_col": impl.start_col,
                }
            )

    except ImportError:
        # Extraction module not available - skip type-aware extraction
        pass
    except Exception:
        # Don't fail extraction for type-aware facts - they're supplementary
        pass


def _find_containing_scope(scopes: list[SyntacticScope], line: int, col: int) -> int:
    """Find the innermost scope containing the given position.

    Returns the file-local scope_id (0 for file scope).
    """
    # Sort by specificity (smaller ranges are more specific)
    containing: list[SyntacticScope] = []
    for scope in scopes:
        if (scope.start_line < line or (scope.start_line == line and scope.start_col <= col)) and (
            scope.end_line > line or (scope.end_line == line and scope.end_col >= col)
        ):
            containing.append(scope)

    if not containing:
        return 0  # File scope

    # Return innermost (smallest range)
    innermost = min(
        containing,
        key=lambda s: (s.end_line - s.start_line) * 10000 + (s.end_col - s.start_col),
    )
    return innermost.scope_id


def _compute_lexical_path(sym: SyntacticSymbol, all_symbols: list[SyntacticSymbol]) -> str:
    """Compute the lexical path for a symbol (e.g., 'Class.method')."""
    if sym.parent_name:
        return f"{sym.parent_name}.{sym.name}"

    # For classes/functions at module level, just use the name
    if sym.kind in ("class", "function"):
        return sym.name

    # For methods, try to find the containing class
    for other in all_symbols:
        if other.kind == "class" and (
            other.line <= sym.line <= other.end_line and other.column <= sym.column
        ):
            return f"{other.name}.{sym.name}"

    return sym.name


class StructuralIndexer:
    """Extracts facts from source files using Tree-sitter.

    This is the Tier 1 (syntactic) indexing layer. It provides:
    - DefFact extraction (function/class/method definitions)
    - RefFact extraction (identifier occurrences)
    - ScopeFact extraction (lexical scopes)
    - ImportFact extraction (import statements)
    - LocalBindFact extraction (same-file bindings)
    - DynamicAccessSite extraction (dynamic access telemetry)

    Files whose language has no tree-sitter grammar (e.g., F#, VB.NET, Erlang)
    are gracefully skipped by this indexer. They will still be searchable via
    the lexical (Tantivy) index.

    Usage::

        indexer = StructuralIndexer(db, repo_path)
        result = indexer.index_files(file_paths, context_id=1)
    """

    def __init__(self, db: Database, repo_path: Path | str):
        self.db = db
        self.repo_path = Path(repo_path)
        self._parser = TreeSitterParser()

    def index_files(
        self,
        file_paths: list[str],
        context_id: int,
        file_id_map: dict[str, int] | None = None,
        workers: int = 1,
    ) -> BatchResult:
        """Index a batch of files."""
        start = time.monotonic()
        result = BatchResult()

        if workers > 1:
            extractions = self._parallel_extract(file_paths, context_id, workers)
        else:
            extractions = self._sequential_extract(file_paths, context_id)

        # Pre-create all files BEFORE entering bulk_writer to avoid lock contention
        if file_id_map is None:
            file_id_map = {}
        for extraction in extractions:
            if extraction.error:
                continue
            if extraction.file_path not in file_id_map:
                file_id_map[extraction.file_path] = self._ensure_file_id(
                    extraction.file_path, extraction.content_hash, extraction.line_count, context_id
                )

        with self.db.bulk_writer() as writer:
            for extraction in extractions:
                result.files_processed += 1

                if extraction.error:
                    result.errors.append(f"{extraction.file_path}: {extraction.error}")
                    continue

                # Track files skipped due to no grammar (not errors)
                if extraction.skipped_no_grammar:
                    result.files_skipped_no_grammar += 1
                    continue

                file_id = file_id_map.get(extraction.file_path)
                if file_id is None:
                    result.errors.append(f"{extraction.file_path}: File ID not found")
                    continue

                # Delete existing facts for this file (idempotent re-indexing)
                for fact_model in (
                    DefFact,
                    RefFact,
                    ScopeFact,
                    ImportFact,
                    LocalBindFact,
                    DynamicAccessSite,
                    TypeAnnotationFact,
                    TypeMemberFact,
                    MemberAccessFact,
                    InterfaceImplFact,
                    ReceiverShapeFact,
                ):
                    writer.delete_where(fact_model, "file_id = :fid", {"fid": file_id})

                # Build local_scope_id -> db_scope_id mapping
                scope_id_map: dict[int, int] = {}  # local_scope_id -> db scope_id

                # Insert ScopeFacts first (need IDs for refs/binds)
                for scope_dict in extraction.scopes:
                    scope_dict.pop("local_scope_id")
                    parent_local_id = scope_dict.pop("parent_local_scope_id")
                    scope_dict["file_id"] = file_id
                    # Parent scope ID will be resolved after all scopes are inserted
                    scope_dict["parent_scope_id"] = (
                        scope_id_map.get(parent_local_id) if parent_local_id is not None else None
                    )
                    writer.insert_many(ScopeFact, [scope_dict])
                    # Note: For proper parent_scope_id resolution, we'd need to insert
                    # in dependency order. For now, leave parent_scope_id as None
                    # and update later if needed.
                    result.scopes_extracted += 1

                # Insert DefFacts
                for def_dict in extraction.defs:
                    def_dict["file_id"] = file_id
                    writer.insert_many(DefFact, [def_dict])
                    result.defs_extracted += 1

                # Insert RefFacts
                for ref_dict in extraction.refs:
                    ref_dict["file_id"] = file_id
                    # Remove local_scope_id (not a DB column, used for internal tracking)
                    ref_dict.pop("local_scope_id", None)
                    writer.insert_many(RefFact, [ref_dict])
                    result.refs_extracted += 1

                # Insert ImportFacts
                for import_dict in extraction.imports:
                    import_dict["file_id"] = file_id
                    # Remove internal tracking fields not in DB schema
                    import_dict.pop("_start_line", None)
                    import_dict.pop("_start_col", None)
                    writer.insert_many(ImportFact, [import_dict])
                    result.imports_extracted += 1

                # Insert LocalBindFacts
                for bind_dict in extraction.binds:
                    bind_dict["file_id"] = file_id
                    # scope_id is nullable - leave as None until we properly track scopes
                    bind_dict["scope_id"] = None
                    writer.insert_many(LocalBindFact, [bind_dict])
                    result.binds_extracted += 1

                # Insert DynamicAccessSites
                for dyn_dict in extraction.dynamic_sites:
                    dyn_dict["file_id"] = file_id
                    writer.insert_many(DynamicAccessSite, [dyn_dict])
                    result.dynamic_sites_extracted += 1

                # Insert TypeAnnotationFacts (Tier 2)
                for ann_dict in extraction.type_annotations:
                    ann_dict["file_id"] = file_id
                    writer.insert_many(TypeAnnotationFact, [ann_dict])
                    result.type_annotations_extracted += 1

                # Insert TypeMemberFacts (Tier 2)
                for mem_dict in extraction.type_members:
                    mem_dict["file_id"] = file_id
                    writer.insert_many(TypeMemberFact, [mem_dict])
                    result.type_members_extracted += 1

                # Insert MemberAccessFacts (Tier 2)
                for acc_dict in extraction.member_accesses:
                    acc_dict["file_id"] = file_id
                    writer.insert_many(MemberAccessFact, [acc_dict])
                    result.member_accesses_extracted += 1

                # Insert InterfaceImplFacts (Tier 2)
                for impl_dict in extraction.interface_impls:
                    impl_dict["file_id"] = file_id
                    writer.insert_many(InterfaceImplFact, [impl_dict])
                    result.interface_impls_extracted += 1

                # Insert ReceiverShapeFacts (Tier 2) - computed during resolution, not extraction
                for shape_dict in extraction.receiver_shapes:
                    shape_dict["file_id"] = file_id
                    writer.insert_many(ReceiverShapeFact, [shape_dict])
                    result.receiver_shapes_extracted += 1

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    def _sequential_extract(self, file_paths: list[str], unit_id: int) -> list[ExtractionResult]:
        """Extract facts sequentially."""
        results = []
        for path in file_paths:
            result = _extract_file(path, str(self.repo_path), unit_id)
            results.append(result)
        return results

    def _parallel_extract(
        self, file_paths: list[str], unit_id: int, workers: int
    ) -> list[ExtractionResult]:
        """Extract facts in parallel using process pool."""
        results = []
        repo_root = str(self.repo_path)

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_extract_file, path, repo_root, unit_id): path
                for path in file_paths
            }

            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    path = futures[future]
                    results.append(ExtractionResult(file_path=path, error=str(e)))

        return results

    def _ensure_file_id(
        self, file_path: str, content_hash: str | None, line_count: int, _context_id: int
    ) -> int:
        """Ensure file exists in database and return its ID."""
        import time

        with self.db.session() as session:
            from sqlmodel import select

            stmt = select(File).where(File.path == file_path)
            existing = session.exec(stmt).first()

            if existing and existing.id is not None:
                return existing.id

            file = File(
                path=file_path,
                content_hash=content_hash,
                line_count=line_count,
                language_family=self._detect_family(file_path),
                indexed_at=time.time(),  # Mark as indexed
            )
            session.add(file)
            session.commit()
            session.refresh(file)
            return file.id if file.id is not None else 0

    def _detect_family(self, file_path: str) -> str | None:
        """Detect language name from file path."""
        ext = Path(file_path).suffix.lower()
        ext_map = {
            ".py": "python",
            ".pyi": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "javascript",
            ".tsx": "javascript",
            ".go": "go",
            ".rs": "rust",
            ".java": "jvm",
            ".kt": "jvm",
            ".scala": "jvm",
            ".cs": "dotnet",
            ".cpp": "cpp",
            ".c": "cpp",
            ".h": "cpp",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
        }
        return ext_map.get(ext)

    def extract_single(self, file_path: str, unit_id: int = 0) -> ExtractionResult:
        """Extract facts from a single file without storing."""
        return _extract_file(file_path, str(self.repo_path), unit_id)

    def compute_batch_interface_hash(self, file_paths: list[str]) -> str:
        """Compute combined interface hash for multiple files."""
        hashes = []
        for path in sorted(file_paths):
            result = self.extract_single(path)
            if result.interface_hash:
                hashes.append(result.interface_hash)

        combined = "\n".join(hashes)
        return hashlib.sha256(combined.encode()).hexdigest()


def index_context(
    db: Any,
    repo_path: Path | str,
    context_id: int,
    file_paths: list[str],
    workers: int = os.cpu_count() or 1,
) -> BatchResult:
    """Convenience function to index all files in a context."""
    indexer = StructuralIndexer(db, repo_path)
    return indexer.index_files(file_paths, context_id, workers=workers)
