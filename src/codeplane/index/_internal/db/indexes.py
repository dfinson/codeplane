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
    # Composite indexes for common Tier 1 query patterns
    # DefFact queries by file and name
    "CREATE INDEX IF NOT EXISTS idx_def_facts_file_name ON def_facts(file_id, name)",
    # RefFact queries by file and target
    "CREATE INDEX IF NOT EXISTS idx_ref_facts_file_target ON ref_facts(file_id, target_def_uid)",
    "CREATE INDEX IF NOT EXISTS idx_ref_facts_target_tier ON ref_facts(target_def_uid, ref_tier)",
    # ScopeFact queries by file
    "CREATE INDEX IF NOT EXISTS idx_scope_facts_file ON scope_facts(file_id)",
    # ImportFact queries by file
    "CREATE INDEX IF NOT EXISTS idx_import_facts_file ON import_facts(file_id)",
    # LocalBindFact queries by scope
    "CREATE INDEX IF NOT EXISTS idx_local_bind_facts_scope ON local_bind_facts(scope_id)",
    # ExportSurface queries by unit
    "CREATE INDEX IF NOT EXISTS idx_export_surfaces_unit ON export_surfaces(unit_id)",
    # Context queries by name and status
    "CREATE INDEX IF NOT EXISTS idx_contexts_family_status ON contexts(language_family, probe_status)",
    # AnchorGroup queries by unit (not file)
    "CREATE INDEX IF NOT EXISTS idx_anchor_groups_unit ON anchor_groups(unit_id)",
]


def create_additional_indexes(engine: Engine) -> None:
    """Create additional composite indexes for Tier 1 fact tables."""
    with engine.connect() as conn:
        for sql in ADDITIONAL_INDEXES:
            conn.execute(text(sql))
        conn.commit()


def drop_additional_indexes(engine: Engine) -> None:
    """Drop additional indexes (for testing/reset)."""
    index_names = [
        "idx_def_facts_file_name",
        "idx_ref_facts_file_target",
        "idx_ref_facts_target_tier",
        "idx_scope_facts_file",
        "idx_import_facts_file",
        "idx_local_bind_facts_scope",
        "idx_export_surfaces_unit",
        "idx_contexts_family_status",
        "idx_anchor_groups_unit",
    ]
    with engine.connect() as conn:
        for name in index_names:
            conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        conn.commit()
