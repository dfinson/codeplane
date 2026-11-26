import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { RecipeMessageReceipt } from "../../api/types";
import { useStore } from "../../store";

vi.mock("../../api/client", () => ({
  fetchRecipeAttemptControl: vi.fn(),
  sendRecipeAttemptMessage: vi.fn(),
  resumeJob: vi.fn(),
  sendOperatorMessage: vi.fn(),
}));
import { fetchRecipeAttemptControl, resumeJob, sendOperatorMessage, sendRecipeAttemptMessage } from "../../api/client";
import { RecipeAttemptMessage } from "../RecipeAttemptMessage";

function receipt(status: RecipeMessageReceipt["status"]): RecipeMessageReceipt {
  return {
    jobId: "job", runId: "run", stepInstanceId: "step", attemptId: "attempt",
    executionToken: "live-token", status, executionStatus: "unconfirmed",
    messageEventId: status === "accepted" ? "receipt-1" : null,
    reason: status === "accepted" ? null : "Exact provider result",
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  useStore.setState({ recipeAttemptControls: {}, recipeVersions: {}, connectionStatus: "connected" });
  vi.mocked(fetchRecipeAttemptControl).mockImplementation(async (jobId, runId, stepInstanceId, attemptId) => ({
    jobId, runId, stepInstanceId, attemptId, canMessage: true, messageDisabledReason: null,
    executionToken: "live-token", nativeSessionId: "native-session", executionEpoch: "epoch",
  }));
  vi.mocked(sendRecipeAttemptMessage).mockResolvedValue(receipt("accepted"));
});

it("sends the previewed exact execution and reports admission separately from execution", async () => {
  render(<RecipeAttemptMessage jobId="job" runId="run" stepId="step" attemptId="attempt" />);
  fireEvent.change(await screen.findByLabelText("Instruction for selected attempt"), { target: { value: "Only this attempt" } });
  fireEvent.click(screen.getByText("Send to this attempt"));
  await screen.findByText(/Admitted to this execution. Execution is unconfirmed/);
  expect(sendRecipeAttemptMessage).toHaveBeenCalledWith("job", "run", "step", "attempt", "live-token", "Only this attempt");
  expect(screen.getByLabelText("Instruction for selected attempt")).toHaveValue("");
  expect(resumeJob).not.toHaveBeenCalled();
  expect(sendOperatorMessage).not.toHaveBeenCalled();
});

it.each(["conflict", "failed", "uncertain"] as const)("retains the draft for %s and never silently resumes or retries", async (status) => {
  vi.mocked(sendRecipeAttemptMessage).mockResolvedValue(receipt(status));
  render(<RecipeAttemptMessage jobId="job" runId="run" stepId="step" attemptId="attempt" />);
  fireEvent.change(await screen.findByLabelText("Instruction for selected attempt"), { target: { value: "Retain me" } });
  fireEvent.click(screen.getByText("Send to this attempt"));
  await screen.findByText(`${status}: Exact provider result`);
  expect(screen.getByLabelText("Instruction for selected attempt")).toHaveValue("Retain me");
  await waitFor(() => expect(screen.getByText("Refresh execution identity")).toBeEnabled());
  fireEvent.click(screen.getByText("Refresh execution identity"));
  await waitFor(() => expect(fetchRecipeAttemptControl).toHaveBeenCalledTimes(3));
  expect(sendRecipeAttemptMessage).toHaveBeenCalledTimes(1);
  if (status === "uncertain") expect(screen.getByText("Send to this attempt")).toBeDisabled();
  expect(resumeJob).not.toHaveBeenCalled();
});

it("does not clear a newer draft or retarget a delayed send to another selected attempt", async () => {
  let finish: (value: RecipeMessageReceipt) => void = () => { throw new Error("Missing send"); };
  vi.mocked(sendRecipeAttemptMessage).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = render(<RecipeAttemptMessage jobId="job" runId="run" stepId="step" attemptId="attempt" />);
  fireEvent.change(await screen.findByLabelText("Instruction for selected attempt"), { target: { value: "First" } });
  fireEvent.click(screen.getByText("Send to this attempt"));
  view.rerender(<RecipeAttemptMessage jobId="other-job" runId="other-run" stepId="other-step" attemptId="other-attempt" />);
  fireEvent.change(await screen.findByLabelText("Instruction for selected attempt"), { target: { value: "New target draft" } });
  await act(async () => { finish(receipt("accepted")); });
  expect(screen.getByLabelText("Instruction for selected attempt")).toHaveValue("New target draft");
  expect(screen.queryByText(/Admitted to this execution/)).not.toBeInTheDocument();
  expect(sendRecipeAttemptMessage).toHaveBeenCalledTimes(1);
});
