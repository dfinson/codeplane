"""Exercise generated SDK types outside the unit suite's global SDK doubles."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_generated_usage_quota_and_shutdown_contracts() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import asyncio
                import json
                from datetime import timedelta
                from types import SimpleNamespace
                from unittest.mock import AsyncMock
                from copilot.generated import session_events as sdk
                from copilot.generated.rpc import ModelProviderKind, ModelProviderRef
                from backend.services.copilot_adapter import CopilotAdapter

                async def check():
                    adapter = CopilotAdapter()
                    queue = asyncio.Queue()
                    adapter._queues["session"] = queue
                    adapter._accepting_events.add("session")
                    adapter.set_job_id("session", "job")
                    writes = []
                    adapter._schedule_db_write = writes.append
                    adapter._db_write_set_quota = AsyncMock()
                    adapter._db_write_increment = AsyncMock()

                    async def emit(kind, data):
                        event = SimpleNamespace(type=SimpleNamespace(value=kind), data=data)
                        await adapter._process_sdk_event_inner(
                            event, "session", "job", "", [True], queue
                        )

                    await emit("assistant.usage", sdk.AssistantUsageData(
                        model="utility", input_tokens=10, output_tokens=5,
                        duration=timedelta(milliseconds=125)
                    ))
                    usage = queue.get_nowait()
                    assert usage.payload["duration_ms"] == 125
                    assert usage.payload["input_tokens"] == 10

                    budget = sdk.ProviderQuotaBudgetMetadata(
                        consumed=1.25, entitlement=10.5, overage=0.125,
                        overage_allowed_when_exhausted=True,
                        remaining_percentage=80, unlimited=False,
                        usage_allowed_when_exhausted=True, reset_at_epoch_ms=0,
                    )
                    observation = sdk.ProviderQuotaState(
                        access_state=next(iter(sdk.ProviderQuotaAccessState)),
                        capacity_state=next(iter(sdk.ProviderQuotaCapacityState)),
                        provider=ModelProviderRef(
                            id="copilot", kind=next(iter(ModelProviderKind)), label="Copilot"
                        ),
                        quota_id="account-credits", compatibility_key="credits",
                        unit=next(iter(sdk.ProviderQuotaUnit)), budget_metadata=budget,
                    )
                    await emit("session.quota_observation", sdk.SessionQuotaObservationData(
                        observation=observation
                    ))
                    observation.budget_metadata = None
                    await emit("session.quota_observation", sdk.SessionQuotaObservationData(
                        observation=observation
                    ))
                    await asyncio.gather(*writes)
                    writes.clear()
                    adapter._db_write_set_quota.assert_awaited_once()
                    quota = json.loads(
                        adapter._db_write_set_quota.await_args.kwargs["quota_remaining"]
                    )["credits"]
                    assert quota["used_requests"] == 1.25
                    assert quota["entitlement_requests"] == 10.5
                    assert quota["overage"] == 0.125
                    assert quota["reset_date"] == "1970-01-01T00:00:00+00:00"

                    shutdown = sdk.SessionShutdownData(
                        code_changes=sdk.ShutdownCodeChanges(
                            files_modified=[], lines_added=0, lines_removed=0
                        ),
                        model_metrics={}, session_start_time=0,
                        shutdown_type=sdk.ShutdownType.ROUTINE,
                        total_api_duration=timedelta(), _total_premium_requests=1.25,
                    )
                    await emit("session.shutdown", shutdown)
                    assert queue.get_nowait() is None
                    await asyncio.gather(*writes)
                    adapter._db_write_increment.assert_awaited_once_with(
                        job_id="job", premium_requests=1.25
                    )

                    def reject_write(coro):
                        coro.close()
                        raise RuntimeError("Telemetry unavailable")

                    adapter._schedule_db_write = reject_write
                    adapter._accepting_events.add("session")
                    try:
                        await emit("session.shutdown", shutdown)
                    except RuntimeError as error:
                        assert str(error) == "Telemetry unavailable"
                    else:
                        raise AssertionError("Telemetry failure was hidden")
                    assert queue.get_nowait() is None

                asyncio.run(check())
                """
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
