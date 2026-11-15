"""Bounded syntax evidence, never execution or a claim about command semantics."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tree_sitter import Language, Node

Dialect = Literal["bash", "sh", "powershell"]
Scope = Literal["immediate", "conditional", "deferred"]

MAX_BYTES = 32 * 1024
MAX_DEPTH = 32
MAX_NODES = 10_000
PARSE_BUDGET_SECONDS = 0.050
SYNTAX_VERSION = "cp-syntax-v1"
GRAMMAR_VERSIONS = {
    "bash": "tree-sitter-bash/0.25.1",
    "sh": "tree-sitter-bash/0.25.1:declared-posix",
    "powershell": "tree-sitter-powershell/0.26.4",
}


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    document: int
    start: int
    end: int
    encoding: str


@dataclass(frozen=True, slots=True)
class SyntaxDocument:
    raw: bytes
    dialect: str | None
    sha256: str
    origin: SourceOrigin | None = None


@dataclass(frozen=True, slots=True)
class SyntaxNode:
    document: int
    kind: str
    start: int
    end: int
    parent: int | None
    scope: Scope
    named: bool
    missing: bool


@dataclass(frozen=True, slots=True)
class CommandFact:
    node: int
    name: str | None
    scope: Scope
    function: str | None
    arguments: tuple[str | None, ...]


@dataclass(frozen=True, slots=True)
class SyntaxFacts:
    documents: tuple[SyntaxDocument, ...]
    nodes: tuple[SyntaxNode, ...]
    commands: tuple[CommandFact, ...]
    structures: frozenset[str]
    incomplete: tuple[str, ...]
    elapsed_ms: float
    version: str = SYNTAX_VERSION

    @property
    def complete(self) -> bool:
        return not self.incomplete


@dataclass
class _Budget:
    deadline: float
    documents: list[SyntaxDocument] = field(default_factory=list)
    nodes: list[SyntaxNode] = field(default_factory=list)
    commands: list[CommandFact] = field(default_factory=list)
    structures: set[str] = field(default_factory=set)
    incomplete: set[str] = field(default_factory=set)
    byte_count: int = 0

    def available(self) -> bool:
        if time.perf_counter() >= self.deadline:
            self.incomplete.add("parser_timeout")
            return False
        return True


@lru_cache(maxsize=2)
def _language(dialect: str) -> Language:
    from tree_sitter import Language

    if dialect == "powershell":
        import tree_sitter_powershell

        return Language(tree_sitter_powershell.language())
    import tree_sitter_bash

    return Language(tree_sitter_bash.language())


_CONDITIONAL = frozenset(
    {
        "if_statement",
        "elif_clause",
        "else_clause",
        "while_statement",
        "for_statement",
        "c_style_for_statement",
        "case_statement",
        "case_item",
        "list",
        "elseif_clause",
        "switch_statement",
        "switch_clause",
        "foreach_statement",
        "do_statement",
        "try_statement",
        "catch_clause",
        "finally_clause",
        "trap_statement",
    }
)
_DEFERRED = frozenset({"function_definition", "function_statement", "script_block_expression"})
_STRUCTURES = {
    "pipeline": "pipeline",
    "file_redirect": "redirection",
    "heredoc_redirect": "redirection",
    "herestring_redirect": "redirection",
    "redirection": "redirection",
    "command_substitution": "substitution",
    "process_substitution": "substitution",
    "sub_expression": "substitution",
    "subshell": "compound",
    "compound_statement": "compound",
    "script_block_expression": "script",
}
_DYNAMIC = frozenset({"expansion", "simple_expansion", "arithmetic_expansion", "variable", "splat_expression"})


def _literal(node: Node, raw: bytes, dialect: str) -> str | None:
    text = raw[node.start_byte : node.end_byte].decode("utf-8")
    if node.type in {"word", "command_name", "command_name_expr", "generic_token"}:
        if any(character in text for character in "$`*?[]{}\\"):
            return None
        return text
    if len(text) >= 2 and text[0] == text[-1] == "'":
        if dialect == "powershell":
            return text[1:-1].replace("''", "'")
        return text[1:-1]
    if len(text) >= 2 and text[0] == text[-1] == '"':
        inner = text[1:-1]
        if any(character in inner for character in "$`\\"):
            return None
        return inner
    return None


def _command_parts(node: Node, raw: bytes, dialect: str) -> tuple[Node | None, list[Node]]:
    if dialect != "powershell":
        name = node.child_by_field_name("name")
        return name, list(node.children_by_field_name("argument"))
    children = list(node.named_children)
    name = next((child for child in children if child.type in {"command_name", "command_name_expr"}), None)
    arguments: list[Node] = []
    for child in children:
        if child.type == "command_elements":
            arguments.extend(child.named_children)
    return name, arguments


def _embedded(node: Node, document: int, raw: bytes, dialect: str, depth: int, budget: _Budget) -> None:
    name_node, args = _command_parts(node, raw, dialect)
    name = _literal(name_node, raw, dialect) if name_node is not None else None
    if name is None:
        budget.incomplete.add("unresolved_expansion")
        return
    interpreter = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower().removesuffix(".exe")
    if interpreter not in {"bash", "sh", "pwsh", "powershell"}:
        if interpreter in {"eval", "invoke-expression", "iex", ".", "source"}:
            budget.incomplete.add("opaque_script")
        return
    nested_dialect = "powershell" if interpreter in {"pwsh", "powershell"} else interpreter
    for index, arg in enumerate(args):
        flag = raw[arg.start_byte : arg.end_byte].decode("utf-8").lower()
        encoded = nested_dialect == "powershell" and flag in {"-encodedcommand", "-enc", "-e"}
        script_flag = flag == "-c" if nested_dialect != "powershell" else flag in {"-command", "-c"}
        if not encoded and not script_flag:
            continue
        if index + 1 >= len(args):
            budget.incomplete.add("opaque_script")
            return
        script_node = args[index + 1]
        script = _literal(script_node, raw, dialect)
        if script is None:
            budget.incomplete.add("opaque_script")
            return
        try:
            content = (
                base64.b64decode(script, validate=True).decode("utf-16-le").encode("utf-8")
                if encoded
                else script.encode("utf-8")
            )
        except (ValueError, UnicodeError, binascii.Error):
            budget.incomplete.add("invalid_encoded_script")
            return
        budget.structures.add("script")
        if encoded:
            budget.structures.add("encoded")
        _parse(
            content,
            nested_dialect,
            depth + 1,
            budget,
            SourceOrigin(
                document,
                script_node.start_byte,
                script_node.end_byte,
                "base64-utf16le" if encoded else "literal",
            ),
        )
        return
    budget.incomplete.add("opaque_script")


def _parse(raw: bytes, dialect: str | None, depth: int, budget: _Budget, origin: SourceOrigin | None = None) -> None:
    from tree_sitter import Parser

    document = len(budget.documents)
    budget.documents.append(SyntaxDocument(raw, dialect, hashlib.sha256(raw).hexdigest(), origin))
    budget.byte_count += len(raw)
    if budget.byte_count > MAX_BYTES:
        budget.incomplete.add("byte_limit")
        return
    if dialect not in GRAMMAR_VERSIONS:
        budget.incomplete.add("syntax_unsupported")
        return
    if depth > MAX_DEPTH:
        budget.incomplete.add("depth_limit")
        return
    try:
        raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        budget.incomplete.add("invalid_encoding")
        return
    if not budget.available():
        return
    language = _language("bash" if dialect == "sh" else dialect)
    parser = Parser(
        language,
        timeout_micros=max(1, int((budget.deadline - time.perf_counter()) * 1_000_000)),
    )
    try:
        tree = parser.parse(raw)
    except ValueError:
        budget.incomplete.add("parser_timeout")
        return
    root = tree.root_node
    # Grammars may exclude leading whitespace from the root.
    if raw[: root.start_byte].strip() or raw[root.end_byte :].strip():
        budget.incomplete.add("partial_root")
    if root.has_error:
        budget.incomplete.add("syntax_error")
    stack: list[tuple[Node, int | None, int, Scope, str | None]] = [(root, None, depth, "immediate", None)]
    while stack and budget.available():
        current, parent, node_depth, scope, function = stack.pop()
        if len(budget.nodes) >= MAX_NODES:
            budget.incomplete.add("node_limit")
            return
        if node_depth > MAX_DEPTH:
            budget.incomplete.add("depth_limit")
            continue
        if current.is_missing or current.type == "ERROR":
            budget.incomplete.add("syntax_error")
        if current.type in _DEFERRED:
            scope = "deferred"
            definition_name = current.child_by_field_name("name")
            if definition_name is not None:
                function = raw[definition_name.start_byte : definition_name.end_byte].decode("utf-8")
        elif scope != "deferred" and current.type in _CONDITIONAL:
            scope = "conditional"
        if current.type in _STRUCTURES:
            budget.structures.add(_STRUCTURES[current.type])
        if current.type in _CONDITIONAL:
            budget.structures.add("compound")
        if current.type in _DYNAMIC:
            budget.incomplete.add("unresolved_expansion")
        position = len(budget.nodes)
        budget.nodes.append(
            SyntaxNode(
                document,
                current.type,
                current.start_byte,
                current.end_byte,
                parent,
                scope,
                current.is_named,
                current.is_missing,
            )
        )
        if current.type == "command":
            name_node, args = _command_parts(current, raw, dialect)
            name = _literal(name_node, raw, dialect) if name_node is not None else None
            budget.commands.append(
                CommandFact(
                    position,
                    name,
                    scope,
                    function,
                    tuple(_literal(arg, raw, dialect) for arg in args),
                )
            )
            _embedded(current, document, raw, dialect, node_depth, budget)
        stack.extend((child, position, node_depth + 1, scope, function) for child in reversed(current.children))


def analyze_syntax(command: bytes | str, dialect: str | None) -> SyntaxFacts:
    """Keep original bytes and every visited node, including executable children."""
    start = time.perf_counter()
    budget = _Budget(start + PARSE_BUDGET_SECONDS)
    raw = command.encode("utf-8") if isinstance(command, str) else command
    try:
        _parse(raw, dialect, 0, budget)
    except ImportError:
        budget.documents = [SyntaxDocument(raw, dialect, hashlib.sha256(raw).hexdigest())]
        budget.incomplete.add("grammar_unavailable")
    return SyntaxFacts(
        tuple(budget.documents),
        tuple(budget.nodes),
        tuple(budget.commands),
        frozenset(budget.structures),
        tuple(sorted(budget.incomplete)),
        (time.perf_counter() - start) * 1000,
    )


async def analyze_syntax_async(command: bytes | str, dialect: str | None) -> SyntaxFacts:
    return await asyncio.to_thread(analyze_syntax, command, dialect)
