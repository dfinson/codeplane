"""Tree-sitter queries for SEM_FACTS evidence record extraction (SPEC §16.7).

Each supported language defines tree-sitter S-expression queries with captures
that categorize semantic facts within definition bodies:

  @sem_call    – function/method name at a call site
  @sem_field   – member field name in an assignment (e.g. self.x = ...)
  @sem_return  – identifier in a return statement
  @sem_raise   – exception / error type in throw / raise
  @sem_key     – key literal in dict / map / object construction

Languages without a query definition gracefully produce no SEM_FACTS records.
New languages are added by inserting a query string keyed by the tree-sitter
language name (LANGUAGE_MAP values in treesitter.py).
"""

from __future__ import annotations

# Capture name → human-readable category tag used in rendered text.
# Order here determines rendering order in embedding text.
SEM_CAPTURE_CATEGORIES: dict[str, str] = {
    "sem_call": "calls",
    "sem_field": "assigns",
    "sem_return": "returns",
    "sem_raise": "raises",
    "sem_key": "literals",
}

# Rendering order (stable across runs).
SEM_CATEGORY_ORDER: tuple[str, ...] = (
    "calls",
    "assigns",
    "returns",
    "raises",
    "literals",
)

# Max unique raw identifiers per category per def (extraction-time cap).
SEM_CAP_PER_CATEGORY = 10


# ---------------------------------------------------------------------------
# tree-sitter language name → query S-expression
# Language names match LANGUAGE_MAP *values* in treesitter.py.
# ---------------------------------------------------------------------------

SEM_FACTS_QUERIES: dict[str, str] = {
    # -------------------------------------------------------------------
    # Python
    # -------------------------------------------------------------------
    "python": """
        (call function: (identifier) @sem_call)
        (call function: (attribute attribute: (identifier) @sem_call))
        (assignment left: (attribute attribute: (identifier) @sem_field))
        (return_statement (identifier) @sem_return)
        (raise_statement (call function: (identifier) @sem_raise))
        (raise_statement (identifier) @sem_raise)
        (pair key: (string) @sem_key)
    """,
    # -------------------------------------------------------------------
    # JavaScript
    # -------------------------------------------------------------------
    "javascript": """
        (call_expression function: (identifier) @sem_call)
        (call_expression
            function: (member_expression
                property: (property_identifier) @sem_call))
        (assignment_expression
            left: (member_expression
                property: (property_identifier) @sem_field))
        (return_statement (identifier) @sem_return)
        (throw_statement
            (new_expression constructor: (identifier) @sem_raise))
        (pair key: (property_identifier) @sem_key)
    """,
    # -------------------------------------------------------------------
    # TypeScript
    # -------------------------------------------------------------------
    "typescript": """
        (call_expression function: (identifier) @sem_call)
        (call_expression
            function: (member_expression
                property: (property_identifier) @sem_call))
        (assignment_expression
            left: (member_expression
                property: (property_identifier) @sem_field))
        (return_statement (identifier) @sem_return)
        (throw_statement
            (new_expression constructor: (identifier) @sem_raise))
        (pair key: (property_identifier) @sem_key)
    """,
    # -------------------------------------------------------------------
    # TSX (same grammar surface as TypeScript)
    # -------------------------------------------------------------------
    "tsx": """
        (call_expression function: (identifier) @sem_call)
        (call_expression
            function: (member_expression
                property: (property_identifier) @sem_call))
        (assignment_expression
            left: (member_expression
                property: (property_identifier) @sem_field))
        (return_statement (identifier) @sem_return)
        (throw_statement
            (new_expression constructor: (identifier) @sem_raise))
        (pair key: (property_identifier) @sem_key)
    """,
    # -------------------------------------------------------------------
    # Go
    # -------------------------------------------------------------------
    "go": """
        (call_expression function: (identifier) @sem_call)
        (call_expression
            function: (selector_expression
                field: (field_identifier) @sem_call))
        (return_statement
            (expression_list (identifier) @sem_return))
    """,
    # -------------------------------------------------------------------
    # Rust
    # -------------------------------------------------------------------
    "rust": """
        (call_expression function: (identifier) @sem_call)
        (call_expression
            function: (field_expression
                field: (field_identifier) @sem_call))
        (call_expression
            function: (scoped_identifier
                name: (identifier) @sem_call))
        (assignment_expression
            left: (field_expression
                field: (field_identifier) @sem_field))
        (return_expression (identifier) @sem_return)
    """,
    # -------------------------------------------------------------------
    # Java
    # -------------------------------------------------------------------
    "java": """
        (method_invocation name: (identifier) @sem_call)
        (assignment_expression
            left: (field_access
                field: (identifier) @sem_field))
        (return_statement (identifier) @sem_return)
        (throw_statement
            (object_creation_expression
                type: (type_identifier) @sem_raise))
    """,
    # -------------------------------------------------------------------
    # Ruby  (TODO: verify against tree-sitter-ruby grammar)
    # -------------------------------------------------------------------
    # Ruby queries are omitted for now — the grammar's `call` node
    # structure differs from the naive (call method: (identifier))
    # pattern.  Will be added after grammar verification.
}
