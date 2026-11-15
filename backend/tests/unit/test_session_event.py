from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.models.session_event import CanonicalSessionEvent, EventMetadata, ToolMotivation


def test_wire_identity_nulls_defaults_and_open_kind_are_preserved() -> None:
    wire = {
        "id": "source-event",
        "kind": "vendor.future.event",
        "session_id": "job-1",
        "timestamp": "2026-01-02T03:04:05Z",
        "payload": {"empty": None, "seq": 999},
        "metadata": {"sequence": 0, "raw_kind": None, "motivation": {"source_event_ids": ["original"]}},
    }
    event = CanonicalSessionEvent.model_validate(wire)
    assert event.id == "source-event"
    assert event.sequence == 0
    assert event.payload["seq"] == 999
    encoded = event.model_dump(mode="json")
    assert encoded["metadata"]["raw_kind"] is None
    assert encoded["metadata"]["motivation"] == {
        "intent": None,
        "reasoning": None,
        "source_event_ids": ["original"],
    }
    assert encoded["raw_event"] is None
    assert "sequence" not in {key for key in encoded if key != "metadata"}
    assert CanonicalSessionEvent.model_validate_json(event.model_dump_json()) == event


def test_legacy_governance_and_classification_history_remain_readable() -> None:
    governance = {"risk_assessment": {"score": 91}, "unknown_legacy": {"x": None}}
    event = CanonicalSessionEvent(
        kind="tool.call.completed",
        session_id="job-1",
        timestamp=datetime.now(UTC),
        payload={},
        metadata=EventMetadata(
            classification={"mechanism": "process.shell", "effect": "read_only", "binaries": ["ls"]},
            governance=governance,
            motivation=ToolMotivation(intent="Read project"),
        ),
    )
    assert event.metadata.classification.binaries == ("ls",)
    assert json.loads(event.model_dump_json())["metadata"]["governance"] == governance


def test_recipe_correlation_does_not_replace_transcript_identifiers() -> None:
    metadata = EventMetadata(
        step_id="transcript-step",
        turn_id="turn-1",
        recipe_run_id="run-1",
        recipe_revision=3,
        step_instance_id="recipe-step",
        attempt_id="attempt-2",
    )
    assert metadata.step_id == "transcript-step"
    assert metadata.step_instance_id == "recipe-step"
    with pytest.raises(ValidationError):
        metadata.step_id = "changed"


@pytest.mark.parametrize("invalid", [{"sequence": -1}, {"duration_ms": -1}, {"partial": None}])
def test_invalid_metadata_retains_legacy_validation(invalid) -> None:
    with pytest.raises(ValidationError):
        EventMetadata.model_validate(invalid)
