/**
 * Transcript, tool group summary, and log line SSE event handlers.
 */

import type { LogLine, SecondarySession, SecondarySessionEntry, TranscriptEntry } from "../types";
import type { SSEHandler, AppState } from "./types";
import { isToolOutcome, removePairedToolStarts, sameTranscriptEntry, toolOutputKey } from "../../lib/transcriptIdentity";

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

export function handleLogLine(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const entry: LogLine = {
    jobId,
    seq: payload.seq as number,
    timestamp: payload.timestamp as string,
    level: payload.level as string,
    message: payload.message as string,
    context: (payload.context as Record<string, unknown> | null) ?? null,
  };
  const existing = state.logs[jobId] ?? [];
  const updated = [...existing, entry];
  return {
    logs: { ...state.logs, [jobId]: updated.length > 10_000 ? updated.slice(-10_000) : updated },
  };
}

export function handleTranscriptUpdate(state: AppState, payload: Record<string, unknown>, _getFresh: () => AppState, eventType: string): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const kind = (payload.kind as string | undefined) ?? eventType;

  // message.delta: accumulate streaming text per turn, don't add to transcript
  if (kind === "message.delta") {
    const turnId = (payload.turnId as string | undefined) ?? "__default__";
    const key = `${jobId}:${turnId}`;
    const delta = (payload.content as string) ?? "";
    return {
      streamingMessages: {
        ...state.streamingMessages,
        [key]: (state.streamingMessages[key] ?? "") + delta,
      },
    };
  }

  // tool.result.chunk: accumulate streaming tool output, don't add to transcript
  if (kind === "tool.result.chunk") {
    const key = toolOutputKey({
      jobId, toolCallId: payload.toolCallId as string | undefined, toolName: payload.toolName as string | undefined,
      turnId: payload.turnId as string | undefined, metadata: payload.metadata as TranscriptEntry["metadata"],
    });
    const chunk = (payload.content as string) ?? "";
    return {
      streamingToolOutput: {
        ...state.streamingToolOutput,
        [key]: (state.streamingToolOutput[key] ?? "") + chunk,
      },
    };
  }

  // llm.reasoning.chunk (streaming partial): accumulate live reasoning per turn, don't add to transcript.
  // Complete (non-partial) reasoning blocks fall through and are added to the transcript below.
  if (kind === "llm.reasoning.chunk" && payload.partial === true) {
    const turnId = (payload.turnId as string | undefined) ?? "__default__";
    const key = `${jobId}:${turnId}`;
    const delta = (payload.content as string) ?? "";
    return {
      streamingReasoning: {
        ...state.streamingReasoning,
        [key]: (state.streamingReasoning[key] ?? "") + delta,
      },
    };
  }

  const entry: TranscriptEntry = {
    jobId,
    eventId: payload.eventId as string | undefined,
    sequence: payload.sequence as number | undefined,
    timestamp: payload.timestamp as string,
    kind,
    metadata: payload.metadata as TranscriptEntry["metadata"],
    pairingStatus: payload.pairingStatus as TranscriptEntry["pairingStatus"],
    pairingReason: payload.pairingReason as TranscriptEntry["pairingReason"],
    content: payload.content as string,
    admissionStatus: payload.admissionStatus as TranscriptEntry["admissionStatus"],
    executionStatus: payload.executionStatus as TranscriptEntry["executionStatus"],
    operatorContent: payload.operatorContent as TranscriptEntry["operatorContent"],
    title: payload.title as string | undefined,
    turnId: payload.turnId as string | undefined,
    toolCallId: payload.toolCallId as string | undefined,
    toolName: payload.toolName as string | undefined,
    arguments: payload.arguments as string | undefined,
    result: payload.result as string | undefined,
    success: kind === "tool.call.failed" ? false : payload.success as boolean | undefined,
    toolIssue: payload.toolIssue as string | undefined,
    toolIntent: payload.toolIntent as string | undefined,
    toolTitle: payload.toolTitle as string | undefined,
    toolDisplay: payload.toolDisplay as string | undefined,
    toolDisplayFull: payload.toolDisplayFull as string | undefined,
    toolDurationMs: payload.toolDurationMs as number | undefined,
    toolVisibility: payload.toolVisibility as string | undefined,
  };
  const existing = state.transcript[jobId] ?? [];

  // Local optimistic entries are settled by their sender, never by text matching.
  if (existing.some((e) => sameTranscriptEntry(e, entry))) {
    return null;
  }
  const updated = isToolOutcome(entry.kind) ? removePairedToolStarts([...existing, entry]) : [...existing, entry];

  // When a complete agent message arrives, clear streaming state for that turn.
  let streamingMessages = state.streamingMessages;
  if (entry.kind === "message.assistant") {
    const key = entry.turnId ? `${jobId}:${entry.turnId}` : `${jobId}:__default__`;
    if (key in streamingMessages) {
      streamingMessages = { ...streamingMessages };
      delete streamingMessages[key];
    }
  }

  // When a tool.call.completed arrives, clear streaming tool output.
  let streamingToolOutput = state.streamingToolOutput;
  if (isToolOutcome(entry.kind)) {
    const keys = entry.toolCallId ? [toolOutputKey(entry)] : [];
    if (keys.length > 0) {
      streamingToolOutput = { ...streamingToolOutput };
      for (const k of keys) delete streamingToolOutput[k];
    }
  }

  // When a complete reasoning block arrives, clear streaming reasoning for that turn.
  let streamingReasoning = state.streamingReasoning;
  if (entry.kind === "llm.reasoning.chunk") {
    const key = entry.turnId ? `${jobId}:${entry.turnId}` : `${jobId}:__default__`;
    if (key in streamingReasoning) {
      streamingReasoning = { ...streamingReasoning };
      delete streamingReasoning[key];
    }
  }

  // Clear setupStep once real transcript content arrives — setup is done.
  let jobs = state.jobs;
  const job = jobs[jobId];
  if (job?.setupStep && entry.kind !== "message.user") {
    jobs = { ...jobs, [jobId]: { ...job, setupStep: null } };
  }

  return {
    ...(jobs !== state.jobs ? { jobs } : {}),
    transcript: { ...state.transcript, [jobId]: updated.length > 10_000 ? updated.slice(-10_000) : updated },
    streamingMessages,
    streamingToolOutput,
    streamingReasoning,
  };
}

export function handleSidecarTranscript(): Partial<AppState> | null {
  // Legacy — no longer emitted. Kept as no-op for backward compat with old SSE replays.
  return null;
}

export function handleSecondarySessionStarted(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const sessionId = payload.sessionId as string;
  if (!jobId || !sessionId) return null;
  const jobSessions = state.secondarySessions[jobId] ?? {};
  const existing = jobSessions[sessionId];
  // Merge with placeholder if entries arrived before started (out-of-order)
  const session: SecondarySession = {
    id: sessionId,
    jobId,
    kind: (payload.kind as SecondarySession["kind"]) ?? "sidecar",
    name: (payload.name as string) ?? "",
    icon: (payload.icon as string) ?? "bot",
    status: "running",
    startedAt: new Date().toISOString(),
    entries: existing?.entries ?? [],
  };
  return {
    secondarySessions: {
      ...state.secondarySessions,
      [jobId]: { ...jobSessions, [sessionId]: session },
    },
  };
}

export function handleSecondarySessionEntry(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const sessionId = payload.sessionId as string;
  if (!jobId || !sessionId) return null;
  const entryData = payload.entry as Record<string, unknown> | undefined;
  if (!entryData) return null;
  const entry: SecondarySessionEntry = {
    seq: (entryData.seq as number) ?? 0,
    kind: (entryData.kind as SecondarySessionEntry["kind"]) ?? "output",
    content: (entryData.content as string) ?? "",
    toolName: (entryData.toolName as string | null) ?? null,
    toolArgs: (entryData.toolArgs as string | null) ?? null,
    durationMs: (entryData.durationMs as number | null) ?? null,
    toolResult: (entryData.toolResult as string | null) ?? null,
    toolDisplay: (entryData.toolDisplay as string | null) ?? null,
    toolDisplayFull: (entryData.toolDisplayFull as string | null) ?? null,
    toolSuccess: (entryData.toolSuccess as boolean | null) ?? null,
    toolIssue: (entryData.toolIssue as string | null) ?? null,
    toolVisibility: (entryData.toolVisibility as string | null) ?? null,
  };
  const jobSessions = state.secondarySessions[jobId] ?? {};
  const existing = jobSessions[sessionId];
  if (!existing) {
    // Entry arrived before started (out-of-order SSE). Create a placeholder
    // session so entries aren't lost — started event will fill in metadata.
    const placeholder: SecondarySession = {
      id: sessionId,
      jobId,
      kind: "sidecar",
      name: "…",
      icon: "bot",
      status: "running",
      startedAt: new Date().toISOString(),
      entries: [entry],
    };
    return {
      secondarySessions: {
        ...state.secondarySessions,
        [jobId]: { ...jobSessions, [sessionId]: placeholder },
      },
    };
  }
  return {
    secondarySessions: {
      ...state.secondarySessions,
      [jobId]: {
        ...jobSessions,
        [sessionId]: { ...existing, entries: [...existing.entries, entry] },
      },
    },
  };
}

export function handleSecondarySessionCompleted(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const sessionId = payload.sessionId as string;
  if (!jobId || !sessionId) return null;
  const jobSessions = state.secondarySessions[jobId] ?? {};
  const existing = jobSessions[sessionId];
  // If completed arrives before started (out-of-order), create session from available data
  const base: SecondarySession = existing ?? {
    id: sessionId,
    jobId,
    kind: "sidecar",
    name: "…",
    icon: "bot",
    status: "running",
    startedAt: new Date().toISOString(),
    entries: [],
  };
  return {
    secondarySessions: {
      ...state.secondarySessions,
      [jobId]: {
        ...jobSessions,
        [sessionId]: {
          ...base,
          status: (payload.status as SecondarySession["status"]) ?? "completed",
          completedAt: new Date().toISOString(),
          output: (payload.output as string | null) ?? null,
          inputTokens: (payload.inputTokens as number) ?? 0,
          outputTokens: (payload.outputTokens as number) ?? 0,
          costUsd: (payload.costUsd as number) ?? 0,
        },
      },
    },
  };
}

export function handleToolGroupSummary(state: AppState, payload: Record<string, unknown>): Partial<AppState> | null {
  const jobId = payload.jobId as string;
  const turnId = payload.turnId as string;
  const summary = payload.summary as string;
  const entries = state.transcript[jobId];
  if (!entries) return null;
  let changed = false;
  const patched = entries.map((e) => {
    if (e.kind === "tool.call.completed" && e.turnId === turnId && e.toolGroupSummary !== summary) {
      changed = true;
      return { ...e, toolGroupSummary: summary };
    }
    return e;
  });
  if (!changed) return null;
  return { transcript: { ...state.transcript, [jobId]: patched } };
}

export const transcriptHandlers: Record<string, SSEHandler> = {
  log: handleLogLine,
  // All transcript kinds route to the same handler, which branches on dotted kind.
  "message.user": handleTranscriptUpdate,
  "message.assistant": handleTranscriptUpdate,
  "message.system": handleTranscriptUpdate,
  "message.delta": handleTranscriptUpdate,
  "llm.reasoning.chunk": handleTranscriptUpdate,
  "tool.call.started": handleTranscriptUpdate,
  "tool.call.completed": handleTranscriptUpdate,
  "tool.call.failed": handleTranscriptUpdate,
  "tool.result.chunk": handleTranscriptUpdate,
  "sidecar.transcript": handleTranscriptUpdate,
  "sidecar.agent_message": handleTranscriptUpdate,
  "tool.group_summary": handleToolGroupSummary,
  "secondary_session.started": handleSecondarySessionStarted,
  "secondary_session.entry": handleSecondarySessionEntry,
  "secondary_session.completed": handleSecondarySessionCompleted,
};
