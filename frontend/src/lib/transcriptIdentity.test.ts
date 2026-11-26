import { describe, expect, it } from "vitest";
import type { TranscriptEntry } from "../store";
import {
  mergeTranscriptEntries,
  sameTranscriptEntry,
  transcriptReactKey,
  removePairedToolStarts,
} from "./transcriptIdentity";

function entry(overrides: Partial<TranscriptEntry> = {}): TranscriptEntry {
  return {
    jobId: "job-1",
    timestamp: "2026-01-01T00:00:00Z",
    kind: "message.assistant",
    content: "done",
    ...overrides,
  };
}

describe("transcript identity", () => {
  it("never pairs unknown tool identities by repeated name or text", () => {
    const entries = [
      entry({ kind: "tool.call.started", toolName: "write", content: "" }),
      entry({ kind: "tool.call.failed", toolName: "write", content: "" }),
    ];
    expect(removePairedToolStarts(entries)).toEqual(entries);
  });

  it("pairs acquisition channels only within the same binding and native session", () => {
    const start = entry({
      kind: "tool.call.started", toolCallId: "call",
      metadata: {
        integration_binding_id: "binding", run_id: "session", namespace: ["observation-source", "first"],
        partial: false, visibility: "visible",
      },
    });
    const sibling = entry({
      kind: "tool.call.started", toolCallId: "call",
      metadata: { integration_binding_id: "other-binding", run_id: "session", partial: false, visibility: "visible" },
    });
    const failure = entry({
      kind: "tool.call.failed", toolCallId: "call",
      metadata: {
        integration_binding_id: "binding", run_id: "session", namespace: ["observation-source", "second"],
        partial: false, visibility: "visible",
      },
    });
    expect(removePairedToolStarts([start, sibling, failure])).toEqual([sibling, failure]);
  });

  it("deduplicates by TraceForge event id regardless of producer sequence", () => {
    expect(sameTranscriptEntry(
      entry({ eventId: "evt-1", sequence: 10 }),
      entry({ eventId: "evt-1", sequence: 11 }),
    )).toBe(true);
  });

  it("does not collapse distinct events that share a producer sequence", () => {
    const merged = mergeTranscriptEntries(
      [entry({ eventId: "evt-1", sequence: 10, content: "first" })],
      [entry({ eventId: "evt-2", sequence: 10, content: "second" })],
    );
    expect(merged.map((item) => item.eventId)).toEqual(["evt-1", "evt-2"]);
  });

  it("does not invent identity for entries without canonical event ids", () => {
    const merged = mergeTranscriptEntries(
      [entry({ eventId: undefined, sequence: 10 })],
      [entry({ eventId: undefined, sequence: 10 })],
    );
    expect(merged).toHaveLength(2);
  });

  it("replaces a live copy with its persisted event-id match", () => {
    const persisted = entry({ eventId: "evt-1", content: "persisted", sequence: undefined });
    const live = entry({ eventId: "evt-1", content: "live", sequence: 999 });

    expect(mergeTranscriptEntries([persisted], [live])).toEqual([persisted]);
  });

  it("orders only when every event has a comparable producer sequence", () => {
    expect(mergeTranscriptEntries(
      [entry({ eventId: "evt-2", sequence: 2 })],
      [entry({ eventId: "evt-0", sequence: 0 })],
    ).map((item) => item.eventId)).toEqual(["evt-0", "evt-2"]);

    const withMissingSequence = mergeTranscriptEntries(
      [entry({ eventId: "evt-2" })],
      [entry({ eventId: "evt-1", sequence: 1 })],
    );
    expect(withMissingSequence.map((item) => item.eventId)).toEqual(["evt-2", "evt-1"]);
    expect(mergeTranscriptEntries(
      [entry({ eventId: "evt-2" })],
      [entry({ eventId: "evt-1", sequence: 1 })],
    )).toEqual(withMissingSequence);
  });

  it("produces unique React keys for entries without canonical identity", () => {
    const item = entry({ eventId: undefined, sequence: undefined });
    expect(transcriptReactKey(item, 0)).not.toBe(transcriptReactKey(item, 1));
  });
});
