"""Strict, evidence-bound utility output. These facts never grant permission."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

Mechanism = Literal["file", "shell", "mcp", "sdk"]
Effect = Literal["read_only", "mutating", "destructive", "unknown"]
Scope = Literal["workspace", "external", "mixed", "unknown"]
Network = Literal["none", "outbound", "unknown"]
Reversibility = Literal["not_applicable", "git", "external", "irreversible", "unknown"]
ActionCapability = Literal[
    "credential_exposure",
    "raw_device_write",
    "filesystem_format",
    "persistence_write",
    "fork_bomb",
    "network_exec",
    "privilege_change",
    "external_service_write",
]
ActionStructure = Literal["pipeline", "redirection", "substitution", "encoded", "script", "compound"]
Unknown = Literal[
    "missing_dialect",
    "unresolved_expansion",
    "opaque_script",
    "redacted_semantics",
    "ambiguous_target",
    "untrusted_metadata",
    "incomplete_command",
    "conflicting_evidence",
]
EvidenceSource = Literal["command", "arguments", "tool_definition", "context"]
EvidenceField = Literal["effect", "scope", "network", "reversibility", "capability", "structure"]


class _StrictFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AssessmentEvidence(_StrictFacts):
    field: EvidenceField
    value: Annotated[str, Field(min_length=1, max_length=64)]
    source: EvidenceSource
    start: Annotated[int, Field(ge=0, le=32767)]
    end: Annotated[int, Field(ge=1, le=32768)]


class ActionAssessment(_StrictFacts):
    schema_version: Literal["cp-action-v1"]
    mechanism: Mechanism
    effect: Effect
    scope: Scope
    network: Network
    reversibility: Reversibility
    capabilities: Annotated[tuple[ActionCapability, ...], Field(max_length=8, json_schema_extra={"uniqueItems": True})]
    structure: Annotated[tuple[ActionStructure, ...], Field(max_length=6, json_schema_extra={"uniqueItems": True})]
    evidence: Annotated[tuple[AssessmentEvidence, ...], Field(max_length=24)]
    unknowns: Annotated[tuple[Unknown, ...], Field(max_length=8, json_schema_extra={"uniqueItems": True})]

    @field_validator("capabilities", "structure", "unknowns")
    @classmethod
    def _unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Assessment arrays must contain unique values")
        return values

    @model_validator(mode="after")
    def _bound_evidence(self, info: ValidationInfo) -> Self:
        fields = info.context
        if not isinstance(fields, dict) or set(fields) != {"command", "arguments", "tool_definition", "context"}:
            raise ValueError("Validation requires the exact four sanitized source fields")
        if any(not isinstance(value, str) for value in fields.values()):
            raise ValueError("Sanitized evidence sources must be strings")
        if sum(len(value.encode("utf-8")) for value in fields.values()) > 32768:
            raise ValueError("Sanitized assessment input exceeds 32 KiB")
        for evidence in self.evidence:
            source = fields[evidence.source]
            if not 0 <= evidence.start < evidence.end <= len(source):
                raise ValueError("Evidence must point to a nonempty Unicode codepoint span in its sanitized source")
        actual = {(item.field, item.value) for item in self.evidence}
        expected = {
            (name, str(value))
            for name, value in (
                ("effect", self.effect),
                ("scope", self.scope),
                ("network", self.network),
                ("reversibility", self.reversibility),
            )
            if value != "unknown"
        }
        expected.update(("capability", value) for value in self.capabilities)
        expected.update(("structure", value) for value in self.structure)
        if not expected <= actual:
            raise ValueError("Every nonunknown fact must have matching evidence")
        if actual - expected:
            raise ValueError("Evidence contradicts or describes a fact absent from the assessment")
        if self.effect == "read_only" and self.reversibility != "not_applicable":
            raise ValueError("Read-only actions require not_applicable reversibility")
        if self.effect in {"mutating", "destructive"} and self.reversibility == "not_applicable":
            raise ValueError("Mutation cannot have not_applicable reversibility")
        return self

    @property
    def complete(self) -> bool:
        return not self.unknowns and "unknown" not in {
            self.effect,
            self.scope,
            self.network,
            self.reversibility,
        }
