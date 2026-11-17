import asyncio
from datetime import UTC, datetime

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from backend.models.db import Base
from backend.models.domain import StateConflictError
from backend.models.schemas.integrations import ObservationRecordBatch
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.ingest.record_service import ObservationRecords


async def test_concurrent_channels_cas_one_shared_projection_without_partial_progress(tmp_path, monkeypatch) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            projects = ProjectRepository(session)
            await projects.create("project", "Project", [])
            job = await HistoryRegistration(
                IntegrationRepository(session),
                JobRepository(session),
                projects,
            ).register(binding_id="binding", project_id="project", format_id="copilot", external_session_id="source")
            await session.commit()
        ready = asyncio.Event()
        arrived = 0
        original = IntegrationRepository.commit_records

        async def simultaneous_commit(repo, **kwargs):
            nonlocal arrived
            arrived += 1
            if arrived == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
            return await original(repo, **kwargs)

        async def append(source):
            async with factory() as session:
                return await ObservationRecords(
                    IntegrationRepository(session),
                    JobRepository(session),
                    EventBus(),
                ).append(
                    "binding",
                    ObservationRecordBatch(
                        source_key=source,
                        start_index=0,
                        records=[{"type": "assistant.message", "data": {"content": source}}],
                    ),
                )

        with monkeypatch.context() as scoped:
            scoped.setattr(IntegrationRepository, "commit_records", simultaneous_commit)
            results = await asyncio.gather(append("first"), append("second"), return_exceptions=True)
        assert sum(isinstance(result, StateConflictError) for result in results) == 1, results
        async with factory() as session:
            bindings = IntegrationRepository(session)
            assert (await bindings.projection("binding")).version == 1
            stored = [item.event for item in await EventRepository(session).list_after(0, job_id=job.id)]
            assert len([event for event in stored if event.kind == "message.assistant"]) == 1
            assert len([event for event in stored if event.kind == "integration.batch.accepted"]) == 1
        losing_source = "first" if isinstance(results[0], BaseException) else "second"
        retried = await append(losing_source)
        assert retried.projection_version == 2
    finally:
        await engine.dispose()


def test_0080_retains_ambiguous_source_checkpoints_and_refuses_lossy_downgrade(tmp_path) -> None:
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'projection.db'}")
    command.upgrade(config, "0079")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            old = MetaData()
            projects = Table("projects", old, autoload_with=connection)
            connection.execute(
                projects.insert().values(
                    id="project",
                    name="Existing",
                    repo_paths="[]",
                    created_at=now,
                    updated_at=now,
                )
            )
            bindings = Table("integration_bindings", old, autoload_with=connection)
            connection.execute(
                bindings.insert().values(
                    id="binding",
                    project_id="project",
                    adapter_id="native",
                    mode="observation",
                    created_at=now,
                    updated_at=now,
                )
            )
            sources = Table("integration_source_checkpoints", old, autoload_with=connection)
            connection.execute(
                sources.insert().values(
                    binding_id="binding",
                    source_key="old-source",
                    parser_revision="old-parser",
                    record_count=1,
                    prefix_sha256="a" * 64,
                    canonical_sha256="b" * 64,
                    parser_sha256="c" * 64,
                    buffer_json='{ "unresolved_projection": true }',
                    state="accepted",
                    version=3,
                    updated_at=now,
                )
            )
            before = tuple(connection.exec_driver_sql("SELECT * FROM integration_source_checkpoints").one())
        command.upgrade(config, "0080")
        with engine.connect() as connection:
            assert tuple(connection.exec_driver_sql("SELECT * FROM integration_source_checkpoints").one()) == before
            assert connection.exec_driver_sql("SELECT count(*) FROM integration_projection_checkpoints").scalar() == 0
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        command.downgrade(config, "0079")
        command.upgrade(config, "0080")
        with engine.begin() as connection:
            projection = Table("integration_projection_checkpoints", MetaData(), autoload_with=connection)
            connection.execute(
                projection.insert().values(
                    binding_id="binding",
                    parser_revision="new-parser",
                    parser_sha256="a" * 64,
                    canonical_sha256="b" * 64,
                    buffer_json="{}",
                    state="reconciliation_required",
                    error_code="explicit_comparison_required",
                    version=1,
                    updated_at=now,
                )
            )
        with pytest.raises(RuntimeError, match="projection history is present"):
            command.downgrade(config, "0079")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM integration_projection_checkpoints").scalar() == 1
            assert tuple(connection.exec_driver_sql("SELECT * FROM integration_source_checkpoints").one()) == before
    finally:
        engine.dispose()
