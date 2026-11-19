"""Bounded one-shot isolation for an optional tokenizer's network-backed cache loader."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys

_TIMEOUT = 5.0


class StoryTokenizerError(RuntimeError):
    """Story preparation failed without changing core execution or estimating missing data."""


async def count_tokens(texts: list[str]) -> list[int]:
    argv = [sys.executable, "-m", "backend.services.story.tokenizer"]
    windows_job = None
    if sys.platform == "win32":
        from backend.services.recipe.windows_process import WindowsCommandJob

        windows_job = WindowsCommandJob()
        argv = windows_job.launch_argv(argv, inherit_stdin=True)
    spawning = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    process = None
    communication = None
    try:
        try:
            async with asyncio.timeout(_TIMEOUT):
                process = await asyncio.shield(spawning)
                communication = asyncio.create_task(process.communicate(json.dumps(texts).encode()))
                output, error = await asyncio.shield(communication)
        except TimeoutError as exc:
            raise StoryTokenizerError(f"Story tokenizer exceeded its {_TIMEOUT:g}s loading/counting deadline") from exc
        if process.returncode:
            detail = error.decode(errors="replace").strip().splitlines()
            raise StoryTokenizerError(
                f"Story tokenizer failed: {detail[-1] if detail else f'worker exit {process.returncode}'}"
            )
        try:
            counts = json.loads(output)
        except (ValueError, UnicodeDecodeError) as exc:
            raise StoryTokenizerError("Story tokenizer returned invalid counts") from exc
        if (
            not isinstance(counts, list)
            or len(counts) != len(texts)
            or any(type(count) is not int or count < 0 for count in counts)
        ):
            raise StoryTokenizerError("Story tokenizer returned invalid counts")
        return counts
    finally:

        async def reap() -> None:
            try:
                owned = await spawning
                if owned.returncode is None:
                    # The worker may exit between the returncode check and kill.
                    with contextlib.suppress(ProcessLookupError):
                        owned.kill()
                if windows_job is not None:
                    # Fence the enrollment bootstrap before terminating the Job.
                    # Venv launchers can exit while the real Python worker survives.
                    while owned.returncode is None:
                        await asyncio.sleep(0.01)
                    windows_job.terminate()
                    while windows_job.active_processes():
                        await asyncio.sleep(0.01)
                if communication is not None:
                    await communication
                else:
                    await owned.communicate()
            finally:
                if windows_job is not None:
                    windows_job.close()

        cleanup = asyncio.create_task(reap())
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        cleanup.result()
        if interrupted:
            raise asyncio.CancelledError


if __name__ == "__main__":
    from contextlib import redirect_stdout

    from backend.services.story.service import _estimate_tokens

    inputs = json.load(sys.stdin)
    with redirect_stdout(sys.stderr):
        result = [_estimate_tokens(text) for text in inputs]
    json.dump(result, sys.stdout)
