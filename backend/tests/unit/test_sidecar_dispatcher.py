"""Tests for backend.services.sidecar.dispatcher — hydration + helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.models.events import EventKind, new_event
from backend.models.session_event import EventMetadata
from backend.services.sidecar.dispatcher import (
    AgentMessageRoute,
    CallbackRoute,
    Concurrency,
    ConditionalRoute,
    ContentMatchCondition,
    EventBusRoute,
    EventCondition,
    FilePatternCondition,
    GateRoute,
    JobMetadataRoute,
    JsonArray,
    JsonObject,
    ManualCondition,
    PlainText,
    RegexCondition,
    SidecarDefinition,
    SidecarDispatcher,
    ThresholdCondition,
    TimerCondition,
    _hydrate_condition,
    _hydrate_parser,
    _hydrate_route,
    _SafeFormatter,
    hydrate_definition,
)


async def test_observation_history_cannot_trigger_another_jobs_sidecar() -> None:
    dispatcher = SidecarDispatcher(AsyncMock(), AsyncMock())
    definition = hydrate_definition(
        {
            "name": "observer",
            "lifetime": "ephemeral",
            "triggers": [{"condition": {"kind": "event", "eventKind": "message.assistant", "once": True}}],
        }
    )
    dispatcher.activate("managed-job", [definition])
    dispatcher._try_execute = AsyncMock()
    await dispatcher.handle_event(
        new_event(
            "observation-job",
            EventKind.message_assistant,
            {"content": "Imported history"},
            metadata=EventMetadata(integration_binding_id="binding", namespace=("observation-source", "log")),
        )
    )
    dispatcher._try_execute.assert_not_awaited()
    assert dispatcher._jobs["managed-job"].fired_once == set()


# ── _SafeFormatter ──


class TestSafeFormatter:
    def test_simple_substitution(self):
        fmt = _SafeFormatter()
        result = fmt.format("{name} is {age}", name="Alice", age=30)
        assert result == "Alice is 30"

    def test_blocks_attribute_access(self):
        fmt = _SafeFormatter()
        with pytest.raises(KeyError):
            fmt.format("{obj.attr}", obj=object())

    def test_blocks_index_access(self):
        fmt = _SafeFormatter()
        with pytest.raises(KeyError):
            fmt.format("{obj[0]}", obj=[1, 2, 3])


# ── _hydrate_condition ──


class TestHydrateCondition:
    def test_event_single_kind(self):
        cond = _hydrate_condition({"kind": "event", "eventKind": "job_completed"})
        assert isinstance(cond, EventCondition)
        assert cond.event_kinds == ("job_completed",)

    def test_event_multiple_kinds(self):
        cond = _hydrate_condition({"kind": "event", "eventKinds": ["a", "b"]})
        assert isinstance(cond, EventCondition)
        assert cond.event_kinds == ("a", "b")

    def test_event_with_filter(self):
        cond = _hydrate_condition({"kind": "event", "eventKind": "x", "eventFilter": {"key": "val"}})
        assert isinstance(cond, EventCondition)
        assert cond.event_filter == {"key": "val"}

    def test_timer(self):
        cond = _hydrate_condition({"kind": "timer", "intervalS": 30})
        assert isinstance(cond, TimerCondition)
        assert cond.interval_s == 30.0

    def test_timer_with_idle_guard(self):
        cond = _hydrate_condition({"kind": "timer", "interval_s": 10, "idleGuardS": 5})
        assert isinstance(cond, TimerCondition)
        assert cond.idle_guard_s == 5

    def test_threshold(self):
        cond = _hydrate_condition({"kind": "threshold", "metric": "tool_calls", "value": 10})
        assert isinstance(cond, ThresholdCondition)
        assert cond.metric == "tool_calls"
        assert cond.value == 10

    def test_regex(self):
        cond = _hydrate_condition({"kind": "regex", "pattern": r"\bERROR\b"})
        assert isinstance(cond, RegexCondition)
        assert cond.pattern == r"\bERROR\b"
        assert cond._compiled is not None

    def test_regex_invalid(self):
        with pytest.raises(ValueError, match="Invalid regex"):
            _hydrate_condition({"kind": "regex", "pattern": "[unclosed"})

    def test_file_pattern(self):
        cond = _hydrate_condition({"kind": "file_pattern", "glob": "*.py", "changeKind": "modified"})
        assert isinstance(cond, FilePatternCondition)
        assert cond.glob == "*.py"
        assert cond.change_kind == "modified"

    def test_content_match(self):
        cond = _hydrate_condition({"kind": "content_match", "keywords": ["TODO", "FIXME"]})
        assert isinstance(cond, ContentMatchCondition)
        assert cond.keywords == ("TODO", "FIXME")

    def test_content_match_single_keyword(self):
        cond = _hydrate_condition({"kind": "content_match", "keywords": "ERROR"})
        assert isinstance(cond, ContentMatchCondition)
        assert cond.keywords == ("ERROR",)

    def test_manual(self):
        cond = _hydrate_condition({"kind": "manual"})
        assert isinstance(cond, ManualCondition)

    def test_unknown_kind(self):
        cond = _hydrate_condition({"kind": "unknown_whatever"})
        assert isinstance(cond, ManualCondition)


# ── _hydrate_parser ──


class TestHydrateParser:
    def test_none(self):
        parser = _hydrate_parser(None)
        assert isinstance(parser, PlainText)

    def test_plain_text(self):
        parser = _hydrate_parser({"kind": "plain_text"})
        assert isinstance(parser, PlainText)

    def test_json_object(self):
        parser = _hydrate_parser({"kind": "json_object", "requiredKeys": ["a", "b"]})
        assert isinstance(parser, JsonObject)
        assert parser.required_keys == ("a", "b")

    def test_json_array(self):
        parser = _hydrate_parser({"kind": "json_array", "itemKeys": ["x"]})
        assert isinstance(parser, JsonArray)
        assert parser.item_keys == ("x",)

    def test_unknown_kind(self):
        parser = _hydrate_parser({"kind": "something_else"})
        assert isinstance(parser, PlainText)


# ── _hydrate_route ──


class TestHydrateRoute:
    def test_event_bus(self):
        route = _hydrate_route({"kind": "event_bus", "eventKind": "my_event"})
        assert isinstance(route, EventBusRoute)
        assert route.event_kind == "sidecar_my_event"

    def test_event_bus_already_prefixed(self):
        route = _hydrate_route({"kind": "event_bus", "eventKind": "sidecar_result"})
        assert isinstance(route, EventBusRoute)
        assert route.event_kind == "sidecar_result"

    def test_job_metadata(self):
        route = _hydrate_route({"kind": "job_metadata", "field": "quality_score"})
        assert isinstance(route, JobMetadataRoute)
        assert route.field_name == "quality_score"

    def test_callback(self):
        route = _hydrate_route({"kind": "callback", "callbackName": "my_handler"})
        assert isinstance(route, CallbackRoute)
        assert route.callback_name == "my_handler"

    def test_agent_message(self):
        route = _hydrate_route({"kind": "agent_message", "role": "tool_result", "label": "review"})
        assert isinstance(route, AgentMessageRoute)
        assert route.role == "tool_result"
        assert route.label == "review"

    def test_gate(self):
        route = _hydrate_route({"kind": "gate", "verdictField": "v", "reasonField": "r", "timeoutS": 60})
        assert isinstance(route, GateRoute)
        assert route.verdict_field == "v"
        assert route.reason_field == "r"
        assert route.timeout_s == 60.0

    def test_conditional(self):
        route = _hydrate_route(
            {
                "kind": "conditional",
                "field": "status",
                "value": "ok",
                "inner": {"kind": "event_bus", "eventKind": "result"},
            }
        )
        assert isinstance(route, ConditionalRoute)
        assert route.field_name == "status"
        assert isinstance(route.inner, EventBusRoute)

    def test_unknown_kind(self):
        route = _hydrate_route({"kind": "unknown"})
        assert isinstance(route, EventBusRoute)


# ── hydrate_definition ──


class TestHydrateDefinition:
    def test_minimal(self):
        raw = {"name": "my-sidecar", "phase": "midflight", "lifetime": "ephemeral"}
        defn = hydrate_definition(raw)
        assert isinstance(defn, SidecarDefinition)
        assert defn.name == "my-sidecar"
        assert defn.phase == "midflight"
        assert defn.lifetime == "ephemeral"
        assert defn.triggers == ()

    def test_full_definition(self):
        raw = {
            "name": "security-reviewer",
            "phase": "postflight",
            "lifetime": "windowed",
            "scope": "repo",
            "model": "claude-sonnet-4-20250514",
            "systemPrompt": "Review code for security issues.",
            "maxTurns": 5,
            "timeoutS": 120,
            "icon": "shield",
            "description": "Automated security review",
            "triggers": [
                {
                    "condition": {"kind": "manual"},
                    "contextSources": ["job_diff", "job_prompt"],
                    "promptTemplate": "Review: {job_diff}",
                    "outputParser": {"kind": "json_object"},
                    "outputRoutes": [{"kind": "event_bus", "eventKind": "review_done"}],
                }
            ],
        }
        defn = hydrate_definition(raw)
        assert defn.name == "security-reviewer"
        assert defn.scope == "repo"
        assert defn.model == "claude-sonnet-4-20250514"
        assert defn.max_turns == 5
        assert defn.timeout_s == 120
        assert len(defn.triggers) == 1
        trigger = defn.triggers[0]
        assert isinstance(trigger.condition, ManualCondition)
        assert trigger.context_sources == ("job_diff", "job_prompt")
        assert isinstance(trigger.output_parser, JsonObject)

    def test_defaults(self):
        defn = hydrate_definition({})
        assert defn.name == "unnamed"
        assert defn.phase == "midflight"
        assert defn.lifetime == "ephemeral"
        assert defn.scope == "global"
        assert defn.model is None
        assert defn.system_prompt == ""


# ── Concurrency enum ──


class TestConcurrency:
    def test_values(self):
        assert Concurrency.skip_if_running == "skip_if_running"
        assert Concurrency.queue == "queue"
        assert Concurrency.parallel == "parallel"


# ── _extract_content: dotted transcript kinds ──
#
# Regression guard for the traceforge migration: transcript events fan out to
# role-specific dotted kinds (message.assistant, tool.call.completed, …) instead
# of the retired single "TranscriptUpdated" kind. _extract_content must key off
# TRANSCRIPT_KINDS membership, not the dead literal.


class TestExtractContent:
    def test_messages_agent_content(self):
        ev = new_event("j", EventKind.message_assistant, {"content": "hello"})
        assert SidecarDispatcher._extract_content(ev, "messages") == "hello"

    def test_messages_agent_delta_content(self):
        ev = new_event("j", EventKind.message_delta, {"content": "part"})
        assert SidecarDispatcher._extract_content(ev, "messages") == "part"

    def test_messages_operator_role_ignored(self):
        ev = new_event("j", EventKind.message_user, {"content": "hi"})
        assert SidecarDispatcher._extract_content(ev, "messages") is None

    def test_messages_non_transcript_kind_ignored(self):
        ev = new_event("j", EventKind.log_line_emitted, {"content": "x"})
        assert SidecarDispatcher._extract_content(ev, "messages") is None

    def test_tool_calls_returns_tool_name(self):
        ev = new_event("j", EventKind.tool_call_completed, {"tool_name": "Bash"})
        assert SidecarDispatcher._extract_content(ev, "tool_calls") == "Bash"

    def test_tool_output_returns_result(self):
        ev = new_event("j", EventKind.tool_call_completed, {"result": "done"})
        assert SidecarDispatcher._extract_content(ev, "tool_output") == "done"

    def test_tool_calls_non_transcript_kind_ignored(self):
        ev = new_event("j", EventKind.diff_updated, {"tool_name": "Bash"})
        assert SidecarDispatcher._extract_content(ev, "tool_calls") is None
