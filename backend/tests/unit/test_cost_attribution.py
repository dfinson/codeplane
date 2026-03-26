from backend.services.cost_attribution import (
    _allocate_weighted_totals,
    _derive_activity_weights,
    _infer_execution_phases,
)


def test_infer_execution_phases_uses_neighboring_valid_phases() -> None:
    spans = [
        {"execution_phase": None},
        {"execution_phase": "agent_reasoning"},
        {"execution_phase": None},
        {"execution_phase": "verification"},
        {"execution_phase": None},
    ]

    assert _infer_execution_phases(spans) == [
        "agent_reasoning",
        "agent_reasoning",
        "agent_reasoning",
        "verification",
        "verification",
    ]


def test_infer_execution_phases_does_not_invent_unknown_bucket() -> None:
    spans = [
        {"execution_phase": None},
        {"execution_phase": "unknown"},
    ]

    assert _infer_execution_phases(spans) == [None, None]


def test_derive_activity_weights_maps_useful_buckets() -> None:
    assert _derive_activity_weights(phase="verification", tool_categories=["file_write"]) == {"verification": 1}
    assert _derive_activity_weights(phase="agent_reasoning", tool_categories=["file_read", "file_write", "shell"]) == {
        "code_reading": 1,
        "code_changes": 1,
        "command_execution": 1,
    }
    assert _derive_activity_weights(phase="agent_reasoning", tool_categories=[]) == {"reasoning": 1}


def test_allocate_weighted_totals_splits_turn_cost_across_activities() -> None:
    allocations = _allocate_weighted_totals(
        weights={"code_reading": 1, "code_changes": 2},
        cost_usd=9.0,
        input_tokens=90,
        output_tokens=45,
    )

    assert allocations == {
        "code_reading": {"cost_usd": 3.0, "input_tokens": 30, "output_tokens": 15},
        "code_changes": {"cost_usd": 6.0, "input_tokens": 60, "output_tokens": 30},
    }