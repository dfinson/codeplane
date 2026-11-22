import copy
import hashlib
import json

import pytest

from backend.models.action_assessment import ActionAssessment
from backend.services.adapters.agent_adapter import UtilityCompletionError
from backend.services.copilot_adapter._schema import prepare_response_schema
from backend.services.events.title_refinement import _Title


def test_assessment_and_title_wire_projection_preserves_full_local_contract():
    full = ActionAssessment.model_json_schema()
    before = copy.deepcopy(full)
    prepared = prepare_response_schema(full)
    wire = prepared.wire_schema
    assert full == prepared.full_schema == before
    assert wire["required"] == full["required"]
    assert wire["additionalProperties"] is False
    assert wire["properties"]["effect"]["enum"] == full["properties"]["effect"]["enum"]
    assert wire["properties"]["effect"]["type"] == "string"
    assert wire["properties"]["schema_version"]["const"] == "cp-action-v1"
    assert wire["properties"]["schema_version"]["type"] == "string"
    evidence = wire["$defs"]["AssessmentEvidence"]["properties"]
    assert evidence["field"]["enum"] == full["$defs"]["AssessmentEvidence"]["properties"]["field"]["enum"]
    assert evidence["source"]["enum"] == full["$defs"]["AssessmentEvidence"]["properties"]["source"]["enum"]
    assert evidence["field"]["type"] == evidence["source"]["type"] == "string"
    assert "minimum" not in evidence["start"] and "maximum" not in evidence["start"]
    assert '"minimum":0' in evidence["start"]["description"]
    assert '"maximum":32767' in evidence["start"]["description"]
    assert "minLength" not in evidence["value"] and "maxLength" not in evidence["value"]
    assert wire["properties"]["evidence"]["items"] == {"$ref": "#/$defs/AssessmentEvidence"}
    for name in ("capabilities", "structure", "unknowns"):
        assert "uniqueItems" not in wire["properties"][name]
        assert "maxItems" not in wire["properties"][name]
        assert full["properties"][name]["uniqueItems"] is True
    assert (
        prepared.full_sha256
        == hashlib.sha256(
            json.dumps(full, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert prepared.full_sha256 != prepared.wire_sha256
    assert prepare_response_schema(json.loads(json.dumps(full, sort_keys=True))).provenance == prepared.provenance
    title = prepare_response_schema(_Title.model_json_schema())
    assert title.full_schema["properties"]["title"]["maxLength"] == 80
    assert "maxLength" not in title.wire_schema["properties"]["title"]
    assert title.wire_schema["required"] == ["title"]


def test_schema_keyword_property_names_and_reference_definitions_are_not_stripped_or_aliased():
    full = {
        "$ref": "#/$defs/minimum",
        "$defs": {
            "minimum": {
                "type": "object",
                "properties": {"maximum": {"type": "integer", "minimum": 1}},
                "required": ["maximum"],
                "additionalProperties": False,
            }
        },
    }
    before = copy.deepcopy(full)
    prepared = prepare_response_schema(full)
    definition = prepared.wire_schema["$defs"]["minimum"]
    assert prepared.wire_schema["$ref"] == "#/$defs/minimum"
    assert "maximum" in definition["properties"]
    assert "minimum" not in definition["properties"]["maximum"]
    definition["required"].append("changed-wire-only")
    assert full == prepared.full_schema == before


def test_typed_enums_and_constants_keep_their_declared_openai_compatible_types():
    full = {
        "type": "object",
        "properties": {
            "level": {"type": "integer", "enum": [1, 2]},
            "kind": {"type": "string", "const": "entry"},
        },
        "required": ["level", "kind"],
        "additionalProperties": False,
    }
    prepared = prepare_response_schema(full)
    assert prepared.wire_schema == prepared.full_schema == full
    assert prepared.full_sha256 == prepared.wire_sha256


@pytest.mark.parametrize("minimum", [0, 1, 2])
def test_native_supported_array_minima_and_nested_union_formats(minimum):
    full = {
        "type": "array",
        "minItems": minimum,
        "maxItems": 8,
        "items": {
            "anyOf": [
                {"type": "string", "format": "uuid", "minLength": 36},
                {"type": "string", "format": "unsupported-format", "pattern": "^value"},
                {"type": "null"},
            ]
        },
    }
    wire = prepare_response_schema(full).wire_schema
    assert ("minItems" in wire) is (minimum in {0, 1})
    assert "maxItems" not in wire
    assert wire["items"]["anyOf"][0]["format"] == "uuid"
    assert "minLength" not in wire["items"]["anyOf"][0]
    assert "format" not in wire["items"]["anyOf"][1] and "pattern" not in wire["items"]["anyOf"][1]
    assert wire["items"]["anyOf"][2] == {"type": "null"}


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object"},
        {"type": "object", "properties": {"value": {"type": "integer"}}},
        {"type": "object", "additionalProperties": True},
        {"enum": ["missing", "type"]},
        {"type": "object", "not": {"required": ["secret"]}},
        {"oneOf": [{"type": "integer"}, {"type": "number"}]},
        {"$ref": "#/$defs/item", "enum": ["must-not-be-dropped"]},
    ],
)
def test_unsupported_structural_rules_fail_explicitly_instead_of_disappearing(schema):
    with pytest.raises(UtilityCompletionError) as failure:
        prepare_response_schema(schema)
    assert failure.value.reason == "response_schema_unsupported"
