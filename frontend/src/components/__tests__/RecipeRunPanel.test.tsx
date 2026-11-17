import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { RecipeRun } from "../../api/types";
import { useStore } from "../../store";

vi.mock("../../api/client", () => ({
  fetchCurrentRecipeRun: vi.fn(),
  fetchRecipeRun: vi.fn(),
  fetchRecipeRunHistory: vi.fn(),
  fetchRecipeRunSteps: vi.fn(),
  resumeSelectedRecipeRun: vi.fn(),
  resumeJob: vi.fn(),
}));
vi.mock("../RecipeStepInspector", () => ({
  RecipeStepInspector: ({ runId, stepId }: { runId: string; stepId: string }) => <div>Selected {runId} / {stepId}</div>,
}));

import { fetchCurrentRecipeRun, fetchRecipeRun, fetchRecipeRunHistory, fetchRecipeRunSteps, resumeJob, resumeSelectedRecipeRun } from "../../api/client";
import { RecipeRunPanel } from "../RecipeRunPanel";

function run(number: number, jobId = "job"): RecipeRun {
  return {
    id: `${jobId}-run-${number}`, jobId, runNumber: number,
    startRequestKey: `request-${number}`, continuationOfId: null, instruction: `Instruction ${number}`,
    codeRef: null, reason: null, revisionId: "revision",
    snapshot: {
      id: "revision", definitionId: "definition", revision: 1,
      definition: { name: "Pinned recipe", description: "", kind: "recipe", steps: [], entrySteps: [] },
      blocks: {}, checksum: "b".repeat(64), publishedAt: "2026-11-16T09:00:00Z",
    },
    snapshotChecksum: "a".repeat(64), inputs: {}, state: "succeeded", version: 3,
    createdAt: "2026-11-16T09:00:00Z", updatedAt: "2026-11-16T09:00:00Z",
    cancelRequestedAt: null, completedAt: "2026-11-16T09:00:00Z", resolvedAt: "2026-11-16T09:00:00Z",
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  useStore.setState({ recipeCurrentRuns: {}, recipeRuns: {}, recipeRunHistoryPages: {}, recipeStepPages: {}, recipeVersions: {}, connectionStatus: "connected" });
  vi.mocked(fetchCurrentRecipeRun).mockResolvedValue({ run: run(2) });
  vi.mocked(fetchRecipeRun).mockImplementation(async (_job, id) => run(id.endsWith("1") ? 1 : 2));
  vi.mocked(fetchRecipeRunHistory).mockResolvedValue({ items: [run(1)], nextCursor: 1, highWater: 2 });
  vi.mocked(fetchRecipeRunSteps).mockResolvedValue({ items: [], nextCursor: null });
});

it("inspects historical runs without replacing the current run and preserves the history bound", async () => {
  render(<RecipeRunPanel jobId="job" />);
  await screen.findByLabelText("Recipe run history");
  await waitFor(() => expect(fetchRecipeRunHistory).toHaveBeenCalledWith("job", { after: 0, limit: 30 }));
  fireEvent.change(screen.getByLabelText("Recipe run history"), { target: { value: "job-run-1" } });
  await screen.findByText("Instruction 1");
  expect(screen.getByText(/Historical run/)).toBeInTheDocument();
  expect(useStore.getState().recipeCurrentRuns.job?.id).toBe("job-run-2");
  expect(fetchRecipeRunSteps).toHaveBeenCalledWith("job", "job-run-1", "");
  fireEvent.click(screen.getByText("More history"));
  await waitFor(() => expect(fetchRecipeRunHistory).toHaveBeenLastCalledWith("job", { after: 1, highWater: 2, limit: 30 }));
  expect(resumeJob).not.toHaveBeenCalled();
});

it("does not interpret a failed identity read as a legacy Job and retries only the read", async () => {
  vi.mocked(fetchCurrentRecipeRun).mockRejectedValueOnce(new Error("Offline"));
  render(<RecipeRunPanel jobId="job" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Recipe identity could not be read");
  fireEvent.click(screen.getByText("Retry recipe read"));
  await screen.findByText("View pinned definition");
  expect(fetchCurrentRecipeRun).toHaveBeenCalledTimes(2);
  expect(resumeJob).not.toHaveBeenCalled();
});

it("fences a delayed current-run response after changing Jobs", async () => {
  let finish: (value: { run: RecipeRun }) => void = () => { throw new Error("Missing pending request"); };
  vi.mocked(fetchCurrentRecipeRun).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  vi.mocked(fetchCurrentRecipeRun).mockResolvedValueOnce({ run: run(2, "new-job") });
  const view = render(<RecipeRunPanel jobId="old-job" />);
  view.rerender(<RecipeRunPanel jobId="new-job" />);
  await screen.findByText("View pinned definition");
  await act(async () => { finish({ run: run(1, "old-job") }); });
  expect(useStore.getState().recipeCurrentRuns["old-job"]).toBeUndefined();
  expect(useStore.getState().recipeCurrentRuns["new-job"]?.id).toBe("new-job-run-2");
});

it("never displays the current definition for a pending or failed historical selection", async () => {
  let rejectHistory: (error: Error) => void = () => { throw new Error("No historical read"); };
  const delayed = new Promise<RecipeRun>((_resolve, reject) => { rejectHistory = reject; });
  vi.mocked(fetchRecipeRun).mockImplementation((_job, id) => id.endsWith("1") ? delayed : Promise.resolve(run(2)));
  render(<RecipeRunPanel jobId="job" />);
  await screen.findByText("View pinned definition");
  fireEvent.change(screen.getByLabelText("Recipe run history"), { target: { value: "job-run-1" } });
  await screen.findByText("Reading selected historical run...");
  expect(screen.queryByText("View pinned definition")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Pinned recipe" })).not.toBeInTheDocument();
  await act(async () => { rejectHistory(new Error("Historical record unavailable")); });
  await screen.findByText("Selected run unavailable.");
  expect(screen.getByRole("alert")).toHaveTextContent("Historical record unavailable");
  expect(screen.queryByText("View pinned definition")).not.toBeInTheDocument();
  expect(useStore.getState().recipeCurrentRuns.job?.id).toBe("job-run-2");
  expect(resumeJob).not.toHaveBeenCalled();
});

it("retains the exact continuation request after an unconfirmed response", async () => {
  vi.mocked(resumeSelectedRecipeRun).mockRejectedValue(new Error("Response lost"));
  render(<RecipeRunPanel jobId="job" />);
  const field = await screen.findByLabelText("Recipe continuation instruction");
  fireEvent.change(field, { target: { value: "Continue from current code" } });
  fireEvent.click(screen.getByText("Resume Job with new run"));
  await screen.findByRole("alert");
  const first = vi.mocked(resumeSelectedRecipeRun).mock.calls[0];
  fireEvent.click(screen.getByText("Retry same Resume receipt"));
  await waitFor(() => expect(resumeSelectedRecipeRun).toHaveBeenCalledTimes(2));
  expect(vi.mocked(resumeSelectedRecipeRun).mock.calls[1]).toEqual(first);
  expect(first?.[1]).toBe("job-run-2");
  expect(resumeJob).not.toHaveBeenCalled();
});
