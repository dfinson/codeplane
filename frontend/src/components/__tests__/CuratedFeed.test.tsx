import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../api/client";
import { useStore } from "../../store";
import { normalizeTFEvent } from "../../store/sseNormalize";
import { CuratedFeed } from "../CuratedFeed";

vi.mock("../VoiceButton", () => ({ MicButton: () => null }));

beforeEach(() => {
  useStore.setState({
    jobs: {}, transcript: {}, approvals: {}, batchApprovals: {}, plans: {},
    streamingMessages: {}, streamingReasoning: {}, streamingToolOutput: {},
    secondarySessions: {}, contextHandoffs: {},
  });
});
afterEach(() => vi.restoreAllMocks());

it.each(["before", "after"])("keeps authoritative echoes arriving %s the send receipt", async (order) => {
  let accept!: () => void;
  vi.spyOn(api, "sendOperatorMessage").mockImplementation(() => new Promise((resolve) => {
    accept = () => resolve({ seq: 0, timestamp: "2026-01-01T00:00:00Z" });
  }));
  render(<MemoryRouter><CuratedFeed jobId="job" jobState="running" interactive /></MemoryRouter>);
  const input = screen.getByPlaceholderText("Message the agent\u2026");
  fireEvent.change(input, { target: { value: "Same text" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(useStore.getState().transcript.job).toHaveLength(1);
  const emit = () => useStore.getState().dispatchSSEEvent("message.user", normalizeTFEvent({
    id: "authoritative", kind: "message.user", session_id: "job", payload: { content: "Same text" },
  }));
  if (order === "before") act(emit);
  await act(async () => accept());
  if (order === "after") act(emit);
  await waitFor(() => expect(useStore.getState().transcript.job).toHaveLength(1));
  expect(useStore.getState().transcript.job?.[0]?.eventId).toBe("authoritative");
});
