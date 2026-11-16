import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RecipeAttemptInspection, RecipeStepInspection } from "../../api/types";
import { useStore } from "../../store";

vi.mock("../../api/client", () => ({
  fetchRecipeStep: vi.fn(),
  fetchRecipeAttempt: vi.fn(),
  resumeJob: vi.fn(),
  sendOperatorMessage: vi.fn(),
}));

import { fetchRecipeAttempt, fetchRecipeStep, resumeJob, sendOperatorMessage } from "../../api/client";
import { RecipeStepInspector } from "../RecipeStepInspector";

const now = "2026-11-16T09:00:00Z";

function step(id = "check"): RecipeStepInspection {
  return {
    step: {
      id, runId: "run", stepPath: id,
      definition: {
        id, title: `Check ${id}`, instructions: "Run the pinned check.",
        runner: { kind: "command", argv: ["git", "status", "--porcelain"], timeoutSeconds: 300 },
        capabilities: ["read", "shell"], outputs: { result: { type: "string", description: "" } },
      },
      state: "failed", reason: "Actual nonzero command result", currentAttemptId: `${id}-2`,
      version: 4, createdAt: now, updatedAt: now,
    },
    attempts: [
      { id: `${id}-1`, number: 1, dispatchIntentAt: now, outcome: "failed", completedAt: now },
      { id: `${id}-2`, number: 2, dispatchIntentAt: now, outcome: "failed", completedAt: now },
    ],
  };
}

function attempt(id = "check-2"): RecipeAttemptInspection {
  return {
    gate: null,
    attempt: {
      id, stepInstanceId: "check", number: id.endsWith("1") ? 1 : 2,
      inputs: {
        request: { type: "string", present: true, value: `Input ${id}`, checksum: "input-checksum" },
      },
      runner: { kind: "command", argv: ["git", "status", "--porcelain"], timeoutSeconds: 300 },
      capabilities: ["read", "shell"],
      workspace: { path: "/workspace/job", codeRef: "a".repeat(40), policy: { mode: "snapshot", key: "job" } },
      dispatchIntentAt: now, executions: [],
      result: {
        outcome: "failed", reason: "Actual exit status 7", completedAt: now,
        evidence: {
          kind: "command", argv: ["git", "status", "--porcelain"], exitStatus: 7,
          stdout: null, stderr: "Actual check failure", workspace: "/workspace/job",
          startCodeRef: "a".repeat(40), endCodeRef: "b".repeat(40), started: true, uncertain: false,
        },
      },
      outputs: [{ id: `output-${id}`, attemptId: id, name: "result", type: "string", value: `Output ${id}`, checksum: "exact-output-checksum", createdAt: now }],
    },
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  useStore.setState({ recipeStepInspections: {}, recipeAttemptInspections: {}, recipeVersions: {}, connectionStatus: "connected" });
  vi.mocked(fetchRecipeStep).mockImplementation(async (_job, _run, id) => step(id));
  vi.mocked(fetchRecipeAttempt).mockImplementation(async (_job, _run, _step, id) => attempt(id));
});

describe("RecipeStepInspector", () => {
  it("shows exact command evidence and immutable historical attempt inputs without fictional sessions", async () => {
    render(<RecipeStepInspector jobId="job" runId="run" stepId="check" />);
    await screen.findByText("Check check");
    await waitFor(() => expect(fetchRecipeAttempt).toHaveBeenCalledWith("job", "run", "check", "check-2"));
    fireEvent.click(screen.getByText("Output", { selector: "span" }));
    await screen.findByText("Actual check failure");
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("Output check-2")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Selected attempt"), { target: { value: "check-1" } });
    await screen.findByText("Output check-1");
    expect(screen.queryByText("Output check-2")).not.toBeInTheDocument();
    expect(screen.getByText(/Historical attempt/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Inputs", { selector: "span" }));
    expect(screen.getByText("Input check-1")).toBeInTheDocument();
    fireEvent.click(screen.getByText("History", { selector: "span" }));
    expect(screen.getByText(/No agent sessions recorded/)).toBeInTheDocument();
    expect(resumeJob).not.toHaveBeenCalled();
    expect(sendOperatorMessage).not.toHaveBeenCalled();
  });

  it("does not install a late response for a previously selected Job and step", async () => {
    let resolveOld: (value: RecipeStepInspection) => void = () => { throw new Error("Not awaiting old read"); };
    vi.mocked(fetchRecipeStep).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const view = render(<RecipeStepInspector jobId="old-job" runId="run" stepId="old" />);
    view.rerender(<RecipeStepInspector jobId="new-job" runId="run" stepId="new" />);
    await screen.findByText("Check new");
    await act(async () => { resolveOld(step("old")); });
    expect(screen.queryByText("Check old")).not.toBeInTheDocument();
    expect(useStore.getState().recipeStepInspections[JSON.stringify(["old-job", "run", "old"])]).toBeUndefined();
    expect(fetchRecipeAttempt).not.toHaveBeenCalledWith("new-job", "run", "new", "old-2");
  });

  it("retries only the selected read after an error and refreshes from recipe events", async () => {
    vi.mocked(fetchRecipeStep).mockRejectedValueOnce(new Error("Snapshot unavailable"));
    render(<RecipeStepInspector jobId="job" runId="run" stepId="check" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Snapshot unavailable");
    fireEvent.click(screen.getByText("Retry read"));
    await screen.findByText("Check check");
    await act(async () => { useStore.getState().dispatchSSEEvent("recipe.updated", { jobId: "job" }); });
    await waitFor(() => expect(fetchRecipeStep).toHaveBeenCalledTimes(3));
    expect(resumeJob).not.toHaveBeenCalled();
    expect(sendOperatorMessage).not.toHaveBeenCalled();
  });
});
