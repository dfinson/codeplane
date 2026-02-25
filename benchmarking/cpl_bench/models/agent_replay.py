"""Agent replay model — passes pre-collected agent trace data through for metrics.

Registered as ``@model("cpl-agent-replay")`` for EVEE evaluation.
"""

from __future__ import annotations

from evee import model


@model("cpl-agent-replay")
class AgentReplayModel:
    """Replay pre-collected agent traces through EVEE metrics.

    This model does not perform live inference.  It receives a pre-collected
    trace record from the dataset and extracts the fields needed by the
    efficiency and outcome metrics.

    Config args:
        (none — all data comes from the dataset)
    """

    def __init__(self, **kwargs: object) -> None:
        pass

    def infer(self, input: dict) -> dict:  # noqa: A002
        """Pass through trace data for metric computation.

        Expects pre-processed trace records from the ``cpl-agent-traces`` dataset.
        """
        events = input.get("events", [])
        tool_events = [e for e in events if e.get("type") == "tool_call"]
        llm_events = [e for e in events if e.get("type") == "llm_request"]

        # Classify tool calls
        codeplane_calls = [e for e in tool_events if "codeplane" in (e.get("tool") or "").lower()]
        terminal_calls = [e for e in tool_events if "run_in_terminal" in (e.get("tool") or "")]
        tool_search_calls = [
            e for e in tool_events if "tool_search" in (e.get("tool") or "").lower()
        ]

        # Filter routing models from LLM events
        routing_models = {"gpt-4o-mini", "gpt-3.5-turbo"}
        agent_llm_events = [e for e in llm_events if e.get("model") not in routing_models]

        # Token aggregation
        prompt_tokens = sum(e.get("prompt_tokens", 0) or 0 for e in agent_llm_events)
        completion_tokens = sum(e.get("completion_tokens", 0) or 0 for e in agent_llm_events)
        cached_tokens = sum(e.get("cached_tokens", 0) or 0 for e in agent_llm_events)

        return {
            # Pass through identity fields
            "variant": input.get("variant", "unknown"),
            "issue": input.get("issue", "unknown"),
            # Computed fields for metrics
            "turns": len(agent_llm_events),
            "total_tool_calls": len(tool_events),
            "codeplane_tool_calls": len(codeplane_calls),
            "terminal_tool_calls": len(terminal_calls),
            "tool_search_calls": len(tool_search_calls),
            "other_tool_calls": len(tool_events)
            - len(codeplane_calls)
            - len(terminal_calls)
            - len(tool_search_calls),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cache_hit_ratio": cached_tokens / prompt_tokens if prompt_tokens else 0.0,
            # Outcome (pre-scored, may be empty)
            "outcome": input.get("outcome", {}),
        }
