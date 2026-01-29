"""Additional index creation for query performance.

These indexes complement the basic indexes defined in SQLModel Field()
declarations. They are composite indexes for common query patterns that
cannot be expressed via Field(index=True).

Call create_additional_indexes() after Database.create_all().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Engine


ADDITIONAL_INDEXES = [
    # Composite indexes for common query patterns
    "CREATE INDEX IF NOT EXISTS idx_occurrences_context_file ON occurrences(context_id, file_id)",
    "CREATE INDEX IF NOT EXISTS idx_occurrences_context_symbol ON occurrences(context_id, symbol_id)",
    "CREATE INDEX IF NOT EXISTS idx_exports_context_file ON exports(context_id, file_id)",
    "CREATE INDEX IF NOT EXISTS idx_contexts_family_status ON contexts(language_family, probe_status)",
    "CREATE INDEX IF NOT EXISTS idx_refresh_jobs_context_status ON refresh_jobs(context_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_edges_source_context ON edges(source_file, context_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_target_context ON edges(target_file, context_id)",
    # Unique constraint for decision cache
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_cache_unique ON decision_cache(ambiguity_signature, repo_head)",
    # Symbol lookup by file and name (for Read-After-Write)
    "CREATE INDEX IF NOT EXISTS idx_symbols_file_name_line ON symbols(file_id, name, line)",
    # File semantic facts lookup
    "CREATE INDEX IF NOT EXISTS idx_file_semantic_facts_context ON file_semantic_facts(context_id)",
]


def create_additional_indexes(engine: Engine) -> None:
    """
    Create additional composite indexes.

    Call this after Database.create_all() to add performance indexes
    that cannot be expressed via SQLModel Field() declarations.
    """
    with engine.connect() as conn:
        for sql in ADDITIONAL_INDEXES:
            conn.execute(text(sql))
        conn.commit()


def drop_additional_indexes(engine: Engine) -> None:
    """Drop additional indexes (for testing/reset)."""
    index_names = [
        "idx_occurrences_context_file",
        "idx_occurrences_context_symbol",
        "idx_exports_context_file",
        "idx_contexts_family_status",
        "idx_refresh_jobs_context_status",
        "idx_edges_source_context",
        "idx_edges_target_context",
        "idx_decision_cache_unique",
        "idx_symbols_file_name_line",
        "idx_file_semantic_facts_context",
    ]
    with engine.connect() as conn:
        for name in index_names:
            conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        conn.commit()
