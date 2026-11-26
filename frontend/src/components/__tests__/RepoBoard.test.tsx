import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import type { ProjectBoard, ProjectBoardItem, TaskLinkInspection } from "../../api/types";
import { useStore } from "../../store";

vi.mock("../../api/client", () => ({
  fetchProjectBoard: vi.fn(),
  fetchProjectBoardPage: vi.fn(),
  fetchProjectBoardApprovals: vi.fn(),
  fetchProjectBoardDeliveries: vi.fn(),
  fetchTaskLinkInspection: vi.fn(),
  startTaskLink: vi.fn(),
  fetchJobs: vi.fn(),
}));
vi.mock("../TrackerSyncPanel", () => ({
  TrackerOperation: ({ operation }: { operation: { id: string; state: string } }) => <div>{operation.id}: {operation.state}</div>,
}));
vi.mock("../KanbanSkeleton", () => ({
  KanbanSkeleton: () => <div data-testid="kanban-skeleton" />,
}));

import { fetchJobs, fetchProjectBoard, fetchProjectBoardApprovals, fetchProjectBoardDeliveries, fetchProjectBoardPage, fetchTaskLinkInspection, startTaskLink } from "../../api/client";
import { RepoBoard } from "../RepoBoard";

const now = "2026-11-16T10:00:00Z";

function task(id: string) {
  return { id, repoPath: "/historical/repo", storyNodeId: id, trackerTicketRef: null, state: "ready" };
}

function item(id: string, linked = false): ProjectBoardItem {
  return {
    id: `${linked ? "job" : "task"}:${id}`,
    column: linked ? "needs_decision" : "ready_blocked",
    tasks: [task(id)], taskCount: 1, hasFailure: false,
    pendingApprovalCount: 0, deliveryAttentionCount: 0,
    job: linked ? {
      id: "job-1", projectId: "project-1", repo: "/historical/repo", prompt: "Exact prompt",
      title: "Linked work", state: "review", baseRef: "main", worktreePath: null, branch: null,
      createdAt: now, updatedAt: now, completedAt: null, sdk: "copilot", source: "managed", mode: "standard",
      observationOnly: false,
      sessionCount: 1,
    } : null,
    recipe: linked ? {
      runId: "run-1", revision: 2, name: "Pinned recipe", state: "succeeded", reason: null,
      steps: [{ id: "step-1", title: "Context", state: "succeeded", reason: "Brief retained" }],
      remainingStepCount: 0,
    } : null,
  };
}

function board(projectId = "project-1"): ProjectBoard {
  return {
    project: { id: projectId, name: projectId, repoPaths: ["/current/repo"], createdAt: now, updatedAt: now },
    totals: {
      taskCount: 3, linkedJobCount: 1, standaloneJobCount: 0, pendingApprovalCount: 0,
      reviewJobCount: 1, deliveryAttentionCount: 0,
      workCounts: { ready_blocked: 2, in_progress: 0, needs_decision: 1, failed: 0 },
    },
    columns: {
      ready_blocked: { items: [item("task-a"), item("task-b")], nextCursor: null },
      in_progress: { items: [], nextCursor: null },
      needs_decision: { items: [item("task-linked", true)], nextCursor: null },
      failed: { items: [], nextCursor: null },
    },
  };
}

function inspection(canStart = false): TaskLinkInspection {
  return {
    task: {
      id: "task-a", projectId: "project-1", repoPath: "/current/repo", storyNodeId: "task-a",
      dependsOn: [], chainRootId: "task-a", state: "ready", jobId: null, trackerLinkId: null,
      trackerTicketRef: null, promptOverride: null, epicId: null, outputRoutes: [], createdAt: now, updatedAt: now,
    },
    prompt: "Locate the complete historical task. Report missing requirements rather than inventing them.",
    promptSource: "historical_reference", dependencies: [], canStart,
    disabledReason: canStart ? null : "An exact chain approval is required.",
    pendingApprovalIds: [], requiresChainApproval: !canStart, sdk: "copilot", model: null,
    preset: "supervised", mode: "standard", recipeRevisionId: null,
  };
}

function renderBoard(path = "/projects/id/project-1/board") {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/projects/id/:projectId/board" element={<RepoBoard />} />
    <Route path="/projects/id/:projectId/board/task/:taskLinkId" element={<RepoBoard />} />
    <Route path="/jobs/:jobId" element={<div>Linked job screen</div>} />
  </Routes></MemoryRouter>);
}

beforeEach(() => {
  vi.resetAllMocks();
  useStore.setState({
    projectBoards: {}, projectBoardPages: {}, projectApprovalPages: {}, projectDeliveryPages: {},
    taskLinkInspections: {}, projectBoardVersion: 0, connectionStatus: "connected", trackerOperations: {},
  });
  vi.mocked(fetchProjectBoard).mockResolvedValue(board());
  vi.mocked(fetchProjectBoardApprovals).mockResolvedValue({ items: [], nextCursor: null });
  vi.mocked(fetchProjectBoardDeliveries).mockResolvedValue({ items: [], nextCursor: null });
  vi.mocked(fetchProjectBoardPage).mockResolvedValue({ items: [], nextCursor: null });
  vi.mocked(fetchTaskLinkInspection).mockResolvedValue(inspection());
});

it("shows three unique work cards for three tasks and one linked Job, not extra recipe-step cards", async () => {
  renderBoard();
  await screen.findByLabelText("Persisted Project totals");
  expect(screen.getAllByLabelText(/^Work item:/)).toHaveLength(3);
  const combined = screen.getByLabelText("Work item: task-linked");
  expect(within(combined).getByText("Task task-linked: ready")).toBeInTheDocument();
  expect(within(combined).getByText(/Job: review/)).toBeInTheDocument();
  expect(within(combined).getByText(/Pinned recipe \/ revision 2/)).toBeInTheDocument();
  expect(screen.getByLabelText("Persisted Project totals")).toHaveTextContent("3 tasks / 1 linked Jobs");
  expect(screen.queryByRole("button", { name: /Ingest/ })).not.toBeInTheDocument();
  expect(fetchJobs).not.toHaveBeenCalled();
});

it("uses full-scope server totals while paging only the selected Project column", async () => {
  const value = board();
  value.totals.workCounts.ready_blocked = 35;
  value.columns.ready_blocked!.nextCursor = "task:page-one-end";
  vi.mocked(fetchProjectBoard).mockResolvedValue(value);
  vi.mocked(fetchProjectBoardPage).mockResolvedValue({ items: [item("later-task")], nextCursor: null });
  renderBoard();
  const column = await screen.findByRole("region", { name: "Ready / blocked" });
  expect(within(column).getByText("35")).toBeInTheDocument();
  fireEvent.click(within(column).getByText("More work"));
  await screen.findByLabelText("Work item: later-task");
  expect(fetchProjectBoardPage).toHaveBeenCalledWith("project-1", "ready_blocked", "task:page-one-end", expect.any(AbortSignal));
  expect(within(column).getByText("35")).toBeInTheDocument();
  expect(fetchJobs).not.toHaveBeenCalled();
});

it("reads actual approvals for completed or archived Jobs independently of current Job cards", async () => {
  const value = board();
  value.totals.pendingApprovalCount = 3;
  vi.mocked(fetchProjectBoard).mockResolvedValue(value);
  vi.mocked(fetchProjectBoardApprovals).mockResolvedValue({
    items: [{
      id: "approval-1", jobId: "archived-job", description: "Retained approval",
      proposedAction: "Exact proposal", requestedAt: now, resolvedAt: null, resolution: null,
      requiresExplicitApproval: true,
    }], nextCursor: "approval-1",
  });
  renderBoard();
  await screen.findByText("Retained approval");
  expect(screen.getByLabelText("Project attention queue")).toHaveTextContent("3 pending approvals");
  fireEvent.click(screen.getByText("More approvals"));
  await waitFor(() => expect(fetchProjectBoardApprovals).toHaveBeenLastCalledWith("project-1", "approval-1", expect.any(AbortSignal)));
  expect(screen.getByRole("link", { name: "Inspect decision in Job" })).toHaveAttribute("href", "/jobs/archived-job");
});

it("opens the complete task instruction with the same disabled Start eligibility", async () => {
  renderBoard("/projects/id/project-1/board/task/task-a");
  await screen.findByText(inspection().prompt);
  expect(screen.getByText(/Only a historical task reference is stored/)).toBeInTheDocument();
  expect(screen.getByText(/An exact chain approval is required/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start inspected task" })).toBeDisabled();
  expect(startTaskLink).not.toHaveBeenCalled();
});

it("preserves explicit saved-task Start without introducing an importer", async () => {
  const value = inspection(true);
  vi.mocked(fetchTaskLinkInspection).mockResolvedValue(value);
  vi.mocked(startTaskLink).mockResolvedValue({ ...value.task, state: "running", jobId: "started-job" });
  renderBoard("/projects/id/project-1/board/task/task-a");
  fireEvent.click(await screen.findByRole("button", { name: "Start inspected task" }));
  await screen.findByText("Linked job screen");
  expect(startTaskLink).toHaveBeenCalledWith("project-1", "task-a");
});

it("prefills only a sole repository in the owning Project's New Job link", async () => {
  renderBoard();
  const link = await screen.findByRole("link", { name: "New Job" });
  expect(link.getAttribute("href")).toContain("projectId=project-1");
  expect(link.getAttribute("href")).toContain("repo=%2Fcurrent%2Frepo");
});

it("retains Project-only selection for multi-repository New Job", async () => {
  const value = board();
  value.project.repoPaths = ["/repo/a", "/repo/b"];
  vi.mocked(fetchProjectBoard).mockResolvedValue(value);
  renderBoard();
  const link = await screen.findByRole("link", { name: "New Job" });
  expect(link.getAttribute("href")).toBe("/jobs/new?projectId=project-1");
});

it("does not mix a different Project response into the selected Board", async () => {
  vi.mocked(fetchProjectBoard).mockResolvedValue(board("foreign-project"));
  renderBoard();
  expect(await screen.findByRole("alert")).toHaveTextContent("different Project");
  expect(screen.queryByLabelText("Persisted Project totals")).not.toBeInTheDocument();
});

it("refreshes persisted reads after a canonical permission event without executing commands", async () => {
  renderBoard();
  await screen.findByLabelText("Persisted Project totals");
  await act(async () => { useStore.getState().dispatchSSEEvent("permission.requested", { jobId: "archived-job" }); });
  await waitFor(() => expect(fetchProjectBoard).toHaveBeenCalledTimes(2));
  expect(startTaskLink).not.toHaveBeenCalled();
});
