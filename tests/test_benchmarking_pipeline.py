"""Tests for extract_trace and compute_metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarking.compute_metrics import (
    compute_metrics,
)
from benchmarking.compute_metrics import (
    main as metrics_main,
)
from benchmarking.extract_trace import (
    _build_session_name,
    _detect_issue,
    _detect_model,
    _detect_repo,
    _find_marker_window,
    _has_codeplane,
    extract_trace,
)
from benchmarking.extract_trace import (
    main as extract_main,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_chatreplay(
    prompts: list[dict[str, Any]],
    mcp_servers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a minimal chatreplay structure."""
    return {
        "exportedAt": "2026-02-20T07:06:20.367Z",
        "totalPrompts": len(prompts),
        "totalLogEntries": sum(len(p.get("logs", [])) for p in prompts),
        "prompts": prompts,
        "mcpServers": mcp_servers or [],
    }


def _make_prompt(text: str, logs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "prompt": text,
        "promptId": "test-prompt",
        "hasSeen": False,
        "logCount": len(logs or []),
        "logs": logs or [],
    }


def _tool_call(
    tool: str,
    args: dict[str, Any] | None = None,
    response: Any = None,
) -> dict[str, Any]:
    return {
        "id": "tc1",
        "kind": "toolCall",
        "tool": tool,
        "args": args or {},
        "time": "2026-02-20T07:05:00.000Z",
        "response": response or [],
    }


def _llm_request(
    model: str = "claude-opus-4.6-fast",
    prompt_tokens: int = 1000,
    completion_tokens: int = 200,
    cached_tokens: int = 0,
    duration_ms: int = 3000,
    response_message: str | list[str] = "",
) -> dict[str, Any]:
    return {
        "id": "req1",
        "kind": "request",
        "type": "ChatMLSuccess",
        "name": "panel/editAgent",
        "metadata": {
            "model": model,
            "startTime": "2026-02-20T07:05:00.000Z",
            "endTime": "2026-02-20T07:05:03.000Z",
            "duration": duration_ms,
            "timeToFirstToken": 1000,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
            "tools": [],
        },
        "response": {"type": "ChatMLSuccess", "message": response_message},
    }


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


class TestDetectRepo:
    def test_codeplane_label(self) -> None:
        data = _make_chatreplay([], mcp_servers=[{"label": "codeplane-evee", "type": "http"}])
        assert _detect_repo(data) == "evee"

    def test_codeplane_label_with_copy_suffix(self) -> None:
        data = _make_chatreplay([], mcp_servers=[{"label": "codeplane-evee_copy3", "type": "http"}])
        assert _detect_repo(data) == "evee"

    def test_no_codeplane(self) -> None:
        data = _make_chatreplay([], mcp_servers=[{"label": "other-server", "type": "http"}])
        assert _detect_repo(data) == "unknown"


class TestDetectIssue:
    def test_branch_pattern(self) -> None:
        prompts = [_make_prompt("checkout bench/233-early-stop")]
        assert _detect_issue(prompts) == "233"

    def test_issue_hash(self) -> None:
        prompts = [_make_prompt("Implement issue #260 for this repo")]
        assert _detect_issue(prompts) == "260"

    def test_no_issue(self) -> None:
        prompts = [_make_prompt("just do something")]
        assert _detect_issue(prompts) == "unknown"


class TestDetectModel:
    def test_primary_model(self) -> None:
        prompts = [
            _make_prompt(
                "test",
                [
                    _llm_request(model="gpt-4o-mini"),
                    _llm_request(model="claude-opus-4.6-fast"),
                    _llm_request(model="claude-opus-4.6-fast"),
                ],
            )
        ]
        assert _detect_model(prompts) == "claude-opus-4.6-fast"

    def test_ignores_routing_models(self) -> None:
        prompts = [
            _make_prompt(
                "test",
                [
                    _llm_request(model="gpt-4o-mini"),
                    _llm_request(model="gpt-4o-mini"),
                    _llm_request(model="claude-opus-4.6-fast"),
                ],
            )
        ]
        assert _detect_model(prompts) == "claude-opus-4.6-fast"


class TestHasCodeplane:
    def test_with_codeplane(self) -> None:
        prompts = [_make_prompt("test", [_tool_call("mcp_codeplane-eve_map_repo")])]
        assert _has_codeplane(prompts) is True

    def test_without_codeplane(self) -> None:
        prompts = [_make_prompt("test", [_tool_call("run_in_terminal")])]
        assert _has_codeplane(prompts) is False


class TestBuildSessionName:
    def test_codeplane(self) -> None:
        name = _build_session_name("evee", "233", "claude-opus-4.6-fast", True)
        assert name == "evee_233_claude-opus-4-6-fast_codeplane"

    def test_native(self) -> None:
        name = _build_session_name("evee", "260", "claude-opus-4.6-fast", False)
        assert name == "evee_260_claude-opus-4-6-fast_native"


# ---------------------------------------------------------------------------
# Marker window tests
# ---------------------------------------------------------------------------


class TestMarkerWindow:
    def test_both_markers_in_prompts(self) -> None:
        prompts = [
            _make_prompt("before"),
            _make_prompt("START_BENCHMARKING_RUN\ndo stuff"),
            _make_prompt("middle step"),
            _make_prompt("END_BENCHMARKING_RUN"),
            _make_prompt("after"),
        ]
        window = _find_marker_window(prompts)
        assert window == (1, 3)

    def test_end_in_llm_response(self) -> None:
        prompts = [
            _make_prompt(
                "START_BENCHMARKING_RUN\ndo stuff",
                [_llm_request(response_message="Done! END_BENCHMARKING_RUN")],
            ),
        ]
        window = _find_marker_window(prompts)
        assert window == (0, 0)

    def test_no_start(self) -> None:
        prompts = [_make_prompt("no markers here")]
        assert _find_marker_window(prompts) is None

    def test_no_end_defaults_to_last(self) -> None:
        prompts = [
            _make_prompt("START_BENCHMARKING_RUN\ngo"),
            _make_prompt("still going"),
        ]
        window = _find_marker_window(prompts)
        assert window == (0, 1)


# ---------------------------------------------------------------------------
# Trace extraction tests
# ---------------------------------------------------------------------------


class TestExtractTrace:
    def test_tool_and_llm_events(self) -> None:
        prompts = [
            _make_prompt(
                "START_BENCHMARKING_RUN\ngo",
                [
                    _tool_call("mcp_codeplane-eve_search", {"query": "foo"}),
                    _llm_request(model="claude-opus-4.6-fast", prompt_tokens=500),
                    _tool_call("run_in_terminal", {"command": "ls"}),
                ],
            )
        ]
        events = extract_trace(prompts)
        assert len(events) == 3
        assert events[0]["type"] == "tool_call"
        assert events[0]["tool"] == "mcp_codeplane-eve_search"
        assert events[1]["type"] == "llm_request"
        assert events[1]["model"] == "claude-opus-4.6-fast"
        assert events[2]["type"] == "tool_call"

    def test_prompt_index_set(self) -> None:
        prompts = [
            _make_prompt("p0", [_tool_call("t1")]),
            _make_prompt("p1", [_tool_call("t2")]),
        ]
        events = extract_trace(prompts)
        assert events[0]["prompt_index"] == 0
        assert events[1]["prompt_index"] == 1


# ---------------------------------------------------------------------------
# Compute metrics tests
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def _sample_trace(self) -> dict[str, Any]:
        return {
            "session_name": "evee_233_claude-opus-4-6-fast_codeplane",
            "repo": "evee",
            "issue": "233",
            "model": "claude-opus-4.6-fast",
            "codeplane": True,
            "events": [
                {
                    "type": "tool_call",
                    "tool": "mcp_codeplane-eve_search",
                    "args": {},
                    "time": "2026-02-20T07:05:00.000Z",
                    "response": [],
                },
                {
                    "type": "tool_call",
                    "tool": "mcp_codeplane-eve_read_source",
                    "args": {},
                    "time": "2026-02-20T07:05:01.000Z",
                    "response": [],
                },
                {
                    "type": "tool_call",
                    "tool": "run_in_terminal",
                    "args": {},
                    "time": "2026-02-20T07:05:02.000Z",
                    "response": [],
                },
                {
                    "type": "tool_call",
                    "tool": "tool_search_tool_regex [server]",
                    "args": {},
                    "time": "2026-02-20T07:05:03.000Z",
                    "response": [],
                },
                {
                    "type": "tool_call",
                    "tool": "run_in_terminal",
                    "args": {},
                    "time": "2026-02-20T07:05:04.000Z",
                    "response": ["ERROR: command failed"],
                },
                {
                    "type": "llm_request",
                    "model": "claude-opus-4.6-fast",
                    "start_time": "2026-02-20T07:05:00.000Z",
                    "duration_ms": 3000,
                    "prompt_tokens": 10000,
                    "completion_tokens": 500,
                    "cached_tokens": 2000,
                },
                {
                    "type": "llm_request",
                    "model": "gpt-4o-mini",
                    "start_time": "2026-02-20T07:05:05.000Z",
                    "duration_ms": 500,
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "cached_tokens": 0,
                },
            ],
        }

    def test_tool_counts(self) -> None:
        metrics = compute_metrics(self._sample_trace())
        tc = metrics["tool_calls"]
        assert tc["total"] == 5
        assert tc["codeplane"] == 2
        assert tc["terminal"] == 2
        assert tc["tool_search"] == 1
        assert tc["errors"] == 1

    def test_llm_counts(self) -> None:
        metrics = compute_metrics(self._sample_trace())
        assert metrics["llm_requests"]["agent"] == 1
        assert metrics["llm_requests"]["routing"] == 1
        assert metrics["llm_requests"]["total"] == 2

    def test_tokens(self) -> None:
        metrics = compute_metrics(self._sample_trace())
        tok = metrics["tokens"]
        assert tok["prompt"] == 10000
        assert tok["completion"] == 500
        assert tok["cached"] == 2000
        assert tok["total"] == 10500

    def test_turns(self) -> None:
        metrics = compute_metrics(self._sample_trace())
        assert metrics["turns"] == 1  # only 1 agent LLM request


# ---------------------------------------------------------------------------
# CLI integration tests (end-to-end with tmp files)
# ---------------------------------------------------------------------------


class TestExtractTraceCLI:
    def test_full_pipeline(self, tmp_path: Path) -> None:
        """Extract → raw + trace → metrics."""
        prompts = [
            _make_prompt("unrelated"),
            _make_prompt(
                "START_BENCHMARKING_RUN\ncheckout bench/260-disable-progress-bars",
                [
                    _tool_call("tool_search_tool_regex [server]"),
                    _llm_request(model="claude-opus-4.6-fast"),
                    _tool_call("mcp_codeplane-eve_search", {"query": "progress"}),
                    _llm_request(model="claude-opus-4.6-fast"),
                    _tool_call(
                        "mcp_codeplane-eve_write_source",
                        {"edits": []},
                    ),
                    _llm_request(
                        model="claude-opus-4.6-fast",
                        response_message="All done! END_BENCHMARKING_RUN",
                    ),
                ],
            ),
            _make_prompt("unrelated after"),
        ]
        chatreplay = _make_chatreplay(
            prompts,
            mcp_servers=[{"label": "codeplane-evee", "type": "http"}],
        )
        input_file = tmp_path / "replay.json"
        input_file.write_text(json.dumps(chatreplay))
        output_dir = tmp_path / "results"

        # Run extract_trace
        rc = extract_main([str(input_file), "--output-dir", str(output_dir)])
        assert rc == 0

        # Check outputs exist with correct naming
        expected_prefix = "evee_260_claude-opus-4-6-fast_codeplane"
        raw_file = output_dir / f"{expected_prefix}_raw.json"
        trace_file = output_dir / f"{expected_prefix}_trace.json"
        assert raw_file.exists()
        assert trace_file.exists()

        # Raw should have only the marker-window prompt (prompt index 1)
        raw = json.loads(raw_file.read_text())
        assert raw["totalPrompts"] == 1

        # Trace should have events
        trace = json.loads(trace_file.read_text())
        assert trace["total_events"] == 6
        assert trace["codeplane"] is True

        # Now run compute_metrics on the trace
        rc2 = metrics_main([str(trace_file), "--output-dir", str(output_dir)])
        assert rc2 == 0

        metrics_file = output_dir / f"{expected_prefix}_result_metrics.json"
        assert metrics_file.exists()

        metrics = json.loads(metrics_file.read_text())
        assert metrics["tool_calls"]["codeplane"] == 2
        assert metrics["turns"] == 3

    def test_missing_markers(self, tmp_path: Path) -> None:
        chatreplay = _make_chatreplay([_make_prompt("no markers")])
        input_file = tmp_path / "replay.json"
        input_file.write_text(json.dumps(chatreplay))

        rc = extract_main([str(input_file)])
        assert rc == 1
