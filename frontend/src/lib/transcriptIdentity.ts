import type { TranscriptEntry } from "../store";

export function isToolOutcome(kind: string): boolean {
  return kind === "tool.call.completed" || kind === "tool.call.failed";
}

export function isFailedTool(entry: TranscriptEntry): boolean {
  return entry.kind === "tool.call.failed" || entry.success === false;
}

export function isRunningTool(entry: TranscriptEntry): boolean {
  return entry.kind === "tool.call.started" && entry.pairingStatus !== "unresolved";
}

type ToolIdentity = Pick<TranscriptEntry, "jobId" | "metadata" | "toolCallId" | "toolName" | "turnId">;

export function toolCallIdentity(entry: ToolIdentity): string | null {
  if (!entry.toolCallId?.trim()) return null;
  const meta = entry.metadata;
  const scoped = meta?.run_id || meta?.integration_binding_id || meta?.attempt_id || meta?.execution_epoch;
  const namespace = meta?.namespace?.[0] === "observation-source" ? meta.namespace.slice(2) : meta?.namespace ?? [];
  return JSON.stringify([
    entry.jobId, meta?.integration_binding_id ?? null, meta?.run_id ?? null,
    meta?.execution_epoch ?? null, meta?.attempt_id ?? null, namespace,
    scoped ? null : entry.turnId ?? null, entry.toolCallId,
  ]);
}

export function toolOutputKey(entry: ToolIdentity): string {
  const meta = entry.metadata;
  const scoped = meta?.run_id || meta?.integration_binding_id || meta?.attempt_id || meta?.execution_epoch;
  return `${entry.jobId}:${scoped && entry.toolCallId
    ? toolCallIdentity(entry) : entry.toolCallId ?? entry.toolName ?? "__tool__"}`;
}

export function removePairedToolStarts(entries: TranscriptEntry[]): TranscriptEntry[] {
  const terminalIds = new Set(entries.filter((entry) => isToolOutcome(entry.kind)).map(toolCallIdentity));
  terminalIds.delete(null);
  return entries.filter((entry) => entry.kind !== "tool.call.started" || !terminalIds.has(toolCallIdentity(entry)));
}

function hasComparableSequence(entry: TranscriptEntry): entry is TranscriptEntry & { sequence: number } {
  return Number.isSafeInteger(entry.sequence);
}

export function transcriptIdentity(entry: TranscriptEntry): string | null {
  if (entry.eventId) return `event:${entry.eventId}`;
  return null;
}

export function sameTranscriptEntry(left: TranscriptEntry, right: TranscriptEntry): boolean {
  return Boolean(left.eventId && right.eventId && left.eventId === right.eventId);
}

export function mergeTranscriptEntries(
  historical: TranscriptEntry[],
  live: TranscriptEntry[],
): TranscriptEntry[] {
  const merged = [...historical];
  for (const entry of live) {
    if (!merged.some((candidate) => sameTranscriptEntry(candidate, entry))) {
      merged.push(entry);
    }
  }
  if (merged.every(hasComparableSequence)) {
    return [...merged].sort((left, right) => left.sequence! - right.sequence!);
  }
  return merged;
}

export function transcriptReactKey(entry: TranscriptEntry, index: number): string {
  return transcriptIdentity(entry) ?? `transcript:${index}`;
}
