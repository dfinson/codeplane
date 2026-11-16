import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock

from backend.models.usage import UsageAmount
from backend.services.action_policy.assessment import ActionBinding, AssessmentService
from backend.services.adapters.agent_adapter import CompletionResult

FIELDS = {"command": "pwd", "arguments": "", "tool_definition": "", "context": ""}


def _binding(job="job", fields=None):
    return ActionBinding.create(job, "epoch", {"tool": "bash", "policy": "v1"}, fields=fields or FIELDS)


def _completion():
    raw = {
        "schema_version": "cp-action-v1",
        "mechanism": "shell",
        "effect": "read_only",
        "scope": "workspace",
        "network": "none",
        "reversibility": "not_applicable",
        "capabilities": [],
        "structure": [],
        "unknowns": [],
    }
    raw["evidence"] = [
        {"field": field, "value": raw[field], "source": "command", "start": 0, "end": 3}
        for field in ("effect", "scope", "network", "reversibility")
    ]
    return CompletionResult(
        text=json.dumps(raw),
        model="approved-model",
        input_tokens=12,
        usage=(
            UsageAmount(
                amount=Decimal(12),
                unit="input_tokens",
                producer="fixture",
                model="approved-model",
                job_id=None,
                execution_epoch=None,
                role="assessment",
                source="response",
            ),
        ),
    )


async def test_single_text_only_attempt_binds_sources_and_rechecks_epoch() -> None:
    provider = AsyncMock()
    provider.complete.return_value = _completion()
    service = AssessmentService()
    current = AsyncMock(return_value=True)
    binding = _binding()
    result = await service.assess(binding, FIELDS, provider=provider, model="approved-model", is_current=current)
    assert result.assessment is not None and result.reason is None
    assert result.binding_sha256 == binding.sha256
    assert len(result.sources) == 4
    provider.complete.assert_awaited_once()
    assert provider.complete.call_args.kwargs["excluded_tools"] == ["*"]
    assert current.await_count == 2
    assert binding.matches({"tool": "bash", "policy": "v1"}, fields=FIELDS)
    assert not binding.matches({"tool": "bash", "policy": "v2"}, fields=FIELDS)
    assert not service._jobs and not service._tasks


async def test_changed_or_redacted_input_never_calls_model() -> None:
    provider = AsyncMock()
    service = AssessmentService()
    current = AsyncMock(return_value=True)
    changed = await service.assess(
        _binding(),
        dict(FIELDS, command="changed"),
        provider=provider,
        model="approved-model",
        is_current=current,
    )
    assert changed.reason == "assessment_binding_mismatch"
    secret = dict(FIELDS, command="token='secret'")
    result = await service.assess(
        _binding(fields=secret),
        secret,
        provider=provider,
        model="approved-model",
        is_current=current,
    )
    assert result.reason == "redacted_semantics"
    assert any(source.redactions for source in result.sources)
    provider.complete.assert_not_called()


async def test_stale_result_and_invalid_output_are_unavailable() -> None:
    provider = AsyncMock()
    provider.complete.return_value = _completion()
    service = AssessmentService()
    result = await service.assess(
        _binding(),
        FIELDS,
        provider=provider,
        model="approved-model",
        is_current=AsyncMock(side_effect=[True, False]),
    )
    assert result.reason == "stale_execution" and result.assessment is None
    assert result.completion is not None and result.completion.model == "approved-model"
    assert result.completion.input_tokens == 12 and result.completion.usage[0].amount == 12
    invalid = _completion()
    invalid.text = '{"authority":"allow"}'
    provider.complete.return_value = invalid
    result = await service.assess(
        _binding(),
        FIELDS,
        provider=provider,
        model="approved-model",
        is_current=AsyncMock(return_value=True),
    )
    assert result.reason == "assessment_invalid_output" and result.assessment is None
    assert result.completion is not None
    assert result.completion is provider.complete.return_value
    assert result.completion.input_tokens == 12 and result.completion.usage[0].amount == 12


async def test_hard_deadline_returns_while_cancelled_provider_drains_without_reusing_slot(monkeypatch) -> None:
    import backend.services.action_policy.assessment as module

    monkeypatch.setattr(module, "DECISION_SECONDS", 0.03)
    monkeypatch.setattr(module, "QUEUE_SECONDS", 0.01)
    cleanup = asyncio.Event()
    cancelled = asyncio.Event()

    async def complete(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await cleanup.wait()
            raise

    provider = AsyncMock()
    provider.complete.side_effect = complete
    service = AssessmentService()
    kwargs = dict(provider=provider, model="approved-model", is_current=AsyncMock(return_value=True))
    try:
        result = await asyncio.wait_for(service.assess(_binding(), FIELDS, **kwargs), timeout=0.2)
        assert result.reason == "assessment_timeout"
        await cancelled.wait()
        retry = await service.assess(_binding(), FIELDS, **kwargs)
        assert retry.reason == "assessment_queue_timeout"
        assert provider.complete.await_count == 1
    finally:
        cleanup.set()
        await asyncio.gather(*tuple(service._tasks), return_exceptions=True)
        await service.shutdown()
    assert not service._jobs and not service._tasks


async def test_two_global_and_one_per_job_limits_do_not_queue_extra_provider_calls(monkeypatch) -> None:
    import backend.services.action_policy.assessment as module

    monkeypatch.setattr(module, "QUEUE_SECONDS", 0.02)
    release = asyncio.Event()
    two_started = asyncio.Event()
    started = 0

    async def complete(*args, **kwargs):
        nonlocal started
        started += 1
        if started == 2:
            two_started.set()
        await release.wait()
        return _completion()

    provider = AsyncMock()
    provider.complete.side_effect = complete
    service = AssessmentService()
    kwargs = dict(provider=provider, model="approved-model", is_current=AsyncMock(return_value=True))
    first = asyncio.create_task(service.assess(_binding("one"), FIELDS, **kwargs))
    second = asyncio.create_task(service.assess(_binding("two"), FIELDS, **kwargs))
    await two_started.wait()
    try:
        blocked = await service.assess(_binding("one"), FIELDS, **kwargs)
        overflow = await service.assess(_binding("three"), FIELDS, **kwargs)
        assert blocked.reason == overflow.reason == "assessment_queue_timeout"
        assert started == 2
    finally:
        release.set()
        await asyncio.gather(first, second)
        await service.shutdown()


async def test_thirty_third_waiter_is_rejected_without_an_extra_model_attempt() -> None:
    release, active = asyncio.Event(), asyncio.Event()
    started = 0

    async def complete(*args, **kwargs):
        nonlocal started
        started += 1
        if started == 2:
            active.set()
        await release.wait()
        return _completion()

    provider = AsyncMock()
    provider.complete.side_effect = complete
    service = AssessmentService()
    kwargs = dict(provider=provider, model="approved-model", is_current=AsyncMock(return_value=True))
    tasks = [asyncio.create_task(service.assess(_binding(f"active-{i}"), FIELDS, **kwargs)) for i in range(2)]
    await active.wait()
    tasks.extend(asyncio.create_task(service.assess(_binding(f"queued-{i}"), FIELDS, **kwargs)) for i in range(32))
    await asyncio.sleep(0)
    try:
        result = await service.assess(_binding("overflow"), FIELDS, **kwargs)
        assert result.reason == "assessment_queue_full"
        assert started == 2
    finally:
        release.set()
        await asyncio.gather(*tasks)
        await service.shutdown()
