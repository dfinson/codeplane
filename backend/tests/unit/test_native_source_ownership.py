"""Real OS ownership and fresh-v2 observation using default dependencies only."""

import asyncio
import json
import sys

import pytest

from backend.models.db import Base
from backend.models.domain import StateConflictError
from backend.persistence.database import create_engine, create_session_factory, serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.ingest.source_ownership import hold_native_source
from backend.tests.unit.conftest import make_job
from backend.tests.unit.test_native_jsonl_watch import _checkpoint, _watcher

_CHILD = """
import json, sys
from pathlib import Path
from backend.models.domain import StateConflictError
from backend.persistence.database import create_engine, create_session_factory
from backend.services.ingest.source_ownership import hold_native_source
factory = create_session_factory(create_engine(Path(sys.argv[1])))
try:
    with hold_native_source(factory, sys.argv[2]):
        print(json.dumps({"status": "acquired", "platform": sys.platform}), flush=True)
        if sys.argv[3] == "hold":
            sys.stdin.buffer.read(1)
except StateConflictError:
    print(json.dumps({"status": "blocked", "platform": sys.platform}), flush=True)
"""


@pytest.fixture
async def stores(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.persistence.database._write_lock", asyncio.Lock())
    engines = []
    result = []
    try:
        for name in ("first.db", "second.db"):
            path = tmp_path / name
            engine = create_engine(path)
            engines.append(engine)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = create_session_factory(engine)
            async with serialized_write(factory) as session:
                await ProjectRepository(session).create("proj-1", "Ownership fixture", [])
                for job_id in ("job-1", "job-2"):
                    await JobRepository(session).create(make_job(id=job_id))
            result.append((factory, path))
        yield result
    finally:
        for engine in engines:
            await engine.dispose()


async def _child(path, job_id, mode="probe"):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        str(path),
        job_id,
        mode,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _finish(process, data=None):
    try:
        return await asyncio.wait_for(process.communicate(data), 10)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise


async def _probe(path, job_id):
    process = await _child(path, job_id)
    output, error = await _finish(process)
    assert process.returncode == 0, (output, error)
    result = json.loads(output)
    assert result["platform"] == sys.platform
    return result["status"]


@pytest.mark.parametrize("release", ["graceful", "terminated"])
async def test_child_owner_blocks_until_release(stores, release):
    (factory, database), _ = stores
    process = await _child(database, "job-1", "hold")
    try:
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
        assert ready == {"status": "acquired", "platform": sys.platform}
        with pytest.raises(StateConflictError, match="already owned"), hold_native_source(factory, "job-1"):
            pytest.fail("A child-owned source must not admit another owner")
        if release == "terminated":
            process.terminate()
        output, error = await _finish(process, b"x" if release == "graceful" else None)
        assert process.returncode == 0 if release == "graceful" else process.returncode != 0
        assert not output and not error
        with hold_native_source(factory, "job-1"):
            assert await _probe(database, "job-1") == "blocked"
        assert await _probe(database, "job-1") == "acquired"
    finally:
        if process.returncode is None:
            process.kill()
            await _finish(process)


async def test_parent_owner_keeps_other_jobs_and_databases_independent(stores):
    (factory, database), (_, other_database) = stores
    with hold_native_source(factory, "job-1"):
        assert await _probe(database, "job-1") == "blocked"
        assert await _probe(database, "job-2") == "acquired"
        assert await _probe(other_database, "job-1") == "acquired"
    assert await _probe(database, "job-1") == "acquired"


@pytest.mark.parametrize("framework", ["copilot", "claude"])
async def test_fresh_v2_watcher_owns_source_across_processes(stores, tmp_path, framework):
    (factory, database), _ = stores
    watcher = await _watcher(factory, framework)
    raw = (
        {"type": "assistant.message", "data": {"content": "Observed"}}
        if framework == "copilot"
        else {"type": "assistant", "message": {"content": [{"type": "text", "text": "Observed"}]}}
    )
    path = tmp_path / "source.jsonl"
    path.write_bytes(json.dumps(raw).encode() + b"\n")
    task = asyncio.create_task(watcher._tail_traceforge_events("source", "native", path))
    try:
        checkpoint = await _checkpoint(factory, 1)
        assert checkpoint["version"] == 2
        assert checkpoint["decoded_event_count"] == checkpoint["emitted_event_count"] == 1
        assert await _probe(database, "native") == "blocked"
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert await _probe(database, "native") == "acquired"
    async with factory() as session:
        assert await JobRepository(session).get_ingest_checkpoint("native") == checkpoint
        history = await EventRepository(session).list_after(0, job_id="native")
    assert [row.event.payload["content"] for row in history] == ["Observed"]
    assert history[0].event.raw_event == raw
