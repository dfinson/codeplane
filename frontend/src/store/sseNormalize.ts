/**
 * TraceForge SSE ingestion adapter.
 *
 * The backend serializes each `traceforge.SessionEvent` to the SSE wire as-is:
 *   - the SSE `event:` line is the dotted kind (e.g. "job.state_changed",
 *     "tool.call.completed", "permission.requested");
 *   - the SSE `data:` line is the full event envelope
 *     `{ id, kind, session_id, timestamp, payload, metadata }` where `payload`
 *     is the raw control-plane payload in **snake_case**.
 *
 * This module adapts that wire shape into the store's internal camelCase
 * domain model at the ingestion boundary (used by `useSSE`), so the store
 * handlers keep reading the camelCase fields they always have. It:
 *   1. deep-converts payload keys snake_case -> camelCase (`normalizeTFEvent`);
 *   2. injects the envelope-derived fields handlers expect (`jobId` from
 *      `session_id`, `timestamp`, `turnId`/tool enrichment from `metadata`);
 *   3. re-derives the job-state transitions the backend previously emitted as a
 *      secondary `job_state_changed` frame — those are now computed on the FE
 *      from the dotted kind (`deriveJobStateFrame`).
 *
 * There is no reverse (camel -> snake) path and no old-wire vocabulary: the
 * wire speaks TraceForge; this adapter is purely the FE's own domain mapping.
 */

/** Minimal shape of a serialized `traceforge.SessionEvent` on the SSE wire. */
export interface TFSessionEvent {
  id?: string;
  kind: string;
  session_id: string;
  timestamp?: string;
  payload?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
}

const SNAKE_SEGMENT = /_([a-z0-9])/g;

function snakeKeyToCamel(key: string): string {
  return key.replace(SNAKE_SEGMENT, (_m, c: string) => c.toUpperCase());
}

/**
 * Recursively convert object keys from snake_case to camelCase.
 *
 * - Only keys are transformed; values are left untouched.
 * - Recurses through nested objects and arrays (transcript entries, approval
 *   actions, plan steps, diff hunks, ...).
 * - Idempotent: already-camel and single-word keys are unchanged.
 */
export function deepSnakeToCamel(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(deepSnakeToCamel);
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[snakeKeyToCamel(k)] = deepSnakeToCamel(v);
    }
    return out;
  }
  return value;
}

/**
 * Unwrap a TF event envelope into the camelCase payload the store handlers
 * consume, injecting `jobId`/`timestamp`/`turnId` from the envelope.
 */
export function normalizeTFEvent(ev: TFSessionEvent): Record<string, unknown> {
  const payload = deepSnakeToCamel(ev.payload ?? {}) as Record<string, unknown>;

  // `jobId` is authoritative from the envelope session id.
  payload.jobId = ev.session_id;
  if (ev.id !== undefined) {
    payload.eventId = ev.id;
  }

  if (payload.timestamp === undefined && ev.timestamp !== undefined) {
    payload.timestamp = ev.timestamp;
  }

  // The event kind is the transcript discriminator in the TF shape; carry it
  // into the FE payload view unless REST-style payload data already provided it.
  if (payload.kind === undefined || ev.kind === "tool.call.failed") {
    payload.kind = ev.kind;
  }

  // Metadata enrichment lives on EventMetadata; canonical sequence never comes
  // from payload data.
  const meta = ev.metadata;
  const metadata = meta == null ? undefined : (meta as Record<string, unknown>);
  payload.metadata = metadata;
  const pairing = ev.payload?.["_codeplane_pairing"];
  if (pairing && typeof pairing === "object") {
    const state = pairing as Record<string, unknown>;
    if (state.status === "unresolved") payload.pairingStatus = state.status;
    if (typeof state.reason === "string") payload.pairingReason = state.reason;
  }
  if (ev.kind === "tool.call.failed") {
    payload.success = false;
    const issue = payload.toolIssue ?? payload.error ?? payload.message;
    if (issue != null) payload.toolIssue = typeof issue === "string" ? issue : JSON.stringify(issue);
  }
  if (payload.arguments != null && typeof payload.arguments !== "string") {
    payload.arguments = JSON.stringify(payload.arguments);
  }
  if (payload.result != null && typeof payload.result !== "string") {
    payload.result = JSON.stringify(payload.result);
  }
  for (const [source, target] of [
    ["recipe_run_id", "recipeRunId"],
    ["recipe_revision", "recipeRevision"],
    ["step_instance_id", "stepInstanceId"],
    ["attempt_id", "attemptId"],
    ["execution_epoch", "executionEpoch"],
    ["execution_token", "executionToken"],
  ] as const) {
    if (metadata?.[source] != null) payload[target] = metadata[source];
  }
  delete payload.sequence;
  if (typeof metadata?.sequence === "number") {
    payload.sequence = metadata.sequence;
  }
  const metaTurnId = metadata?.turn_id;
  if (payload.turnId === undefined && metaTurnId != null) {
    payload.turnId = metaTurnId;
  }
  if (payload.toolDisplay === undefined && metadata?.tool_display != null) {
    payload.toolDisplay = metadata.tool_display;
  }
  if (payload.toolDurationMs === undefined && metadata?.duration_ms != null) {
    payload.toolDurationMs = metadata.duration_ms;
  }
  const motivation = metadata?.motivation as Record<string, unknown> | undefined;
  if (payload.toolIntent === undefined && motivation?.intent != null) {
    payload.toolIntent = motivation.intent;
  }
  // `partial` distinguishes a streaming delta from a complete block for kinds
  // that share one dotted kind (e.g. llm.reasoning.chunk); carry it through.
  if (payload.partial === undefined && metadata?.partial != null) {
    payload.partial = metadata.partial;
  }

  return payload;
}

/** Dotted kinds that map directly to a single implied job state. */
const JOB_STATE_BY_KIND: Record<string, string> = {
  "job.created": "running",
  "job.canceled": "canceled",
  "job.review": "review",
  "job.completed": "completed",
  "job.failed": "failed",
};

/**
 * Re-derive the job-state transition the backend used to synthesize as a
 * secondary `job_state_changed` frame after certain primary events.
 *
 * Returns a `handleJobStateChanged`-shaped payload (`{ jobId, newState,
 * timestamp }`) or `null` when a kind implies no state change. `permission.*`
 * kinds derive their state from the resolution; batch permission kinds derive
 * nothing (they never drove a job-state frame).
 */
export function deriveJobStateFrame(
  kind: string,
  payload: Record<string, unknown>,
): Record<string, unknown> | null {
  if (typeof payload.attemptId === "string") return null;
  const base = { jobId: payload.jobId, timestamp: payload.timestamp };

  if (kind === "permission.requested") {
    return { ...base, newState: "waiting_for_approval" };
  }
  if (kind === "permission.resolved") {
    return { ...base, newState: payload.resolution === "approved" ? "running" : "failed" };
  }

  const mapped = JOB_STATE_BY_KIND[kind];
  if (mapped) {
    return { ...base, newState: mapped };
  }
  return null;
}
