import { describe, it, expect } from "vitest";
import { deepSnakeToCamel, normalizeTFEvent, deriveJobStateFrame, type TFSessionEvent } from "./sseNormalize";
import { handleApprovalRequested, handleBatchApprovalRequested } from "./handlers/approvalHandlers";
import { useStore } from "./index";

it("shows a batched permission once and retains an unavailable risk score", () => {
  const state = useStore.getState();
  const approval = normalizeTFEvent({
    id: "request", kind: "permission.requested", session_id: "job",
    payload: { approval_id: "approval", batch_managed: true }, metadata: null,
  });
  expect(handleApprovalRequested(state, approval)).toBeNull();
  const batch = normalizeTFEvent({
    id: "batch-request", kind: "permission.batch.requested", session_id: "job", metadata: null,
    payload: { batch_id: "batch", actions: [{
      id: "action", kind: "file", recommendedAction: "escalate", reasonCode: "cp_usd_ceiling",
      reason: "cp_usd_ceiling", riskScore: null, riskBand: "unknown", description: "view: source.py",
    }] },
  });
  expect(handleBatchApprovalRequested(state, batch)?.batchApprovals?.batch?.actions[0]?.riskScore).toBeNull();
});

it("preserves authoritative attempt model scope from canonical metadata", () => {
  const result = normalizeTFEvent({
    id: "event", kind: "model.downgraded", session_id: "job",
    payload: { actual_model: "actual", attempt_id: "untrusted-payload" },
    metadata: { attempt_id: "attempt", execution_epoch: "epoch", recipe_run_id: "run" },
  });
  expect(result).toMatchObject({ actualModel: "actual", attemptId: "attempt", executionEpoch: "epoch", recipeRunId: "run" });
});

describe("deepSnakeToCamel", () => {
  it("converts top-level snake keys to camelCase", () => {
    expect(deepSnakeToCamel({ tool_name: "x", raw_size: 3 })).toEqual({ toolName: "x", rawSize: 3 });
  });

  it("handles multi-underscore keys", () => {
    expect(deepSnakeToCamel({ tool_duration_ms: 5, source_session_id: "s" })).toEqual({
      toolDurationMs: 5,
      sourceSessionId: "s",
    });
  });

  it("recurses into nested objects and arrays", () => {
    const input = {
      changed_files: [{ file_path: "a.ts", raw_size: 1 }],
      entry: { tool_name: "grep", nested_obj: { deep_key: true } },
    };
    expect(deepSnakeToCamel(input)).toEqual({
      changedFiles: [{ filePath: "a.ts", rawSize: 1 }],
      entry: { toolName: "grep", nestedObj: { deepKey: true } },
    });
  });

  it("leaves already-camel and single-word keys unchanged", () => {
    expect(deepSnakeToCamel({ kind: "message.assistant", jobId: "j1", content: "hi" })).toEqual({
      kind: "message.assistant",
      jobId: "j1",
      content: "hi",
    });
  });

  it("never mutates string values (discriminators survive)", () => {
    expect(deepSnakeToCamel({ kind: "message.delta", warning_type: "drift" })).toEqual({
      kind: "message.delta",
      warningType: "drift",
    });
  });

  it("is idempotent", () => {
    const once = deepSnakeToCamel({ tool_name: "x", nested: { a_b: 1 } });
    expect(deepSnakeToCamel(once)).toEqual(once);
  });
});

describe("normalizeTFEvent", () => {
  function ev(overrides: Partial<TFSessionEvent> = {}): TFSessionEvent {
    return {
      id: "evt-1",
      kind: "tool.call.completed",
      session_id: "job-1",
      timestamp: "2025-01-01T00:00:00Z",
      payload: {},
      metadata: null,
      ...overrides,
    };
  }

  it("keeps an admission receipt distinct from observed operator input", () => {
    const result = normalizeTFEvent(ev({
      kind: "message.user",
      payload: {
        content: "Coordinated operator admission receipt",
        operator_content: "Instruction", admission_status: "accepted", execution_status: "unconfirmed",
      },
      metadata: { execution_token: "exact-target", run_id: "actual-session" },
    }));
    expect(result).toMatchObject({
      admissionStatus: "accepted", executionStatus: "unconfirmed", operatorContent: "Instruction",
      metadata: { execution_token: "exact-target", run_id: "actual-session" },
    });
    expect(result.turnId).toBeUndefined();
    expect(deriveJobStateFrame("message.user", result)).toBeNull();
  });

  it("injects jobId from the envelope session_id", () => {
    const out = normalizeTFEvent(ev({ payload: { tool_name: "grep" } }));
    expect(out.jobId).toBe("job-1");
    expect(out.toolName).toBe("grep");
  });

  it("injects the envelope kind as the transcript discriminator", () => {
    const out = normalizeTFEvent(ev({ kind: "message.assistant", payload: { content: "hi" } }));
    expect(out.kind).toBe("message.assistant");
  });

  it("falls back to the envelope timestamp when payload lacks one", () => {
    const out = normalizeTFEvent(ev({ payload: {} }));
    expect(out.timestamp).toBe("2025-01-01T00:00:00Z");
  });

  it("prefers the payload timestamp when present", () => {
    const out = normalizeTFEvent(ev({ payload: { timestamp: "2030-02-02T00:00:00Z" } }));
    expect(out.timestamp).toBe("2030-02-02T00:00:00Z");
  });

  it("carries turn_id from metadata as camelCase turnId", () => {
    const out = normalizeTFEvent(ev({ metadata: { turn_id: "turn-9" } }));
    expect(out.turnId).toBe("turn-9");
  });

  it("carries canonical event identity from the envelope and metadata", () => {
    const out = normalizeTFEvent(ev({
      id: "evt-9",
      payload: { sequence: 9000 },
      metadata: { sequence: 42 },
    }));
    expect(out.eventId).toBe("evt-9");
    expect(out.sequence).toBe(42);
    expect(out.seq).toBeUndefined();
  });

  it("does not use payload sequence as producer-stream order", () => {
    const out = normalizeTFEvent(ev({ payload: { sequence: 9000 }, metadata: {} }));
    expect(out.sequence).toBeUndefined();
  });

  it("prefers a payload turnId over metadata", () => {
    const out = normalizeTFEvent(ev({ payload: { turn_id: "payload-turn" }, metadata: { turn_id: "meta-turn" } }));
    expect(out.turnId).toBe("payload-turn");
  });

  it("lifts TF metadata tool enrichment into the payload view", () => {
    const out = normalizeTFEvent(ev({
      payload: {},
      metadata: {
        tool_display: "Read src/main.ts",
        duration_ms: 123,
        motivation: { intent: "Inspect file", reasoning: "need context" },
      },
    }));
    expect(out.toolDisplay).toBe("Read src/main.ts");
    expect(out.toolDurationMs).toBe(123);
    expect(out.toolIntent).toBe("Inspect file");
  });

  it("handles a null/absent payload", () => {
    const out = normalizeTFEvent(ev({ payload: null }));
    expect(out.jobId).toBe("job-1");
  });
});

describe("deriveJobStateFrame", () => {
  const p = { jobId: "job-1", timestamp: "2025-01-01T00:00:00Z" };

  it("maps lifecycle kinds to their implied state", () => {
    expect(deriveJobStateFrame("job.created", p)).toMatchObject({ jobId: "job-1", newState: "running" });
    expect(deriveJobStateFrame("job.canceled", p)).toMatchObject({ newState: "canceled" });
    expect(deriveJobStateFrame("job.review", p)).toMatchObject({ newState: "review" });
    expect(deriveJobStateFrame("job.completed", p)).toMatchObject({ newState: "completed" });
    expect(deriveJobStateFrame("job.failed", p)).toMatchObject({ newState: "failed" });
  });

  it("maps permission.requested to waiting_for_approval", () => {
    expect(deriveJobStateFrame("permission.requested", p)).toMatchObject({ newState: "waiting_for_approval" });
  });

  it("derives permission.resolved state from the resolution", () => {
    expect(deriveJobStateFrame("permission.resolved", { ...p, resolution: "approved" })).toMatchObject({
      newState: "running",
    });
    expect(deriveJobStateFrame("permission.resolved", { ...p, resolution: "denied" })).toMatchObject({
      newState: "failed",
    });
  });

  it("returns null for kinds that imply no job-state transition", () => {
    expect(deriveJobStateFrame("job.state_changed", p)).toBeNull();
    expect(deriveJobStateFrame("tool.call.completed", p)).toBeNull();
    expect(deriveJobStateFrame("permission.batch.requested", p)).toBeNull();
    expect(deriveJobStateFrame("message.assistant", p)).toBeNull();
  });

  it("carries jobId and timestamp through", () => {
    expect(deriveJobStateFrame("job.review", p)).toEqual({
      jobId: "job-1",
      timestamp: "2025-01-01T00:00:00Z",
      newState: "review",
    });
  });
});
