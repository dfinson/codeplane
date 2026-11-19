import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { ReviewPreparation, ReviewRecord } from "../../api/types";
import { useStore } from "../../store";

vi.mock("../../api/client", async (importOriginal) => ({
  ApiError: (await importOriginal<typeof import("../../api/client")>()).ApiError,
  fetchCurrentRecipeRun: vi.fn(),
  fetchRecipeRunSteps: vi.fn(),
  fetchReviewHistory: vi.fn(),
  prepareReview: vi.fn(),
  recordReview: vi.fn(),
  sendOperatorMessage: vi.fn(),
  resolveJob: vi.fn(),
}));
import { ApiError, fetchCurrentRecipeRun, fetchReviewHistory, prepareReview, recordReview, resolveJob, sendOperatorMessage } from "../../api/client";
import { RecipeReviewPanel } from "../RecipeReviewPanel";

const now = "2026-11-17T14:00:00Z";
const prepared: ReviewPreparation = {
  snapshotArtifactId: "captured-json", snapshotChecksum: "a".repeat(64),
  evidence: {
    jobId: "job", jobVersion: 1, runId: null, runVersion: null, recipeRevisionId: null, recipeSnapshotChecksum: null,
    code: { workspace: "/workspace/job", commit: "b".repeat(40) }, codeHead: "c".repeat(40),
    codeTree: "d".repeat(40), eventHighWater: 42, attempts: [], events: [], artifacts: [], capturedAt: now,
  },
};

beforeEach(() => {
  vi.resetAllMocks();
  useStore.setState({
    recipeCurrentRuns: {}, recipeStepPages: {}, reviewPreparations: {}, reviewHistoryPages: {}, jobs: {},
  });
  vi.mocked(fetchCurrentRecipeRun).mockResolvedValue({ run: null });
  vi.mocked(fetchReviewHistory).mockResolvedValue({ items: [], nextCursor: null, highWater: 42 });
  vi.mocked(prepareReview).mockResolvedValue(prepared);
  vi.mocked(recordReview).mockImplementation(async (_jobId, body): Promise<ReviewRecord> => ({
    ...body, jobId: "job", eventId: "review-receipt", evidence: prepared.evidence, recordedAt: now,
  }));
});

it("records rationale against retained evidence without messaging, delivery, or invented legacy recipe identity", async () => {
  render(<RecipeReviewPanel jobId="job" />);
  await screen.findByText("Legacy Job: no recipe identity is invented.");
  expect(screen.getByText("Record review rationale")).toBeDisabled();
  fireEvent.click(screen.getByText("Retain selected review evidence"));
  await screen.findByText(/Selected code and evidence retained/);
  expect(prepareReview).toHaveBeenCalledWith("job", { expectedRunId: null, attemptIds: [], eventIds: [] });
  fireEvent.change(screen.getByLabelText("Review rationale"), { target: { value: "Reason for this exact revision" } });
  fireEvent.click(screen.getByText("Record review rationale"));
  await screen.findByText(/Review rationale recorded as review-receipt/);
  expect(recordReview).toHaveBeenCalledWith("job", expect.objectContaining({
    snapshotArtifactId: "captured-json", snapshotChecksum: prepared.snapshotChecksum,
    rationale: "Reason for this exact revision",
  }));
  expect(sendOperatorMessage).not.toHaveBeenCalled();
  expect(resolveJob).not.toHaveBeenCalled();
});

it("retries a lost review response with the same idempotency key and unchanged evidence", async () => {
  vi.mocked(recordReview).mockRejectedValueOnce(new Error("Response lost"));
  render(<RecipeReviewPanel jobId="job" />);
  await screen.findByText("Legacy Job: no recipe identity is invented.");
  fireEvent.click(screen.getByText("Retain selected review evidence"));
  await screen.findByText(/Selected code and evidence retained/);
  fireEvent.change(screen.getByLabelText("Review rationale"), { target: { value: "Retained rationale" } });
  fireEvent.click(screen.getByText("Record review rationale"));
  expect(await screen.findByRole("alert")).toHaveTextContent("same request identity");
  const first = vi.mocked(recordReview).mock.calls[0]![1];
  expect(screen.getByLabelText("Review rationale")).toBeDisabled();
  fireEvent.click(screen.getByText("Retry same review receipt"));
  await waitFor(() => expect(recordReview).toHaveBeenCalledTimes(2));
  expect(vi.mocked(recordReview).mock.calls[1]![1]).toEqual(first);
  expect(prepareReview).toHaveBeenCalledTimes(1);
});

it("unlocks explicit recapture after a confirmed stale rejection without losing the rationale", async () => {
  let rejectRecord!: (reason: unknown) => void;
  vi.mocked(recordReview).mockImplementationOnce(() => new Promise<ReviewRecord>((_resolve, reject) => {
    rejectRecord = reject;
  }));
  render(<RecipeReviewPanel jobId="job" />);
  await screen.findByText("Legacy Job: no recipe identity is invented.");
  fireEvent.click(screen.getByText("Retain selected review evidence"));
  await screen.findByText(/Selected code and evidence retained/);
  fireEvent.change(screen.getByLabelText("Review rationale"), { target: { value: "Keep my reasoning" } });
  fireEvent.click(screen.getByText("Record review rationale"));
  const first = vi.mocked(recordReview).mock.calls[0]![1];
  expect(screen.getByText("Retain selected review evidence")).toBeDisabled();
  expect(screen.getByLabelText("Review rationale")).toBeDisabled();
  await act(async () => rejectRecord(new ApiError(409, "The code changed after evidence capture.")));
  expect(screen.getByRole("alert")).toHaveTextContent("Retain fresh evidence");
  expect(screen.getByLabelText("Review rationale")).toHaveValue("Keep my reasoning");
  expect(screen.getByLabelText("Review rationale")).toBeEnabled();
  expect(screen.getByText("Retain selected review evidence")).toBeEnabled();
  expect(screen.getByText("Record review rationale")).toBeDisabled();
  expect(prepareReview).toHaveBeenCalledTimes(1);
  expect(recordReview).toHaveBeenCalledTimes(1);

  const fresh = { ...prepared, snapshotArtifactId: "fresh-json", snapshotChecksum: "e".repeat(64) };
  vi.mocked(prepareReview).mockResolvedValueOnce(fresh);
  fireEvent.click(screen.getByText("Retain selected review evidence"));
  await waitFor(() => expect(screen.getByText("Record review rationale")).toBeEnabled());
  fireEvent.click(screen.getByText("Record review rationale"));
  await screen.findByText(/Review rationale recorded as review-receipt/);
  const second = vi.mocked(recordReview).mock.calls[1]![1];
  expect(second).toMatchObject({
    snapshotArtifactId: fresh.snapshotArtifactId, snapshotChecksum: fresh.snapshotChecksum,
    rationale: "Keep my reasoning",
  });
  expect(second.requestKey).not.toBe(first.requestKey);
  expect(sendOperatorMessage).not.toHaveBeenCalled();
  expect(resolveJob).not.toHaveBeenCalled();
});
