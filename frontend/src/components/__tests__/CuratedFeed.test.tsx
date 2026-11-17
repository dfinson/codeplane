import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import * as api from "../../api/client";
import { useStore } from "../../store";
import { normalizeTFEvent } from "../../store/sseNormalize";
import { CuratedFeed } from "../CuratedFeed";

vi.mock("../VoiceButton", () => ({ MicButton: () => null }));
const navigate = vi.hoisted(() => vi.fn());
vi.mock("react-router-dom", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-router-dom")>(),
  useNavigate: () => navigate,
}));

function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

function feed(jobId: string, jobState = "running") {
  return <MemoryRouter><CuratedFeed jobId={jobId} jobState={jobState} interactive /></MemoryRouter>;
}

beforeEach(() => {
  navigate.mockReset();
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(toast, "error").mockImplementation(() => 0);
  vi.spyOn(toast, "success").mockImplementation(() => 0);
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

it.each(["draft", "sending"])("does not apply a prior Job failure to the current Job's %s", async (state) => {
  const first = pending<Awaited<ReturnType<typeof api.sendOperatorMessage>>>();
  const second = pending<Awaited<ReturnType<typeof api.sendOperatorMessage>>>();
  vi.spyOn(api, "sendOperatorMessage").mockImplementation((jobId) => jobId === "a" ? first.promise : second.promise);
  const view = render(feed("a"));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "A message" } });
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  view.rerender(feed("b"));
  const input = screen.getByRole("textbox");
  expect(input).toHaveValue("");
  expect(input).not.toBeDisabled();
  fireEvent.change(input, { target: { value: "B message" } });
  if (state === "sending") fireEvent.keyDown(input, { key: "Enter" });
  await act(async () => first.reject(new api.ApiError(503, "Old request failed")));
  expect(input).toHaveValue(state === "sending" ? "" : "B message");
  if (state === "sending") {
    expect(input).toBeDisabled();
    await act(async () => second.resolve({ seq: 0, timestamp: "2026-01-01T00:00:00Z" }));
    expect(input).not.toBeDisabled();
  } else {
    expect(input).not.toBeDisabled();
  }
  expect(toast.error).not.toHaveBeenCalled();
  expect(useStore.getState().transcript.a).toHaveLength(0);
});

it.each([false, true])("does not navigate on a stale continuation response (returned to A: %s)", async (returnToA) => {
  const continuation = pending<Awaited<ReturnType<typeof api.continueJob>>>();
  vi.spyOn(api, "continueJob").mockReturnValue(continuation.promise);
  const view = render(feed("a", "completed"));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Follow-up for A" } });
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  view.rerender(feed("b"));
  if (returnToA) view.rerender(feed("a", "completed"));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Current draft" } });
  await act(async () => continuation.resolve({
    id: "followup-a", state: "queued", branch: null, worktreePath: null, createdAt: "2026-01-01T00:00:00Z",
  }));
  expect(navigate).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox")).toHaveValue("Current draft");
  expect(useStore.getState().transcript.a).toHaveLength(0);
});

it("does not reinterpret recipe message rejection as Resume or a new Job", async () => {
  const send = vi.spyOn(api, "sendOperatorMessage").mockRejectedValue(
    new api.ApiError(409, "Recipe Jobs require a selected attempt and its current execution token."),
  );
  const resume = vi.spyOn(api, "resumeJob");
  const followup = vi.spyOn(api, "continueJob");
  render(feed("recipe-job", "review"));
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "  preserve this request  " } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(input).not.toBeDisabled());
  expect(input).toHaveValue("  preserve this request  ");
  expect(send).toHaveBeenCalledWith("recipe-job", "preserve this request");
  expect(resume).not.toHaveBeenCalled();
  expect(followup).not.toHaveBeenCalled();
  expect(navigate).not.toHaveBeenCalled();
});

it("preserves successful legacy review messaging without a second operation", async () => {
  const send = vi.spyOn(api, "sendOperatorMessage").mockResolvedValue({ seq: 0, timestamp: "2026-01-01T00:00:00Z" });
  const resume = vi.spyOn(api, "resumeJob");
  render(feed("legacy-job", "review"));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Continue the legacy conversation" } });
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  await waitFor(() => expect(screen.getByRole("textbox")).not.toBeDisabled());
  expect(send).toHaveBeenCalledWith("legacy-job", "Continue the legacy conversation");
  expect(resume).not.toHaveBeenCalled();
});
