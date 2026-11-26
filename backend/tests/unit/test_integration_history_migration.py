from datetime import UTC, datetime

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from alembic import command
from backend.models.db import ApprovalRow, ProjectRow


def test_0077_retains_job_approval_and_recipe_history_and_only_relaxes_observations(tmp_path) -> None:
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'history.db'}")
    command.upgrade(config, "0076")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            connection.execute(
                ProjectRow.__table__.insert().values(
                    id="project",
                    name="Existing",
                    repo_paths='["/repo"]',
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.exec_driver_sql(
                """INSERT INTO jobs
                   (id,project_id,repo,prompt,state,base_ref,permission_mode,session_count,sdk,created_at,updated_at)
                   VALUES('job','project','/repo','Retain exactly','completed','main','full_auto',1,'copilot',?,?)""",
                (now, now),
            )
            connection.execute(
                ApprovalRow.__table__.insert().values(
                    id="approval",
                    job_id="job",
                    description="Retain pending approval",
                    requested_at=now,
                )
            )
            connection.exec_driver_sql(
                "INSERT INTO recipe_definitions VALUES(1,'definition','{}',1,1,?,?)",
                (now, now),
            )
            connection.exec_driver_sql(
                "INSERT INTO recipe_revisions VALUES(1,'revision','definition',1,'{}','{}','checksum',?)",
                (now,),
            )
            connection.exec_driver_sql(
                """INSERT INTO recipe_runs
                   (id,job_id,revision_id,snapshot_json,snapshot_checksum,inputs_json,state,version,created_at,updated_at)
                   VALUES('run','job','revision','{}','checksum','{}','succeeded',1,?,?)""",
                (now, now),
            )
            before = tuple(connection.exec_driver_sql("SELECT * FROM jobs WHERE id='job'").one())
        command.upgrade(config, "0077")
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            after = tuple(connection.exec_driver_sql("SELECT * FROM jobs WHERE id='job'").one())
            assert after[: len(before)] == before
            assert connection.exec_driver_sql("SELECT resolution FROM approvals WHERE id='approval'").scalar() is None
            assert connection.exec_driver_sql("SELECT job_id FROM recipe_runs WHERE id='run'").scalar() == "job"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            with pytest.raises(IntegrityError):
                connection.exec_driver_sql("UPDATE jobs SET repo=NULL WHERE id='job'")
            connection.exec_driver_sql(
                """INSERT INTO integration_bindings
                   (id,project_id,adapter_id,mode,created_at,updated_at)
                   VALUES('binding','project','crewai','observation',?,?)""",
                (now, now),
            )
            connection.exec_driver_sql(
                """INSERT INTO jobs
                   (id,project_id,repo,base_ref,prompt,state,source,observation_only,integration_binding_id,
                    permission_mode,session_count,created_at,updated_at,sdk)
                   VALUES('observed','project',NULL,NULL,'','running','integration',1,'binding',
                          'full_auto',1,?,?,'crewai')""",
                (now, now),
            )
            assert connection.exec_driver_sql("SELECT repo FROM jobs WHERE id='observed'").scalar() is None
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        with pytest.raises(RuntimeError, match="history is present"):
            command.downgrade(config, "0076")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT id FROM jobs WHERE id='observed'").scalar() == "observed"
            assert "integration_bindings" in inspect(connection).get_table_names()
    finally:
        engine.dispose()
