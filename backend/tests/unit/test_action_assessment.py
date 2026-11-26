import json

import pytest
from pydantic import ValidationError

from backend.models.action_assessment import ActionAssessment


def _assessment():
    facts = {
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
    facts["evidence"] = [
        {"field": field, "value": facts[field], "source": "command", "start": 0, "end": 3}
        for field in ("effect", "scope", "network", "reversibility")
    ]
    return facts


FIELDS = {"command": "pwd", "arguments": "", "tool_definition": "", "context": ""}


def test_complete_facts_require_exact_sanitized_unicode_evidence() -> None:
    assessment = ActionAssessment.model_validate_json(json.dumps(_assessment()), context=FIELDS)
    assert assessment.complete
    with pytest.raises(ValidationError):
        ActionAssessment.model_validate_json(json.dumps(_assessment()))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "cp-action-v2"),
        ("authority", "allow"),
        ("mechanism", "universal"),
        ("unknowns", ["missing_dialect", "missing_dialect"]),
        ("capabilities", ["credential_exposure"]),
        ("reversibility", "git"),
        ("effect", "mutating"),
    ],
)
def test_extra_inconsistent_unsupported_or_unevidenced_facts_fail(field, value) -> None:
    facts = _assessment()
    facts[field] = value
    with pytest.raises(ValidationError):
        ActionAssessment.model_validate_json(json.dumps(facts), context=FIELDS)


@pytest.mark.parametrize("span", [(2, 2), (2, 4), (-1, 1), (True, 1)])
def test_evidence_spans_are_codepoints_not_bytes_or_coerced_booleans(span) -> None:
    fields = dict(FIELDS, command="éx")
    facts = _assessment()
    for evidence in facts["evidence"]:
        evidence["start"], evidence["end"] = span
    with pytest.raises(ValidationError):
        ActionAssessment.model_validate_json(json.dumps(facts), context=fields)


def test_unknown_facts_are_valid_but_never_complete() -> None:
    facts = _assessment()
    facts.update(effect="unknown", scope="unknown", network="unknown", reversibility="unknown")
    facts.update(evidence=[], unknowns=["redacted_semantics"])
    assessment = ActionAssessment.model_validate_json(json.dumps(facts), context=FIELDS)
    assert not assessment.complete


def test_json_schema_rejects_extra_properties_and_requires_every_field() -> None:
    schema = ActionAssessment.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(_assessment())
    assert schema["$defs"]["AssessmentEvidence"]["additionalProperties"] is False
