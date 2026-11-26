"""Exercise the additive migration on an isolated database."""

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_0083_upgrade_and_downgrade_preserve_existing_tables(tmp_path):
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'migration.db'}")
    command.upgrade(config, "0082")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO projects (id, name, repo_paths, created_at, updated_at) "
                "VALUES ('project', 'Existing', '[]', '2026-01-01', '2026-01-01')"
            )
            before = tuple(conn.exec_driver_sql("SELECT * FROM projects").one())
        command.upgrade(config, "0083")
        schema = inspect(engine)
        assert {column["name"] for column in schema.get_columns("share_tokens")} == {
            "token_hash",
            "job_id",
            "created_at",
            "expires_at",
        }
        assert schema.get_foreign_keys("share_tokens")[0]["options"]["ondelete"] == "CASCADE"
        with engine.connect() as conn:
            assert tuple(conn.exec_driver_sql("SELECT * FROM projects").one()) == before
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        command.downgrade(config, "0082")
        assert "share_tokens" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert tuple(conn.exec_driver_sql("SELECT * FROM projects").one()) == before
    finally:
        engine.dispose()
