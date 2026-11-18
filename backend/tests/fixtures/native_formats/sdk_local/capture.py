"""Capture actual SDK callbacks/serialization with inert local input, never a model conversation.

Run in the isolated optional environment described by provenance.json. Raw files
are written once, not normalized after capture. No CodePlane decoder is imported.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import sys
from datetime import UTC, datetime
from importlib.metadata import distributions, version
from pathlib import Path
from uuid import uuid4


def emit(stream, discriminator, kind, **fields):
    row = {discriminator: kind, "timestamp": datetime.now(UTC).isoformat(), **fields}
    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode() + b"\n")
    stream.flush()


def source_hash(symbol):
    path = Path(inspect.getsourcefile(symbol))
    return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


async def langchain_capture(destination):
    from langchain_core.callbacks import BaseCallbackHandler  # type: ignore[import-not-found]
    from langchain_core.tools import StructuredTool  # type: ignore[import-not-found]

    calls = []

    def local(value: str) -> dict:
        """Return inert input or raise the requested local error."""
        calls.append(value)
        if value == "fail":
            raise ValueError("local rejection")
        return {"echo": value, "count": 0, "ok": False}

    async def alocal(value: str) -> dict:
        return local(value)

    with destination.open("xb") as stream:

        class Capture(BaseCallbackHandler):
            run_inline = True
            raise_error = True

            def __init__(self):
                self.names = {}

            def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
                name = kwargs.get("name") or serialized["name"]
                self.names[run_id] = name
                emit(
                    stream,
                    "event",
                    "on_tool_start",
                    run_id=str(run_id),
                    name=name,
                    serialized=serialized,
                    input_str=input_str,
                    inputs=kwargs.get("inputs"),
                    tags=kwargs.get("tags"),
                    metadata=kwargs.get("metadata"),
                )

            def on_tool_end(self, output, *, run_id, **kwargs):
                emit(stream, "event", "on_tool_end", run_id=str(run_id), name=self.names[run_id], output=output)

            def on_tool_error(self, error, *, run_id, **kwargs):
                emit(stream, "event", "on_tool_error", run_id=str(run_id), name=self.names[run_id], error=str(error))

        tool = StructuredTool.from_function(local, coroutine=alocal, name="local")
        config = {"callbacks": [Capture()], "tags": ["inert-capture"], "metadata": {"source": "local-only"}}
        assert tool.invoke({"value": "alpha"}, config=config) == {"echo": "alpha", "count": 0, "ok": False}
        try:
            tool.invoke({"value": "fail"}, config=config)
        except ValueError as exc:
            assert str(exc) == "local rejection"
        else:
            raise AssertionError("The inert failure must remain a failure")
        assert await tool.ainvoke({"value": "async"}, config=config) == {"echo": "async", "count": 0, "ok": False}
    assert calls == ["alpha", "fail", "async"]
    return {
        "producer": "langchain-core",
        "version": version("langchain-core"),
        "sdk_sources": [source_hash(BaseCallbackHandler), source_hash(StructuredTool)],
        "body_calls": calls,
    }


async def semantic_kernel_capture(destination):
    from semantic_kernel import Kernel  # type: ignore[import-not-found]
    from semantic_kernel.contents import ChatHistory, FunctionCallContent  # type: ignore[import-not-found]
    from semantic_kernel.filters import FilterTypes  # type: ignore[import-not-found]
    from semantic_kernel.functions import KernelArguments, kernel_function  # type: ignore[import-not-found]

    calls = []

    @kernel_function(name="local", description="Inert local function")
    def local(value: str) -> str:
        calls.append(value)
        if value == "fail":
            raise ValueError("local rejection")
        return f"result:{value}"

    kernel = Kernel()
    function = kernel.add_function(plugin_name="test", function=local)
    with destination.open("xb") as stream:

        async def invocation(context, next):
            identity = str(uuid4())
            fields = {
                "invocation_id": identity,
                "function_name": context.function.name,
                "plugin_name": context.function.plugin_name,
            }
            emit(stream, "type", "native_function.started", **fields, arguments=dict(context.arguments))
            try:
                await next(context)
            except Exception as exc:
                emit(stream, "type", "native_function.failed", **fields, error=str(exc))
                raise
            emit(stream, "type", "native_function.completed", **fields, result=context.result.value)

        async def auto_invocation(context, next):
            fields = {
                "invocation_id": str(uuid4()),
                "function_name": context.function.name,
                "plugin_name": context.function.plugin_name,
            }
            emit(
                stream,
                "type",
                "auto_function.started",
                **fields,
                arguments=dict(context.arguments),
                function_count=context.function_count,
                request_sequence_index=context.request_sequence_index,
                function_sequence_index=context.function_sequence_index,
            )
            await next(context)
            emit(
                stream,
                "type",
                "auto_function.completed",
                **fields,
                result=context.function_result.value,
                terminate=context.terminate,
            )

        kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, invocation)
        kernel.add_filter(FilterTypes.AUTO_FUNCTION_INVOCATION, auto_invocation)
        assert (await function.invoke(kernel, KernelArguments(value="alpha"))).value == "result:alpha"
        try:
            await function.invoke(kernel, KernelArguments(value="fail"))
        except Exception as exc:
            assert "local rejection" in str(exc)
        else:
            raise AssertionError("The inert failure must remain a failure")
        history = ChatHistory()
        await kernel.invoke_function_call(
            FunctionCallContent(id="inert-auto-call", name="test-local", arguments='{"value":"auto"}', index=0),
            history,
            function_call_count=1,
            request_index=0,
        )
        assert history.messages[-1].items[0].result == "result:auto"
    assert calls == ["alpha", "fail", "auto"]
    return {
        "producer": "semantic-kernel",
        "version": version("semantic-kernel"),
        "sdk_sources": [source_hash(Kernel), source_hash(type(function))],
        "body_calls": calls,
    }


async def m365_capture(destination):
    from microsoft_agents.activity import Activity, ChannelAccount, ConversationAccount  # type: ignore[import-not-found]
    from microsoft_agents.hosting.core.storage.transcript.transcript_file_store import (  # type: ignore[import-not-found]
        FileTranscriptStore,
    )

    store = FileTranscriptStore(destination.parent / "producer-store")
    common = {"channel_id": "local", "conversation": ConversationAccount(id="inert-conversation")}
    activities = [
        Activity(
            **common,
            id="activity-user",
            type="message",
            from_property=ChannelAccount(id="user", name="Local User", role="user"),
            text="Read local input.",
        ),
        Activity(
            **common,
            id="activity-bot",
            type="message",
            from_property=ChannelAccount(id="bot", name="Local Agent", role="bot"),
            text="Inspecting local input.",
        ),
        Activity(
            **common,
            id="activity-invoke",
            type="invoke",
            from_property=ChannelAccount(id="bot", role="bot"),
            value={"command": "read", "count": 0},
        ),
        Activity(
            **common,
            id="activity-result",
            type="event",
            from_property=ChannelAccount(id="user", role="user"),
            text="Local result.",
            value={"ok": False},
        ),
        Activity(
            **common,
            id="activity-trace",
            type="trace",
            from_property=ChannelAccount(id="bot", role="bot"),
            text="Local diagnostic.",
            value={"detail": "inert"},
        ),
    ]
    for activity in activities:
        await store.log_activity(activity)
    source = destination.parent / "producer-store" / "local" / "inert-conversation.transcript"
    raw_bytes = source.read_bytes()
    serialized = b"".join(
        activity.model_dump_json(exclude_none=True, exclude_unset=True).encode() + b"\n" for activity in activities
    )
    assert raw_bytes == serialized
    assert all(
        "from_property" in json.loads(line) and "from" not in json.loads(line) for line in raw_bytes.splitlines()
    )
    with destination.open("xb") as retained:
        retained.write(raw_bytes)
    read_back = await store.get_transcript_activities("local", "inert-conversation")
    assert [activity.id for activity in read_back.items] == [activity.id for activity in activities]
    return {
        "producer": "microsoft-agents-hosting-core",
        "version": version("microsoft-agents-hosting-core"),
        "activity_version": version("microsoft-agents-activity"),
        "sdk_sources": [source_hash(FileTranscriptStore), source_hash(Activity)],
        "writer": "FileTranscriptStore.log_activity",
        "copied_bytes_sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    attempts = []

    def offline(event, values):
        if event in {
            "socket.connect",
            "socket.getaddrinfo",
            "socket.sendto",
            "subprocess.Popen",
            "os.system",
            "os.posix_spawn",
        }:
            attempts.append(event)
            raise PermissionError(f"Inert producer capture denies {event}")

    sys.addaudithook(offline)
    os.environ.update(
        LANGCHAIN_TRACING_V2="false",
        LANGSMITH_TRACING="false",
        OTEL_SDK_DISABLED="true",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )
    for key in tuple(os.environ):
        if any(part in key for part in ("API_KEY", "ACCESS_TOKEN", "CLIENT_SECRET", "CONNECTION_STRING")):
            del os.environ[key]
    captures = {}
    for name, capture in (
        ("langchain", langchain_capture),
        ("semantic_kernel", semantic_kernel_capture),
        ("maf_transcript", m365_capture),
    ):
        path = args.output / f"{name}.jsonl"
        captures[name] = await capture(path)
        raw = path.read_bytes()
        captures[name].update(sha256=hashlib.sha256(raw).hexdigest(), physical_records=len(raw.splitlines()))
    assert not attempts, attempts
    report = {
        "classification": "actual SDK callback/serialization with inert local input",
        "model_or_vendor_conversation": False,
        "runtime_network_or_process_attempts": attempts,
        "captures": captures,
        "distributions": {item.metadata["Name"]: item.version for item in distributions()},
    }
    (args.output / "capture-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({name: row["physical_records"] for name, row in captures.items()}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
