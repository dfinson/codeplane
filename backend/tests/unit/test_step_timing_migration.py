from datetime import UTC, datetime

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine

from alembic import command


def test_0081_preserves_old_timing_bytes_and_refuses_lossy_rollback(tmp_path):
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'timing.db'}")
    command.upgrade(config, "0080")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            projects = Table("projects", MetaData(), autoload_with=connection)
            connection.execute(projects.insert().values(
                id="project", name="Existing", repo_paths="[]", created_at=now, updated_at=now,
            ))
            connection.exec_driver_sql(
                """INSERT INTO jobs
                   (id,project_id,repo,prompt,state,base_ref,permission_mode,session_count,sdk,created_at,updated_at)
                   VALUES('job','project','/repo','Retain','completed','main','full_auto',1,'copilot',?,?)""",
                (now, now),
            )
            connection.exec_driver_sql(
                """INSERT INTO steps
                   (id,job_id,step_number,intent,status,trigger,tool_count,started_at,completed_at,duration_ms)
                   VALUES('old-step','job',1,'Existing','completed','turn',0,
                          '2001-02-03 04:05:06.000001','2001-02-03 04:05:07.000001',1000)"""
            )
            before = tuple(connection.exec_driver_sql("SELECT * FROM steps").one())
        command.upgrade(config, "0081")
        with engine.connect() as connection:
            after = tuple(connection.exec_driver_sql("SELECT * FROM steps").one())
            assert after[:-1] == before and after[-1] == "legacy_unrecorded"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        command.downgrade(config, "0080")
        with engine.connect() as connection:
            assert tuple(connection.exec_driver_sql("SELECT * FROM steps").one()) == before
        command.upgrade(config, "0081")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """INSERT INTO steps
                   (id,job_id,step_number,intent,status,trigger,tool_count,started_at,duration_ms,timing_source)
                   VALUES('unknown','job',2,'Source','source_ended','turn',0,NULL,NULL,'source')"""
            )
        with pytest.raises(RuntimeError, match="timing provenance exists"):
            command.downgrade(config, "0080")
        with engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT started_at,duration_ms,timing_source FROM steps WHERE id='unknown'"
            ).one()
            assert tuple(row) == (None, None, "source")
    finally:
        engine.dispose()
