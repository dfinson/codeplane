"""Recipe definitions and immutable revision contracts.

Recipe nodes are not transcript turns or independently managed TaskLinks.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue

from backend.models.schemas.base import CamelModel

RecipeValueType = Literal["string", "integer", "number", "boolean", "object", "array", "code_ref"]
RecipeCapability = Literal["read", "write", "shell", "delegate", "external_access"]
RecipeState = Literal[
    "blocked", "ready", "running", "needs_decision", "succeeded", "failed", "skipped", "canceled", "interrupted"
]
RecipeIdentifier = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,79}$")]


class RecipeModel(CamelModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RecipeParameter(RecipeModel):
    type: RecipeValueType
    description: str = ""
    required: bool = True
    default: JsonValue = None


class RecipeParameterBinding(RecipeModel):
    source: Literal["parameter"] = "parameter"
    parameter: str


class RecipeOutputBinding(RecipeModel):
    source: Literal["output"] = "output"
    step_id: str
    output: str


class RecipeLiteralBinding(RecipeModel):
    source: Literal["literal"] = "literal"
    value: JsonValue


RecipeBinding = Annotated[
    RecipeParameterBinding | RecipeOutputBinding | RecipeLiteralBinding, Field(discriminator="source")
]


class RecipeInput(RecipeModel):
    type: RecipeValueType
    binding: RecipeBinding | None = None
    optional: bool = False
    degraded_reason: str | None = None


class RecipeOutput(RecipeModel):
    type: RecipeValueType
    description: str = ""


class RecipeExport(RecipeOutput):
    binding: RecipeOutputBinding


class RecipeRoute(RecipeModel):
    next_steps: list[str] = Field(default_factory=list, max_length=128)
    terminal: Literal["succeeded", "failed", "canceled"] | None = None


class RecipeRetryPolicy(RecipeModel):
    # max_attempts counts the first execution, not just additional retries.
    max_attempts: int = Field(default=1, ge=1, le=20)
    retry_on: list[Literal["failed"]] = Field(default_factory=list)
    exhaustion: RecipeRoute = Field(default_factory=lambda: RecipeRoute(terminal="failed"))


class RecipeWorkspacePolicy(RecipeModel):
    mode: Literal["none", "snapshot", "mutable", "isolated"] = "snapshot"
    key: str = Field(default="job", min_length=1, max_length=80)


class RecipeAgentRunner(RecipeModel):
    kind: Literal["agent"] = "agent"
    provider: str | None = None
    model: str | None = None
    max_turns: int = Field(default=1, ge=1, le=100)
    session: Literal["new", "reuse"] = "new"


class RecipeCommandRunner(RecipeModel):
    kind: Literal["command"] = "command"
    argv: list[str] = Field(default_factory=list, max_length=256)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    reconciles: list[str] = Field(default_factory=list, max_length=128)


class RecipeHumanRunner(RecipeModel):
    kind: Literal["human"] = "human"
    proposed_action: str = ""
    decisions: list[str] = Field(default_factory=lambda: ["approved", "rejected"], max_length=16)


class RecipeBlockRunner(RecipeModel):
    kind: Literal["block"] = "block"
    revision_id: str


RecipeRunner = Annotated[
    RecipeAgentRunner | RecipeCommandRunner | RecipeHumanRunner | RecipeBlockRunner, Field(discriminator="kind")
]


class RecipeDependency(RecipeModel):
    step_id: str
    outcomes: list[str] = Field(default_factory=lambda: ["succeeded"], min_length=1, max_length=16)


class RecipeStep(RecipeModel):
    id: RecipeIdentifier
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=50_000)
    runner: RecipeRunner
    capabilities: list[RecipeCapability] = Field(default_factory=list, max_length=5)
    workspace: RecipeWorkspacePolicy = Field(default_factory=RecipeWorkspacePolicy)
    inputs: dict[RecipeIdentifier, RecipeInput] = Field(default_factory=dict, max_length=64)
    outputs: dict[RecipeIdentifier, RecipeOutput] = Field(default_factory=dict, max_length=64)
    depends_on: list[RecipeDependency] = Field(default_factory=list, max_length=128)
    routes: dict[str, RecipeRoute] = Field(default_factory=dict, max_length=16)
    retry: RecipeRetryPolicy = Field(default_factory=RecipeRetryPolicy)


class RecipeDefinition(RecipeModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    kind: Literal["recipe", "block"] = "recipe"
    parameters: dict[RecipeIdentifier, RecipeParameter] = Field(default_factory=dict, max_length=64)
    entry_steps: list[str] = Field(default_factory=list, max_length=128)
    steps: list[RecipeStep] = Field(default_factory=list, max_length=128)
    outputs: dict[RecipeIdentifier, RecipeExport] = Field(default_factory=dict, max_length=64)


class RecipeValidationIssue(RecipeModel):
    path: str
    code: str
    message: str


class RecipeValidationResult(RecipeModel):
    valid: bool
    issues: list[RecipeValidationIssue]


class RecipePinnedBlock(RecipeModel):
    definition_id: str
    revision_id: str
    revision: int
    checksum: str
    definition: RecipeDefinition


class RecipeDraftResponse(RecipeModel):
    id: str
    version: int
    definition: RecipeDefinition
    created_at: datetime
    updated_at: datetime


class RecipeRevisionResponse(RecipeModel):
    id: str
    definition_id: str
    revision: int
    definition: RecipeDefinition
    blocks: dict[str, RecipePinnedBlock]
    checksum: str
    published_at: datetime


class SaveRecipeDraftRequest(RecipeModel):
    expected_version: int = Field(ge=1)
    definition: RecipeDefinition


class PublishRecipeRequest(RecipeModel):
    expected_version: int = Field(ge=1)


class CopyRecipeRequest(RecipeModel):
    name: str = Field(min_length=1, max_length=200)


class RecipeDraftListResponse(RecipeModel):
    items: list[RecipeDraftResponse]
    next_cursor: int | None
    high_water: int


class RecipeRevisionListResponse(RecipeModel):
    items: list[RecipeRevisionResponse]
    next_cursor: int | None
    high_water: int
