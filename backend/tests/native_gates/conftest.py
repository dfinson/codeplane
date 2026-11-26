"""Opt-in SDK qualification, not model inference or Copilot programme acceptance.

The qualify_*.py names keep optional SDKs out of the default core test collection.
Run all rows explicitly, with no skips, using an isolated uv environment:

    uv venv <qualification-env>
    uv pip install --python <qualification-env>/bin/python \
        -r backend/tests/native_gates/requirements.txt -e .
    uv pip check --python <qualification-env>/bin/python
    uv run --no-project --python <qualification-env>/bin/python \
        python -m pytest backend/tests/native_gates/qualify_native_*.py \
        backend/tests/native_gates/qualify_runtime_integration.py \
        backend/tests/native_gates/qualify_review_regressions.py \
        backend/tests/native_gates/qualify_observation_scope.py \
        backend/tests/native_gates/qualify_executor_isolation.py

The AutoGen/LlamaIndex/ADK follow-ups use a separate environment with
requirements-profiles.txt and the same dependency-checked editable install, then run
qualify_autogen.py, qualify_llamaindex.py, qualify_google_adk.py and
qualify_profile_release.py. None of
these optional environments changes the application's default profile.

Chroma declares ONNX Runtime as mandatory, so it must be installed for a complete
optional environment even though these fixtures do not import it or run embeddings.
No model weights are required. No native package code is patched or replaced.
"""

import asyncio
import socket

import pytest

from backend.models.session_event import EventMetadata
from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.adapters.native_gates import NativeBinding, NativeGates


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Native gate qualification must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("CREWAI_TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "false")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "true")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")


class Decision:
    execution_epoch = "epoch"

    def __init__(self):
        self.allow = True
        self.pending = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.invocations = []

    async def authorize_permission(self, invocation):
        self.invocations.append(invocation)
        self.entered.set()
        if self.pending:
            await self.release.wait()
        return self.allow


@pytest.fixture
def binding_factory():
    managers = []

    def make(family, *, token="token", policy=None):
        decision = policy or Decision()
        live = {"current": True}

        async def current():
            return live["current"]

        gates = NativeGates(
            NativeBinding(
                family=family,
                execution=ExecutionIdentity("job-1", f"{family}:session", "epoch", token),
                policy=decision,
                metadata=EventMetadata(
                    source_framework=family,
                    run_id="session",
                    execution_epoch="epoch",
                    execution_token=token,
                ),
                workspace="/workspace",
                protected_paths=(),
                is_current=current,
                is_current_inline=lambda: live["current"],
            )
        )
        managers.append(gates)
        return gates, decision, live

    yield make
    for manager in managers:
        manager.close()
