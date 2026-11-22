"""Anthropic-compatible Copilot wire schemas, separate from application validation.

Supported keywords follow anthropic-sdk-python 2.10.0's
src/anthropic/lib/_parse/_transform.py. Unsupported scalar/collection bounds
remain descriptive guidance on the wire and mandatory in the caller's validator.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from backend.services.adapters.agent_adapter import UtilityCompletionError

PROJECTION_VERSION = "cp-copilot-wire-schema-v1"
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
_LOCAL_CONSTRAINTS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
}


@dataclass(frozen=True, slots=True)
class PreparedSchema:
    full_schema: dict[str, object]
    wire_schema: dict[str, object]
    full_sha256: str
    wire_sha256: str

    @property
    def provenance(self) -> dict[str, str]:
        return {
            "projection_version": PROJECTION_VERSION,
            "full_sha256": self.full_sha256,
            "wire_sha256": self.wire_sha256,
        }


def _encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _schema_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("Schema properties and definitions must be named objects")
    return {key: _project(child) for key, child in value.items()}


def _primitive(value: object) -> bool:
    return value is None or isinstance(value, (str, bool, int, float))


def _project(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Expected an object schema")
    if "$ref" in value:
        if set(value) - {"$ref", "$defs", "title", "description"}:
            raise ValueError("Wire references cannot discard sibling validation rules")
        if not isinstance(value["$ref"], str):
            raise ValueError("Schema references must be strings")
        result: dict[str, object] = {"$ref": value["$ref"]}
        if "$defs" in value:
            result["$defs"] = _schema_map(value["$defs"])
        return result

    result = {}
    local: dict[str, object] = {}
    for key, child in value.items():
        if key in _LOCAL_CONSTRAINTS or key in {"default", "examples", "$schema"}:
            local[key] = child
        elif key in {"properties", "$defs"}:
            result[key] = _schema_map(child)
        elif key == "items":
            result[key] = _project(child)
        elif key in {"anyOf", "allOf"}:
            if not isinstance(child, list) or not child:
                raise ValueError("Schema alternatives must be a nonempty list")
            result[key] = [_project(item) for item in child]
        elif key == "minItems":
            if type(child) is not int or child < 0:
                raise ValueError("Array minimum must be a nonnegative integer")
            (result if child in {0, 1} else local)[key] = child
        elif key == "format":
            (result if isinstance(child, str) and child in _FORMATS else local)[key] = child
        elif key == "enum":
            if not isinstance(child, list) or not child or not all(_primitive(item) for item in child):
                raise ValueError("Wire enums require primitive values")
            result[key] = child
        elif key == "const":
            if not _primitive(child):
                raise ValueError("Wire constants require primitive values")
            result[key] = child
        elif key == "required":
            if not isinstance(child, list) or not all(isinstance(item, str) for item in child):
                raise ValueError("Required fields must be a list of names")
            result[key] = child
        elif key in {"title", "description"}:
            if not isinstance(child, str):
                raise ValueError("Schema annotations must be strings")
            result[key] = child
        elif key == "type":
            if not isinstance(child, str) or child not in _TYPES:
                raise ValueError("Unsupported wire schema type")
            result[key] = child
        elif key == "additionalProperties":
            if child is not False:
                raise ValueError("Wire object schemas require closed properties")
            result[key] = False
        else:
            raise ValueError(f"Unsupported wire schema keyword: {key}")

    if value.get("type") == "object":
        result.setdefault("properties", {})
        result["additionalProperties"] = False
    if "enum" in result or "const" in result:
        result.pop("type", None)
    if not {"type", "enum", "const", "anyOf", "allOf"} & result.keys():
        raise ValueError("Schema requires a type, literal, reference or composition")
    if local:
        description = result.get("description", "")
        result["description"] = (
            (f"{description}\n\n" if description else "")
            + "Application validation constraints (still required): "
            + _encoded(local)
        )
    return result


def prepare_response_schema(schema: dict[str, object]) -> PreparedSchema:
    """Snapshot the caller's schema without mutating or substituting its validator."""
    try:
        full = copy.deepcopy(schema)
        full_digest = hashlib.sha256(_encoded(full).encode("utf-8")).hexdigest()
        wire = _project(copy.deepcopy(full))
        wire_digest = hashlib.sha256(_encoded(wire).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, RecursionError) as exc:
        raise UtilityCompletionError(
            "The response schema cannot be represented by the provider wire subset",
            reason="response_schema_unsupported",
        ) from exc
    return PreparedSchema(full, wire, full_digest, wire_digest)
