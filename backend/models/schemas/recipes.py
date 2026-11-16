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
    source_input: RecipeIdentifier | None = None


class RecipeAgentRunner(RecipeModel):
    kind: Literal["agent"] = "agent"
    provider: str | None = None
    model: str | None = None
    max_turns: int = Field(default=1, ge=1, le=100)
    session: Literal["new", "reuse"] = "new"


class RecipeCommandArgument(RecipeModel):
    input: RecipeIdentifier
    field: Literal["workspace", "commit"] | None = None


class RecipeCommandRunner(RecipeModel):
    kind: Literal["command"] = "command"
    argv: list[str | RecipeCommandArgument] = Field(default_factory=list, max_length=256)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    reconciles: list[str] = Field(default_factory=list, max_length=128)


class RecipeHumanRunner(RecipeModel):
    kind: Literal["human"] = "human"
    proposed_action: str = ""
    decisions: list[Literal["approved", "rejected"]] = Field(default=["approved", "rejected"], max_length=2)


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


class RecipeSelection(RecipeModel):
    revision_id: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=64)


class ResolveDefaultRecipeRequest(RecipeModel):
    plan_first: bool = False


class RecipeInputReceipt(RecipeModel):
    type: RecipeValueType
    present: bool
    value: JsonValue = None
    receipt_id: str | None = None
    producing_attempt_id: str | None = None
    producer_outcome: str | None = None
    checksum: str | None = None
    degraded_reason: str | None = None
    scope_attempt_id: str | None = None
    parameter_name: str | None = None


class RecipeOutputReceipt(RecipeModel):
    id: str
    attempt_id: str
    name: str
    type: RecipeValueType
    value: JsonValue
    checksum: str
    created_at: datetime


class RecipeAttemptExecution(RecipeModel):
    execution_token: str
    session_id: str
    provider: str
    model: str | None
    started_at: datetime


class RecipeArtifactReceipt(RecipeModel):
    artifact_id: str
    checksum: str
    size_bytes: int
    complete: bool = True


class RecipeCommandResult(RecipeModel):
    kind: Literal["command"] = "command"
    argv: list[str]
    exit_status: int | None
    stdout: str | None
    stderr: str | None
    workspace: str
    start_code_ref: str
    end_code_ref: str | None
    uncertain: bool = False
    started: bool | None = True
    stdout_artifact: RecipeArtifactReceipt | None = None
    stderr_artifact: RecipeArtifactReceipt | None = None
    stdout_preview: str | None = None
    stderr_preview: str | None = None


class RecipeAgentResult(RecipeModel):
    kind: Literal["agent"] = "agent"
    session_ids: list[str]
    text: str | None
    uncertain: bool = False
    actual_models: list[str] = Field(default_factory=list, max_length=1000)


class RecipeHumanResult(RecipeModel):
    kind: Literal["human"] = "human"
    approval_id: str | None
    decision: str | None
    input_checksum: str


class RecipeGateProposal(RecipeModel):
    kind: Literal["recipe_gate"] = "recipe_gate"
    recipe_run_id: str
    step_instance_id: str
    attempt_id: str
    input_checksum: str
    proposed_action: str
    next_steps: dict[str, list[str]]


class RecipeGateBinding(RecipeModel):
    attempt_id: str
    approval_id: str
    input_checksum: str
    proposed_action: str
    next_steps: dict[str, list[str]]
    created_at: datetime


class RecipeDecisionReceipt(RecipeModel):
    kind: Literal["recipe_decision"] = "recipe_decision"
    attempt_id: str
    input_checksum: str
    outputs: dict[str, JsonValue] = Field(default_factory=dict, max_length=64)
    notes: str | None = Field(default=None, max_length=10_000)


class RecipeBlockResult(RecipeModel):
    kind: Literal["block"] = "block"
    revision_id: str
    child_attempt_ids: list[str]


RecipeEvidence = Annotated[
    RecipeCommandResult | RecipeAgentResult | RecipeHumanResult | RecipeBlockResult, Field(discriminator="kind")
]


class RecipeAttemptResult(RecipeModel):
    outcome: str
    evidence: RecipeEvidence
    reason: str | None
    completed_at: datetime


class RecipeResolvedWorkspace(RecipeModel):
    path: str
    code_ref: str
    policy: RecipeWorkspacePolicy


class RecipeCodeReference(RecipeModel):
    workspace: str = Field(min_length=1)
    commit: str = Field(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")


class RecipeAttemptResponse(RecipeModel):
    id: str
    step_instance_id: str
    number: int
    inputs: dict[str, RecipeInputReceipt]
    runner: RecipeRunner
    capabilities: list[RecipeCapability]
    workspace: RecipeResolvedWorkspace
    dispatch_intent_at: datetime
    result: RecipeAttemptResult | None
    executions: list[RecipeAttemptExecution]
    outputs: list[RecipeOutputReceipt]


class RecipeStepInstanceResponse(RecipeModel):
    id: str
    run_id: str
    step_path: str
    definition: RecipeStep
    state: RecipeState
    reason: str | None
    current_attempt_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class RecipeRunResponse(RecipeModel):
    id: str
    job_id: str
    run_number: int
    start_request_key: str
    continuation_of_id: str | None
    instruction: str | None
    code_ref: RecipeCodeReference | None
    reason: str | None
    revision_id: str
    snapshot: RecipeRevisionResponse
    snapshot_checksum: str
    inputs: dict[str, JsonValue]
    state: RecipeState
    version: int
    created_at: datetime
    updated_at: datetime
    cancel_requested_at: datetime | None
    completed_at: datetime | None
    resolved_at: datetime | None


class RecipeRunListResponse(RecipeModel):
    items: list[RecipeRunResponse]
    next_cursor: int | None
    high_water: int


class RecipeCurrentRunResponse(RecipeModel):
    run: RecipeRunResponse | None


class RecipeStepListResponse(RecipeModel):
    items: list[RecipeStepInstanceResponse]
    next_cursor: str | None


class RecipeAttemptSummary(RecipeModel):
    id: str
    number: int
    dispatch_intent_at: datetime
    outcome: str | None
    completed_at: datetime | None


class RecipeStepInspection(RecipeModel):
    step: RecipeStepInstanceResponse
    attempts: list[RecipeAttemptSummary]


class RecipeGateInspection(RecipeModel):
    binding: RecipeGateBinding
    resolution: str | None
    notes: str | None
    resolved_at: datetime | None


class RecipeAttemptInspection(RecipeModel):
    attempt: RecipeAttemptResponse
    gate: RecipeGateInspection | None
