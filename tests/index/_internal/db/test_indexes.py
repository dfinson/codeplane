"""Tests for additional index creation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from codeplane.index._internal.db.indexes import (
    ADDITIONAL_INDEXES,
    create_additional_indexes,
    drop_additional_indexes,
)


class TestAdditionalIndexes:
    """Tests for ADDITIONAL_INDEXES constant."""

    def test_indexes_is_list(self) -> None:
        """ADDITIONAL_INDEXES is a list."""
        assert isinstance(ADDITIONAL_INDEXES, list)

    def test_indexes_are_sql_strings(self) -> None:
        """All entries are SQL CREATE INDEX statements."""
        for sql in ADDITIONAL_INDEXES:
            assert isinstance(sql, str)
            assert sql.startswith("CREATE INDEX IF NOT EXISTS")

    def test_indexes_have_expected_tables(self) -> None:
        """Indexes reference expected tables."""
        expected_tables = {
            "def_facts",
            "ref_facts",
            "scope_facts",
            "import_facts",
            "local_bind_facts",
            "export_surfaces",
            "contexts",
            "anchor_groups",
        }
        found_tables = set()
        for sql in ADDITIONAL_INDEXES:
            # Extract table name from "ON table_name("
            parts = sql.split(" ON ")
            if len(parts) > 1:
                table_part = parts[1].split("(")[0]
                found_tables.add(table_part)

        assert found_tables == expected_tables

    def test_indexes_have_unique_names(self) -> None:
        """All index names are unique."""
        names = []
        for sql in ADDITIONAL_INDEXES:
            # Extract index name from "CREATE INDEX IF NOT EXISTS idx_name ON"
            parts = sql.split(" ")
            parts.index("idx_name") if "idx_name" in parts else None
            for _i, part in enumerate(parts):
                if part.startswith("idx_"):
                    names.append(part)
                    break
        assert len(names) == len(set(names)), "Duplicate index names found"


class TestCreateAdditionalIndexes:
    """Tests for create_additional_indexes function."""

    def test_creates_all_indexes(self) -> None:
        """create_additional_indexes executes all index SQL."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        create_additional_indexes(mock_engine)

        # Should execute each index SQL
        assert mock_conn.execute.call_count == len(ADDITIONAL_INDEXES)
        mock_conn.commit.assert_called_once()

    def test_uses_text_for_raw_sql(self) -> None:
        """create_additional_indexes uses text() for raw SQL."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch("codeplane.index._internal.db.indexes.text") as mock_text:
            create_additional_indexes(mock_engine)

            # Should call text() for each SQL statement
            assert mock_text.call_count == len(ADDITIONAL_INDEXES)


class TestDropAdditionalIndexes:
    """Tests for drop_additional_indexes function."""

    def test_drops_all_indexes(self) -> None:
        """drop_additional_indexes drops all expected indexes."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        drop_additional_indexes(mock_engine)

        # Should execute DROP for each index
        expected_count = 9  # Number of index names in the function
        assert mock_conn.execute.call_count == expected_count
        mock_conn.commit.assert_called_once()

    def test_uses_drop_if_exists(self) -> None:
        """drop_additional_indexes uses DROP INDEX IF EXISTS."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch("codeplane.index._internal.db.indexes.text") as mock_text:
            drop_additional_indexes(mock_engine)

            # All calls should be DROP INDEX IF EXISTS
            for call_args in mock_text.call_args_list:
                sql = call_args[0][0]
                assert sql.startswith("DROP INDEX IF EXISTS")

    def test_drops_expected_index_names(self) -> None:
        """drop_additional_indexes drops the correct index names."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        expected_names = {
            "idx_def_facts_file_name",
            "idx_ref_facts_file_target",
            "idx_ref_facts_target_tier",
            "idx_scope_facts_file",
            "idx_import_facts_file",
            "idx_local_bind_facts_scope",
            "idx_export_surfaces_unit",
            "idx_contexts_family_status",
            "idx_anchor_groups_unit",
        }

        with patch("codeplane.index._internal.db.indexes.text") as mock_text:
            drop_additional_indexes(mock_engine)

            dropped_names = set()
            for call_args in mock_text.call_args_list:
                sql = call_args[0][0]
                # Extract name from "DROP INDEX IF EXISTS name"
                name = sql.replace("DROP INDEX IF EXISTS ", "")
                dropped_names.add(name)

            assert dropped_names == expected_names
