import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect

from alembic import command
from backend.models.events import new_event
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.services.events.sse_manager import SSEConnection, SSEManager
from backend.tests.unit.test_approval_service import session_factory as session_factory


@pytest.mark.parametrize("raw", [{}, {"zero": 0, "present": False, "nested": {"missing": None, "text": "\u03bb"}}])
@pytest.mark.parametrize("zone", [UTC, timezone(timedelta(hours=2))])
async def test_canonical_raw_round_trip_and_sse_replay_preserve_supplied_data(session_factory, raw, zone) -> None:
    event = new_event(
        "job-1", "message.assistant", {"content": "Observed"}, raw_event=raw, timestamp=datetime.now(zone),
    )
    async with session_factory() as session:
        events = EventRepository(session)
        cursor = await events.append(event)
        await session.commit()
        assert await events.append(event) == cursor
        stored = await events.get(event.id)
        expected = event.model_copy(update={"timestamp": event.timestamp.astimezone(UTC)})
        assert stored.model_dump(mode="json") == expected.model_dump(mode="json")
        connection = SSEConnection(job_id="job-1")
        await SSEManager().replay_events(connection, events, JobRepository(session), 0)
        frame = connection.queue.get_nowait()
        body = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
        assert body["raw_event"] == raw
        assert f"id: {cursor}\n" in frame
        assert connection.queue.empty()


async def test_replay_never_reconstructs_missing_raw_history_or_replaces_known_raw(session_factory) -> None:
    async with session_factory() as session:
        repo = EventRepository(session)
        missing = new_event("job-1", "message.assistant", {"content": "Legacy"})
        cursor = await repo.append(missing)
        assert await repo.append(missing.model_copy(update={"raw_event": {"later": "known"}})) == cursor
        assert (await repo.get(missing.id)).raw_event is None
        known = new_event("job-1", "message.assistant", {}, raw_event={})
        await repo.append(known)
        with pytest.raises(ValueError, match="Conflicting content"):
            await repo.append(known.model_copy(update={"raw_event": {"changed": True}}))


def test_0079_preserves_old_bytes_and_refuses_lossy_downgrade(tmp_path) -> None:
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'events.db'}")
    command.upgrade(config, "0078")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            old = MetaData()
            projects = Table("projects", old, autoload_with=connection)
            connection.execute(projects.insert().values(
                id="project", name="Existing", repo_paths='["/repo"]', created_at=now, updated_at=now,
            ))
            connection.exec_driver_sql(
                """INSERT INTO jobs
                   (id,project_id,repo,prompt,state,base_ref,permission_mode,session_count,sdk,created_at,updated_at)
                   VALUES('job','project','/repo','Retain exactly','completed','main','full_auto',1,'copilot',?,?)""",
                (now, now),
            )
            connection.exec_driver_sql(
                """INSERT INTO events(event_id,job_id,kind,timestamp,payload,event_metadata)
                   VALUES('event','job','vendor.unknown',?,'{ "retain": 0 }','{}')""",
                (now,),
            )
            before = tuple(connection.exec_driver_sql("SELECT * FROM events").one())
        command.upgrade(config, "0079")
        with engine.connect() as connection:
            after = tuple(connection.exec_driver_sql("SELECT * FROM events").one())
            assert after[:-1] == before and after[-1] is None
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        command.downgrade(config, "0078")
        with engine.connect() as connection:
            assert "raw_event" not in {column["name"] for column in inspect(connection).get_columns("events")}
            assert tuple(connection.exec_driver_sql("SELECT * FROM events").one()) == before
        command.upgrade(config, "0079")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """INSERT INTO events(event_id,job_id,kind,timestamp,payload,event_metadata,raw_event)
                   VALUES('new-event','job','vendor.unknown',?,'{}','{}','{}')""",
                (now,),
            )
        with pytest.raises(RuntimeError, match="Raw event history is present"):
            command.downgrade(config, "0078")
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT raw_event FROM events WHERE event_id='new-event'"
            ).scalar() == "{}"
            assert connection.exec_driver_sql(
                "SELECT raw_event FROM events WHERE event_id='event'"
            ).scalar() is None
    finally:
        engine.dispose()
