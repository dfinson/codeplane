"""Cold-cache imports must not initialize the optional network-backed tokenizer."""

import json
import os
import subprocess
import sys


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
