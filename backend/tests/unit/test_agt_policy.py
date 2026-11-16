import json

import pytest

from backend.models.action_assessment import ActionAssessment
from backend.services.action_policy.agt_policy import AgtPolicy, PolicyInput
from backend.services.action_policy.classifier import Preset
from backend.services.action_policy.syntax import analyze_syntax


def _facts(*, effect="read_only", network="none", scope="workspace", reversibility="not_applicable", **overrides):
    raw = {
        "schema_version": "cp-action-v1",
        "mechanism": "shell",
        "effect": effect,
        "scope": scope,
        "network": network,
        "reversibility": reversibility,
        "capabilities": [],
        "structure": [],
        "unknowns": [],
    }
    raw.update(overrides)
    raw["evidence"] = [
        {"field": field, "value": raw[field], "source": "command", "start": 0, "end": 3}
        for field in ("effect", "scope", "network", "reversibility")
        if raw[field] != "unknown"
    ]
    return ActionAssessment.model_validate_json(
        json.dumps(raw), context={"command": "pwd", "arguments": "", "tool_definition": "", "context": ""}
    )


@pytest.fixture
def policy():
    instance = AgtPolicy()
    assert instance.unavailable_reason is None, f"Selected AGT core unavailable: {instance.unavailable_reason}"
    return instance


@pytest.mark.parametrize(
    ("preset", "expected"),
    [(Preset.autonomous, "allow"), (Preset.supervised, "warn"), (Preset.locked, "escalate")],
)
def test_complete_git_workspace_mutation_preserves_preset_posture(policy, preset, expected) -> None:
    result = policy.decide(
        PolicyInput(
            "job",
            preset,
            assessment=_facts(effect="mutating", reversibility="git"),
            syntax=analyze_syntax("pwd", "bash"),
        )
    )
    assert result.action == expected
    assert result.risk_score is None and result.risk_band == "unknown"


def test_hard_gates_and_incomplete_evidence_cannot_be_overridden_by_budget_or_trust(policy) -> None:
    syntax = analyze_syntax("pwd", "bash")
    hard = policy.decide(
        PolicyInput(
            "job",
            Preset.autonomous,
            assessment=_facts(),
            syntax=syntax,
            hard_gate=True,
            budget_pressure=True,
        )
    )
    assert hard.action == "deny" and hard.individual_human and not hard.monitor_eligible
    incomplete = policy.decide(
        PolicyInput(
            "job",
            Preset.autonomous,
            assessment=_facts(),
            syntax=analyze_syntax("echo $unknown", "bash"),
        )
    )
    assert incomplete.action == "escalate" and incomplete.individual_human and not incomplete.monitor_eligible
    unavailable = policy.decide(PolicyInput("job", Preset.autonomous))
    assert unavailable.action == "escalate" and unavailable.individual_human


def test_local_mutation_fact_cannot_be_erased_by_model_read_only_claim(policy) -> None:
    result = policy.decide(
        PolicyInput(
            "job",
            Preset.autonomous,
            assessment=_facts(),
            syntax=analyze_syntax("pwd", "bash"),
            local_effect="mutating",
        )
    )
    assert result.action == "escalate" and result.individual_human


def test_preflight_is_stateless_and_unloaded_policy_is_nonwaivable(policy) -> None:
    request = PolicyInput("job", Preset.autonomous, trusted_file_read=True)
    assert [policy.decide(request).action for _ in range(3)] == ["allow"] * 3
    assert policy._engine._rate_limits == {}
    policy._engine.remove_policy("cp-policy-v1")
    result = policy.decide(request)
    assert result.action == "escalate" and result.individual_human


def test_terminal_execution_rejects_even_when_policy_is_unavailable() -> None:
    policy = AgtPolicy()
    policy._engine = None
    assert policy.decide(PolicyInput("job", Preset.autonomous, terminal=True)).action == "reject"
