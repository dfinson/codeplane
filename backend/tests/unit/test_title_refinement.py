import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import CPLConfig
from backend.models.events import EventKind, new_event
from backend.models.execution_contracts import ExecutionContractPin
from backend.models.session_event import EventMetadata
from backend.persistence.event_repo import EventRepository
from backend.services.action_policy.assessment import AssessmentService
from backend.services.adapters.agent_adapter import CompletionResult
from backend.services.adapters.completion_context import CompletionContext, current_completion_context
from backend.services.events.activity_projection import ActivityProjection
from backend.services.events.event_bus import EventBus
from backend.services.events.title_refinement import TitleRefinement, _Title
from backend.tests.unit.test_approval_service import session_factory as session_factory
from backend.tests.unit.test_execution_contracts import _contract


@pytest.mark.parametrize(
    "text,refined",
    [
        ('{"title":"Inspect worker cancellation ownership"}', True),
        ('{"title":"one"}', False),
        (json.dumps({"title": "Inspect " + "x" * 80 + " now"}), False),
        ('{"title":"Inspect worker cancellation ownership","extra":true}', False),
        ('```json\n{"title":"Inspect worker cancellation ownership"}\n```', False),
    ],
)
async def test_title_claim_is_once_and_uses_only_closed_activity_context(session_factory, text, refined):
    events = EventBus()
    contexts: list[CompletionContext] = []

    async def complete(*args: object, **kwargs: object) -> CompletionResult:
        contexts.append(current_completion_context())
        return CompletionResult(text=text, model="claude-test")

    provider = SimpleNamespace(complete=AsyncMock(side_effect=complete))
    registry = MagicMock()
    registry.get_completion_provider.return_value = provider
    pin = ExecutionContractPin(job_id="job-1", admission_id="run", budget_id="job-1", contract=_contract("test"))
    host = SimpleNamespace(
        _event_bus=events,
        _session_factory=session_factory,
        _adapter_registry=registry,
        _config=CPLConfig(),
        _recipe_contract_pins={},
        _job_contract_pins={"job-1": pin},
        execution_epoch="epoch",
    )
    assessments = AssessmentService()
    titles = TitleRefinement(host, assessments)
    projection = ActivityProjection(events)
    titles.start()
    metadata = EventMetadata(execution_epoch="epoch")
    try:
        for event in (
            new_event("job-1", EventKind.message_user, {"content": "Inspect worker cancellation"}, metadata=metadata),
            new_event("job-1", EventKind.message_assistant, {"content": "Review ownership guards"}, metadata=metadata),
            new_event("job-1", EventKind.tool_call_completed, {"result": "PRIVATE_TOOL_RESULT"}, metadata=metadata),
            new_event("job-1", EventKind.message_user, {"content": "New unrelated instruction"}, metadata=metadata),
        ):
            await events.publish(event)
            await projection.push(event)
        await asyncio.wait_for(titles._queue.join(), timeout=2)
        provider.complete.assert_awaited_once()
        call = provider.complete.call_args
        assert json.loads(call.args[0])["snippets"] == [
            "Inspect worker cancellation",
            "Review ownership guards",
        ]
        assert call.kwargs["excluded_tools"] == ["*"]
        assert contexts[0].response_schema == _Title.model_json_schema()
        async with session_factory() as session:
            records = [row.event for row in await EventRepository(session).list_after(0, job_id="job-1")]
        assert len([record for record in records if record.kind == "activity.title.claimed"]) == 1
        updates = [record for record in records if record.kind == EventKind.turn_summary]
        assert bool(updates) is refined
        if refined:
            assert updates[0].payload["title"] == "Inspect worker cancellation ownership"
        await titles.close()
        restarted = TitleRefinement(host, assessments)
        # A durable claim remains consumed even after failure or process replacement.
        close = new_event(
            "job-1",
            EventKind.turn_summary,
            {"activity_status": "closed", "activity_id": records[0].payload["activity_id"]},
            metadata=metadata,
        )
        await restarted.observe(close)
        assert restarted._queue.empty()
        provider.complete.assert_awaited_once()
    finally:
        await titles.close()
        await assessments.shutdown()
