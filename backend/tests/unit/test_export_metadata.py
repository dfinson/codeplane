import pytest

from backend.models.events import new_event
from backend.models.session_event import EventMetadata
from backend.services.events.export_metadata import ExportDisclosureError, export_metadata


def test_raw_prompts_commands_results_paths_and_personal_ids_never_leave_projection() -> None:
    event = new_event(
        "job",
        "tool.call.completed",
        {
            "content": "private prompt",
            "arguments": {"command": "cat secret-file"},
            "result": "private result",
            "path": "/home/private",
            "url": "https://private.test",
            "tool_call_id": "alice@example.test",
            "success": False,
        },
        metadata=EventMetadata(run_id="personal external ID", span_id="alice-span", parent_id="alice-parent"),
        raw_event={"raw_secret": "private raw source must not leave the application"},
    )
    exported = export_metadata(
        event,
        identity_key=b"x" * 32,
        integration="copilot",
        provider="copilot",
        role="primary",
        approved_models=frozenset(),
    )
    text = str(exported)
    assert not any(value in text for value in ("private", "alice", "personal", "secret-file"))
    assert exported["outcome"] == "failed"
    assert exported["tool_call_id"] != event.payload["tool_call_id"]
    assert (
        export_metadata(
            event,
            identity_key=b"x" * 32,
            integration="copilot",
            provider="copilot",
            role="primary",
            approved_models=frozenset(),
        )
        == exported
    )


def test_credential_shaped_allowed_field_withholds_entire_export_without_echo() -> None:
    secret = "ghp_" + "a" * 36
    with pytest.raises(ExportDisclosureError) as failure:
        export_metadata(
            new_event("job", "log", {}),
            identity_key=b"x" * 32,
            integration=secret,
            provider="copilot",
            role="primary",
            approved_models=frozenset(),
        )
    assert secret not in str(failure.value)
