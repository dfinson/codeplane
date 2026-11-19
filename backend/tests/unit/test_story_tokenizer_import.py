"""Cold-cache imports must not initialize the optional network-backed tokenizer."""

import asyncio
import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import psutil
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import update

from backend.models.db import JobRow, TrailNodeRow
from backend.tests.unit.test_approval_service import session_factory as session_factory


def _assert_worker_exited(pid: int) -> None:
    # Windows may retain a terminated PID while its handles are still open.
    with contextlib.suppress(psutil.NoSuchProcess):
        psutil.Process(pid).wait(timeout=0)


@pytest.fixture
def blocked_tokenizer(tmp_path, monkeypatch):
    started, release = tmp_path / "started", tmp_path / "release"
    (tmp_path / "tiktoken.py").write_text(
        "import os, time\n"
        "from pathlib import Path\n"
        "def get_encoding(name):\n"
        f"    Path({str(started)!r}).write_text(str(os.getpid()))\n"
        f"    while not Path({str(release)!r}).exists(): time.sleep(0.01)\n"
        "    raise OSError('controlled tokenizer cache loader failure')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "tiktoken", raising=False)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, sys.path)))
    from backend.services.story.service import _get_tokenizer

    _get_tokenizer.cache_clear()
    yield started, release
    release.touch()
    _get_tokenizer.cache_clear()


async def test_populated_story_drain_does_not_stall_http_or_shutdown(session_factory, blocked_tokenizer):
    """The loopback probe runs while the real populated drain is inside its tokenizer loader."""
    from backend.services.story.service import StoryService

    started, release = blocked_tokenizer
    async with session_factory() as session:
        await session.execute(update(JobRow).where(JobRow.id == "job-1").values(state="review"))
        session.add(
            TrailNodeRow(
                id="story-node",
                job_id="job-1",
                seq=1,
                anchor_seq=1,
                kind="insight",
                timestamp=datetime.now(UTC),
                enrichment="complete",
                intent="Inert populated story",
            )
        )
        await session.commit()

    class NoModel:
        called = 0

        async def complete(self, prompt):
            self.called += 1
            raise AssertionError("Blocked tokenization must never invoke a model")

    completer = NoModel()
    service = StoryService(completer, session_factory=session_factory)
    drain = None

    @contextlib.asynccontextmanager
    async def lifespan(app):
        nonlocal drain
        drain = asyncio.create_task(service.drain_loop())
        try:
            yield
        finally:
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)

    @app.get("/ready")
    async def ready():
        return {"ready": True}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    finished, probed = threading.Event(), threading.Event()
    result: dict[str, Any] = {}

    def probe():
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            deadline = time.monotonic() + 5
            while not started.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Populated drain never reached tokenizer loading")
                time.sleep(0.005)
            began = time.monotonic()
            connection.request("GET", "/ready")
            response = connection.getresponse()
            result.update(status=response.status, body=response.read(), elapsed=time.monotonic() - began)
        except Exception as exc:
            result["error"] = repr(exc)
        finally:
            connection.close()
            probed.set()
            # Release the synchronous baseline after failure so reproduction cannot hang pytest.
            finished.wait(2)
            release.touch()

    watcher = threading.Thread(target=probe)
    watcher.start()
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        assert await asyncio.to_thread(probed.wait, 6)
        assert result.get("status") == 200, result
        assert result["elapsed"] < 0.5
        assert not release.exists(), "Tokenizer loader must still be blocked during the HTTP response"
        began = time.monotonic()
        server.should_exit = True
        await asyncio.wait_for(serving, 1)
        assert time.monotonic() - began < 1 and drain.done()
        assert not release.exists(), "Shutdown must reap blocked loading without waiting for its completion"
        pid = int(started.read_text())
        assert pid != os.getpid()
        _assert_worker_exited(pid)
        assert completer.called == 0
    finally:
        finished.set()
        release.touch()
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        watcher.join(timeout=3)
        listener.close()


@pytest.mark.parametrize("block_before_import", [False, True])
async def test_story_tokenizer_deadline_is_explicit_and_reaps_worker(
    blocked_tokenizer, monkeypatch, block_before_import
):
    from backend.services.story import service, tokenizer

    started, release = blocked_tokenizer
    if block_before_import:
        (started.parent / "sitecustomize.py").write_text("import time\ntime.sleep(10)\n")
    processes: list[asyncio.subprocess.Process] = []
    spawn = asyncio.create_subprocess_exec

    async def record_worker(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(tokenizer.asyncio, "create_subprocess_exec", record_worker)
    monkeypatch.setattr(tokenizer, "_TIMEOUT", 0.5)
    completer = AsyncMock()
    story = service.StoryService(completer)
    began = time.monotonic()
    with pytest.raises(tokenizer.StoryTokenizerError, match="deadline"):
        await story._generate_passes(
            refs=[{"spanId": "s1", "why": "populated"}],
            ctx={"job": {"prompt": "inert"}},
            system="system",
            max_input_tokens=1000,
            job_id="job-1",
        )
    assert time.monotonic() - began < 1.5
    assert not release.exists() and not story._tokenizer_lock.locked()
    completer.complete.assert_not_called()
    assert len(processes) == 1 and processes[0].returncode is not None
    if block_before_import:
        assert not started.exists()
    _assert_worker_exited(processes[0].pid)


async def test_loader_error_remains_explicit_without_estimated_story(blocked_tokenizer):
    from backend.services.story import service, tokenizer
    from backend.tests.unit.test_story_service import _make_session_mock

    started, release = blocked_tokenizer
    release.touch()
    session = _make_session_mock(story_text=None)
    completer = AsyncMock()
    story = service.StoryService(completer)
    with pytest.raises(tokenizer.StoryTokenizerError, match="controlled tokenizer cache loader failure"):
        await story.get_or_generate(session, "job-1")
    assert not story._gen_locks and not story._tokenizer_lock.locked()
    session.commit.assert_not_called()
    completer.complete.assert_not_called()
    _assert_worker_exited(int(started.read_text()))


async def test_overlapping_story_counts_keep_one_owned_worker_through_repeated_cancellation(
    blocked_tokenizer, monkeypatch
):
    from backend.services.story import service, tokenizer

    started, release = blocked_tokenizer
    spawn = AsyncMock(wraps=asyncio.create_subprocess_exec)
    monkeypatch.setattr(tokenizer.asyncio, "create_subprocess_exec", spawn)
    story = service.StoryService(AsyncMock())
    first = asyncio.create_task(story._count_tokens(["first"]))
    second = None
    try:
        async with asyncio.timeout(3):
            while not started.exists():
                await asyncio.sleep(0.005)
        pid = int(started.read_text())
        second = asyncio.create_task(story._count_tokens(["second"]))
        await asyncio.sleep(0.03)
        assert spawn.call_count == 1
        second.cancel()  # A queued caller must not start another worker.
        first.cancel()
        asyncio.get_running_loop().call_soon(first.cancel)
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(asyncio.CancelledError):
            await second
        assert spawn.call_count == 1 and not release.exists() and not story._tokenizer_lock.locked()
        _assert_worker_exited(pid)
    finally:
        release.touch()
        first.cancel()
        if second is not None:
            second.cancel()
        await asyncio.gather(*([first, second] if second is not None else [first]), return_exceptions=True)


async def test_missing_optional_tokenizer_still_uses_logged_estimate_without_worker(monkeypatch):
    from backend.services.story import service

    service._get_tokenizer.cache_clear()
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    worker = AsyncMock(side_effect=AssertionError("Missing optional package needs no worker"))
    monkeypatch.setattr(service, "count_tokens", worker)
    logger = MagicMock()
    monkeypatch.setattr(service, "log", logger)
    try:
        assert await service.StoryService(AsyncMock())._count_tokens(["a" * 400, ""]) == [100, 0]
        worker.assert_not_called()
        logger.warning.assert_called_once()
        assert logger.warning.call_args.kwargs["fallback"] == "character_estimate"
    finally:
        service._get_tokenizer.cache_clear()


def test_cold_cache_story_and_lifespan_imports_do_not_load_encoding(tmp_path):
    script = """
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

attempts = []
def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        attempts.append(event)
        raise PermissionError("Story tokenizer test blocks network")
sys.addaudithook(deny_network)

from backend.services.story.service import StoryService, _estimate_tokens
import backend.lifespan

class NoModel:
    async def complete(self, prompt):
        raise AssertionError("Import/construction must not generate a story")

StoryService(NoModel())

async def start_empty_story_drain():
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from backend.models.db import Base

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    queried = asyncio.Event()
    def after_query(connection, cursor, statement, parameters, context, many):
        if "story_text IS NULL" in statement:
            queried.set()
    event.listen(engine.sync_engine, "after_cursor_execute", after_query)
    task = asyncio.create_task(StoryService(NoModel(), async_sessionmaker(engine)).drain_loop())
    try:
        await asyncio.wait_for(queried.wait(), 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
asyncio.run(start_empty_story_drain())
assert not attempts, attempts
assert not list(Path(os.environ["TIKTOKEN_CACHE_DIR"]).rglob("*"))
installed = importlib.util.find_spec("tiktoken") is not None
if installed:
    import tiktoken
    import tiktoken.registry
    assert "cl100k_base" not in tiktoken.registry.ENCODINGS
    for _ in range(2):
        before = len(attempts)
        try:
            _estimate_tokens("a" * 400)
        except OSError:
            assert len(attempts) > before
        else:
            raise AssertionError("An encoding download failure must not become an estimate")
    assert "cl100k_base" not in tiktoken.registry.ENCODINGS
    from unittest.mock import patch
    encoding = tiktoken.Encoding(
        name="local-byte-fixture", pat_str=r"(?s).",
        mergeable_ranks={bytes([value]): value for value in range(256)}, special_tokens={},
    )
    with patch("tiktoken.get_encoding", return_value=encoding) as select:
        for text in ("ASCII", "\\u03bb", ""):
            assert _estimate_tokens(text) == len(encoding.encode(text)) == len(text.encode("utf-8"))
        select.assert_called_once_with("cl100k_base")
    async def cached_counting():
        from unittest.mock import AsyncMock
        from backend.services.story import service
        with patch.object(service, "count_tokens", AsyncMock(side_effect=AssertionError("Already cached"))):
            assert await StoryService(NoModel())._count_tokens(["ASCII", "\\u03bb", ""]) == [5, 2, 0]
    asyncio.run(cached_counting())
    bootstrap = Path(os.environ["CODEPLANE_HOME"]) / "cached-worker"
    bootstrap.mkdir(parents=True)
    (bootstrap / "sitecustomize.py").write_text(
        "import sys, tiktoken, tiktoken.registry\\n"
        "def blocked(event, args):\\n"
        "    if event in {'socket.connect', 'socket.getaddrinfo'}: raise AssertionError('No network')\\n"
        "sys.addaudithook(blocked)\\n"
        "tiktoken.registry.ENCODINGS['cl100k_base'] = tiktoken.Encoding("
        "name='local-byte-fixture', pat_str=r'(?s).', "
        "mergeable_ranks={bytes([i]): i for i in range(256)}, special_tokens={})\\n"
    )
    os.environ["PYTHONPATH"] = str(bootstrap) + os.pathsep + os.pathsep.join(sys.path)
    from backend.services.story.tokenizer import count_tokens
    assert asyncio.run(count_tokens(["ASCII", "\\u03bb", ""])) == [5, 2, 0]
    from backend.services.story import service
    service._get_tokenizer.cache_clear()
    class LocalCompleter:
        def __init__(self):
            self.prompts = []
        async def complete(self, prompt):
            self.prompts.append(prompt)
            return "Inert narrative [[1]]"
    async def exact_multipass():
        completer = LocalCompleter()
        with patch("tiktoken.get_encoding", side_effect=AssertionError("Tokenizer ran on the event loop")):
            await StoryService(completer)._generate_passes(
                refs=[{"spanId": str(i), "snippet": "x" * 2000} for i in range(3)],
                ctx={"job": {"prompt": "inert"}}, system="system", max_input_tokens=1000, job_id="fixture",
            )
        assert len(completer.prompts) == 3
    asyncio.run(exact_multipass())
else:
    assert _estimate_tokens("a" * 400) == 100
    assert not attempts
assert not list(Path(os.environ["TIKTOKEN_CACHE_DIR"]).rglob("*"))
print(json.dumps({"installed_tiktoken": installed, "blocked_explicit_requests": len(attempts)}))
"""
    environment = {
        **os.environ,
        "TIKTOKEN_CACHE_DIR": str(tmp_path / "empty-tokenizer-cache"),
        "DATA_GYM_CACHE_DIR": str(tmp_path / "empty-tokenizer-cache"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "CODEPLANE_HOME": str(tmp_path / "codeplane"),
    }
    result = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout.splitlines()[-1])
    assert (
        report["blocked_explicit_requests"] >= 2
        if report["installed_tiktoken"]
        else report["blocked_explicit_requests"] == 0
    )
