import pytest

from backend.models.events import new_event
from backend.models.session_event import EventMetadata
from backend.services.events.export_metadata import ExportDisclosureError, export_metadata, validate_projection


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


@pytest.mark.parametrize("value", ["https://private.example.test/path", "/home/private/data", "C:\\private\\data"])
def test_allowed_metadata_fields_cannot_smuggle_addresses_or_paths(value):
    with pytest.raises(ExportDisclosureError):
        export_metadata(
            new_event("job", "tool.call.completed", {}),
            identity_key=b"x" * 32,
            integration=value,
            provider="copilot",
            role="primary",
            approved_models=frozenset(),
        )


def test_export_inspection_is_bounded_without_truncating_source_identifiers():
    with pytest.raises(ExportDisclosureError, match="32 KiB"):
        export_metadata(
            new_event("j" * (32 * 1024), "raw", {}),
            identity_key=b"x" * 32,
            integration="generic-otel",
            provider="otel",
            role="primary",
            approved_models=frozenset(),
        )


def test_source_span_and_parent_ids_are_scoped_but_remain_correlatable():
    def project(span, parent=None, binding="binding"):
        return export_metadata(
            new_event(
                "job",
                "raw",
                {"trace_id": "trace"},
                metadata=EventMetadata(span_id=span, parent_id=parent, integration_binding_id=binding, run_id="run"),
            ),
            identity_key=b"x" * 32,
            integration="generic-otel",
            provider="otel",
            role="primary",
            approved_models=frozenset(),
        )

    parent = project("parent")
    child = project("child", "parent")
    assert child["parent_span_id"] == parent["span_id"]
    assert child["source_trace_id"] == parent["source_trace_id"]
    assert project("parent", binding="other")["span_id"] != parent["span_id"]


def test_export_projection_rejects_oversized_supported_usage_instead_of_truncating():
    from decimal import Decimal

    from backend.models.usage import UsageAmount

    amount = UsageAmount(
        amount=Decimal(0),
        unit="input_tokens",
        producer="copilot",
        model=None,
        job_id="job",
        execution_epoch=None,
        role="primary",
        source="supplied",
    )
    with pytest.raises(ExportDisclosureError, match="8 KiB"):
        export_metadata(
            new_event("job", "telemetry.usage", {}),
            identity_key=b"x" * 32,
            integration="copilot",
            provider="copilot",
            role="primary",
            approved_models=frozenset(),
            usage=[amount] * 80,
        )


def test_native_sink_projection_is_rechecked_before_an_sdk_span_can_be_created():
    safe = export_metadata(
        new_event("job", "tool.call.completed", {}),
        identity_key=b"x" * 32,
        integration="copilot",
        provider="copilot",
        role="primary",
        approved_models=frozenset(),
    )
    assert validate_projection(safe, frozenset()) == safe
    for changed in (
        {**safe, "arguments": "private command"},
        {**safe, "job_id": "unkeyed"},
        {**safe, "kind": "arbitrary command"},
        {**safe, "role": "unknown"},
    ):
        with pytest.raises(ExportDisclosureError):
            validate_projection(changed, frozenset())


@pytest.mark.parametrize(
    "field,value",
    [
        ("integration", "src/private/customer-list.csv"),
        ("provider", "private customer notes"),
        ("version", "~/private/keys"),
        ("model", "src/private/customer-list.csv"),
        ("producer", "private customer notes"),
    ],
)
@pytest.mark.parametrize("boundary", ["canonical", "native-sink"])
def test_export_fields_require_identifiers_not_paths_or_free_text(field, value, boundary):
    from decimal import Decimal

    from backend.models.usage import UsageAmount

    event = new_event("job", "telemetry.usage", {"model": "approved-model"})
    usage = UsageAmount(
        amount=Decimal(0),
        unit="input_tokens",
        producer="copilot",
        model=None,
        job_id="job",
        execution_epoch=None,
        role="primary",
        source="native",
    )
    kwargs = {
        "identity_key": b"x" * 32,
        "integration": "copilot",
        "provider": "copilot",
        "role": "primary",
        "approved_models": frozenset({"approved-model", value}),
        "usage": [usage],
        "versions": {"policy_version": "cp-policy-v1"},
    }
    if boundary == "canonical":
        if field in {"integration", "provider"}:
            kwargs[field] = value
        elif field == "version":
            kwargs["versions"] = {"policy_version": value}
        elif field == "model":
            event.payload["model"] = value
        else:
            kwargs["usage"] = [usage.model_copy(update={"producer": value})]
        with pytest.raises(ExportDisclosureError):
            export_metadata(event, **kwargs)
    else:
        safe = export_metadata(event, **kwargs)
        if field == "producer":
            safe["usage"][0]["producer"] = value
        else:
            safe["policy_version" if field == "version" else field] = value
        with pytest.raises(ExportDisclosureError):
            validate_projection(safe, kwargs["approved_models"])


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4.1",
        "anthropic/claude-sonnet-4-5",
        "ft:gpt-4o-mini:organization:custom-suffix:abc123",
        "publishers/google/models/gemini-2.5-pro",
        "projects/example/locations/us/publishers/google/models/gemini-2.5-pro",
        "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-sonnet-v1:0",
    ],
)
def test_approved_native_model_identifiers_and_version_labels_survive_both_boundaries(model):
    safe = export_metadata(
        new_event("job", "llm.call.completed", {"model": model}),
        identity_key=b"x" * 32,
        integration="generic-otel",
        provider="custom_provider.v2",
        role="primary",
        approved_models=frozenset({model}),
        versions={"schema_version": "cp-export-v1", "grammar_version": "tree-sitter-bash@0.25.1"},
    )
    assert safe["model"] == model
    assert validate_projection(safe, frozenset({model})) == safe
