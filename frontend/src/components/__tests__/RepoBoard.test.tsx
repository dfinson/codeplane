/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { useStore } from "../../store";
import type { JobSummary } from "../../store";

// Mock the API client
vi.mock("../../api/client", () => ({
  fetchJobs: vi.fn(),
  fetchProject: vi.fn(),
  fetchProjectTaskLinks: vi.fn(),
  ingestProjectTasks: vi.fn(),
  startTaskLink: vi.fn(),
}));

import {
  fetchJobs,
  fetchProject,
  fetchProjectTaskLinks,
  ingestProjectTasks,
  startTaskLink,
} from "../../api/client";
import { RepoBoard } from "../RepoBoard";

vi.mock("../KanbanSkeleton", () => ({
  KanbanSkeleton: () => <div data-testid="kanban-skeleton">KanbanSkeleton</div>,
}));

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-1",
    repo: "/repos/test",
    projectId: "proj-1",
    prompt: "Fix the bug",
    state: "running",
    baseRef: "main",
    worktreePath: "/repos/test",
    branch: "fix/bug",
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-01T00:00:00Z",
    completedAt: null,
    prUrl: null,
    ...overrides,
  };
}

function makeTaskLink(overrides: Partial<any> = {}) {
  return {
    id: "tl-1",
    projectId: "proj-1",
    repoPath: "/repos/test",
    storyNodeId: "add-sca",
    dependsOn: [],
    state: "ready",
    jobId: null,
    trackerLinkId: null,
    trackerTicketRef: null,
    promptOverride: null,
    epicId: null,
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeProject(overrides: Partial<any> = {}) {
  return {
    id: "proj-1",
    name: "payments",
    repoPaths: ["/repos/test"],
    createdAt: "",
    updatedAt: "",
    ...overrides,
  };
}

function renderBoard(projectId = "proj-1", route?: string) {
  return render(
    <MemoryRouter initialEntries={[route ?? `/projects/id/${encodeURIComponent(projectId)}/board`]}>
      <Routes>
        <Route path="/projects/id/:projectId/board" element={<RepoBoard />} />
        <Route path="/projects/id/:projectId/board/task/:taskLinkId" element={<RepoBoard />} />
        <Route path="/jobs/new" element={<div>New job screen</div>} />
        <Route path="/jobs/:jobId" element={<div>Linked job screen</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(fetchJobs).mockReset();
  vi.mocked(fetchProject).mockReset();
  vi.mocked(fetchProjectTaskLinks).mockReset();
  vi.mocked(ingestProjectTasks).mockReset();
  vi.mocked(startTaskLink).mockReset();
  vi.mocked(fetchProject).mockResolvedValue(makeProject() as any);
  vi.mocked(fetchProjectTaskLinks).mockResolvedValue({ items: [] } as any);
  useStore.setState({
    jobs: {},
    approvals: {},
    logs: {},
    transcript: {},
    diffs: {},
  });
});

describe("RepoBoard", () => {
  it("shows the skeleton immediately on a cold load before jobs arrive", () => {
    vi.mocked(fetchJobs).mockReturnValueOnce(new Promise(() => {}) as any);
    renderBoard();
    expect(screen.getByTestId("kanban-skeleton")).toBeInTheDocument();
  });

  it("keeps the existing board visible during a refresh when jobs are already loaded", () => {
    useStore.setState({
      jobs: { existing: makeJob({ id: "existing" }) },
    });
    vi.mocked(fetchJobs).mockReturnValueOnce(new Promise(() => {}) as any);
    renderBoard();
    expect(screen.queryByTestId("kanban-skeleton")).not.toBeInTheDocument();
  });

  it("fetches jobs on mount", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    renderBoard();
    await waitFor(() => {
      expect(fetchJobs).toHaveBeenCalledWith({ limit: 100, archived: false });
    });
  });

  it("paginates through every global jobs page", async () => {
      vi.mocked(fetchJobs)
        .mockResolvedValueOnce({
          items: [makeJob({ id: "other", repo: "/repos/other" })],
          cursor: "other",
          hasMore: true,
        } as any)
        .mockResolvedValueOnce({
          items: [makeJob({ id: "project-job", title: "Page two job" })],
          cursor: null,
          hasMore: false,
        } as any);

      renderBoard();

      expect(await screen.findByText("Page two job")).toBeInTheDocument();
      expect(fetchJobs).toHaveBeenNthCalledWith(1, { limit: 100, archived: false });
      expect(fetchJobs).toHaveBeenNthCalledWith(2, {
        limit: 100,
        archived: false,
        cursor: "other",
      });
  });

  it("clears the previous Project and renders an error after navigation fails", async () => {
      vi.mocked(fetchProject)
        .mockResolvedValueOnce(makeProject({ name: "First Project" }) as any)
        .mockRejectedValueOnce(new Error("Project missing"));
      vi.mocked(fetchJobs)
        .mockResolvedValueOnce({ items: [], cursor: null, hasMore: false } as any)
        .mockResolvedValueOnce({ items: [], cursor: null, hasMore: false } as any);
      vi.mocked(fetchProjectTaskLinks)
        .mockResolvedValueOnce({ items: [] } as any)
        .mockResolvedValueOnce({ items: [] } as any);

      render(
        <MemoryRouter initialEntries={["/projects/id/proj-1/board"]}>
          <Routes>
            <Route
              path="/projects/id/:projectId/board"
              element={(
                <>
                  <Link to="/projects/id/missing/board">Open missing Project</Link>
                  <RepoBoard />
                </>
              )}
            />
          </Routes>
        </MemoryRouter>,
      );

      expect(await screen.findByText("First Project")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("link", { name: "Open missing Project" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("Project missing");
      expect(screen.queryByText("First Project")).not.toBeInTheDocument();
  });

  it("links the primary New Job action to the scoped project", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    renderBoard();

    expect(await screen.findByRole("link", { name: "New Job" })).toHaveAttribute(
      "href",
      "/jobs/new?projectId=proj-1&repo=%2Frepos%2Ftest",
    );
  });

  it("shows a persistent task deep-link banner and highlights the matching TaskLink card", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValueOnce({ items: [makeTaskLink({ id: "tl-1", storyNodeId: "add-sca" })] } as any);

    renderBoard("proj-1", "/projects/id/proj-1/board/task/tl-1");

    expect(await screen.findByText("Viewing task")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" }).closest("div")).toHaveTextContent("add-sca");
    expect(screen.getByLabelText("Task recipe: add-sca — ready")).toHaveClass("ring-2");
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("shows an inline message when a deep-linked TaskLink no longer exists", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValueOnce({ items: [] } as any);

    renderBoard("proj-1", "/projects/id/proj-1/board/task/missing-task");

    expect(await screen.findByRole("alert")).toHaveTextContent("Task no longer exists in this project.");
  });

  it("shows persistent tracker-task guidance when the Project has no task links", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValueOnce({ items: [] } as any);

    renderBoard();

    expect(await screen.findByText("No tracker tasks yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Project settings" })).toHaveAttribute(
      "href",
      "/projects/id/proj-1/settings",
    );
  });

  it("shows human-readable blocker names for waiting TaskLinks", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValueOnce({
      items: [
        makeTaskLink({ id: "dep-1", storyNodeId: "add-auth", state: "running" }),
        makeTaskLink({ id: "blocked-1", storyNodeId: "ship-ui", state: "waiting", dependsOn: ["/repos/test::add-auth"] }),
      ],
    } as any);

    renderBoard();

    expect(await screen.findByText("Add Auth (in progress)")).toBeInTheDocument();
  });

  it("renders the Board heading and Project name", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProject).mockResolvedValueOnce(makeProject({ name: "my-app" }) as any);
    renderBoard("proj-1");
    await waitFor(() => expect(screen.getByText("Board")).toBeInTheDocument());
    expect(screen.getByText("my-app")).toBeInTheDocument();
  });

  it("shows only jobs belonging to the Project's member repos, excluding other repos", async () => {
    const jobA = makeJob({ id: "job-a", repo: "/repos/test", title: "Job A", state: "running" });
    const jobB = makeJob({ id: "job-b", repo: "/repos/other", projectId: "proj-2", title: "Job B", state: "running" });
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [jobA, jobB], cursor: null } as any);
    renderBoard("proj-1");
    await waitFor(() => expect(screen.getByText("Job A")).toBeInTheDocument());
    // Job B belongs to a different Project and must never appear (CAP-1 / AD-5).
    expect(screen.queryByText("Job B")).not.toBeInTheDocument();
  });

  it("aggregates jobs across ALL member repos for a multi-repo Project", async () => {
    vi.mocked(fetchProject).mockResolvedValueOnce(makeProject({ repoPaths: ["/repos/test", "/repos/other"] }) as any);
    const jobA = makeJob({ id: "job-a", repo: "/repos/test", title: "Job A", state: "running" });
    const jobB = makeJob({ id: "job-b", repo: "/repos/other", title: "Job B", state: "running" });
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [jobA, jobB], cursor: null } as any);
    renderBoard("proj-1");
    await waitFor(() => expect(screen.getByText("Job A")).toBeInTheDocument());
    expect(screen.getByText("Job B")).toBeInTheDocument();
  });

  it("classifies scoped jobs into the same three-column buckets as the flat board", async () => {
    const active = makeJob({ id: "active", repo: "/repos/test", title: "Active job", state: "running" });
    const signoff = makeJob({ id: "signoff", repo: "/repos/test", title: "Signoff job", state: "waiting_for_approval" });
    const failed = makeJob({ id: "failed", repo: "/repos/test", title: "Failed job", state: "failed" });
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [active, signoff, failed], cursor: null } as any);
    renderBoard("proj-1");
    await waitFor(() => expect(screen.getByText("Active job")).toBeInTheDocument());
    expect(screen.getByText("Signoff job")).toBeInTheDocument();
    expect(screen.getByText("Failed job")).toBeInTheDocument();
    expect(screen.getByText("In Progress")).toBeInTheDocument();
    expect(screen.getByText("Awaiting Input")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Failed" })).toBeInTheDocument();
  });

  it("keeps every Task state and returned order separate from Job columns and counts", async () => {
    const states = ["waiting", "ready", "starting", "running", "completed", "failed"] as const;
    const tasks = states.map((state) => makeTaskLink({
      id: `task-${state}`,
      storyNodeId: `task-${state}`,
      state,
      jobId: state === "running" ? "active" : null,
    }));
    vi.mocked(fetchJobs).mockResolvedValueOnce({
      items: [
        makeJob({ id: "active", title: "Active job", state: "running" }),
        makeJob({ id: "approval", title: "Approval job", state: "waiting_for_approval" }),
        makeJob({ id: "failed", title: "Failed job", state: "failed" }),
      ],
      cursor: null,
    } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValue({ items: tasks } as any);

    renderBoard();

    await screen.findByText("task-waiting");
    const taskSection = screen.queryByRole("region", { name: "Tasks", exact: true });
    expect(taskSection).not.toBeNull();
    expect(within(taskSection!).getByLabelText("6 tasks")).toHaveTextContent("6");
    const cards = states.map((state) => within(taskSection!).getByLabelText(`Task recipe: task-${state} — ${state}`));
    expect(Array.from(taskSection!.querySelectorAll('[id^="task-link-card-"]'))).toEqual(cards);
    const jobSection = screen.getByRole("region", { name: "Jobs", exact: true });
    expect(taskSection!.compareDocumentPosition(jobSection) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    for (const [title, jobTitle] of [
      ["In Progress", "Active job"],
      ["Awaiting Input", "Approval job"],
      ["Failed", "Failed job"],
    ]) {
      const column = within(jobSection).getByRole("region", { name: title, exact: true });
      expect(within(column).getByText(jobTitle)).toBeInTheDocument();
      expect(within(column).getByText("1", { exact: true })).toBeInTheDocument();
      for (const card of cards) expect(column).not.toContainElement(card);
    }
  });

  it("scopes empty Job messages to Jobs when a saved Task has failed", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValue({
      items: [makeTaskLink({ id: "failed-task", storyNodeId: "failed-task", state: "failed" })],
    } as any);

    renderBoard();

    expect(await screen.findByLabelText("Task recipe: failed-task — failed")).toBeInTheDocument();
    const failedJobs = screen.getByRole("region", { name: "Failed", exact: true });
    expect(within(failedJobs).getByText("0", { exact: true })).toBeInTheDocument();
    expect(within(failedJobs).queryByText("No failed jobs")).not.toBeNull();
    expect(within(screen.getByRole("region", { name: "Awaiting Input", exact: true }))
      .queryByText("No jobs awaiting input")).not.toBeNull();
    expect(screen.queryByText("All clear")).not.toBeInTheDocument();
    expect(screen.queryByText("No failures or issues")).not.toBeInTheDocument();
  });

  it("preserves highlighted Task deep links and keyboard navigation in the Tasks section", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValue({
      items: [makeTaskLink({ id: "linked-task", storyNodeId: "linked-task", state: "completed", jobId: "job-done" })],
    } as any);

    renderBoard("proj-1", "/projects/id/proj-1/board/task/linked-task");

    await screen.findByText("Viewing task");
    const tasks = screen.queryByRole("region", { name: "Tasks", exact: true });
    expect(tasks).not.toBeNull();
    const card = within(tasks!).getByRole("link", { name: "Task recipe: linked-task — completed" });
    expect(card).toHaveClass("ring-2");
    expect(card).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(card, { key: "Enter" });
    expect(await screen.findByText("Linked job screen")).toBeInTheDocument();
  });

  it("renders TaskLink cards for the Project in the Tasks section", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValue({
      items: [makeTaskLink({ id: "tl-1", storyNodeId: "add-sca" })],
    } as any);
    renderBoard("proj-1");
    await waitFor(() => expect(screen.getByText("add-sca")).toBeInTheDocument());
    const tasks = screen.queryByRole("region", { name: "Tasks", exact: true });
    expect(tasks).not.toBeNull();
    expect(within(tasks!).getByText("ready")).toBeInTheDocument();
  });

  it("greys out a TaskLink card whose dependency's linked job has not completed", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValue({
      items: [
        makeTaskLink({ id: "tl-1", storyNodeId: "add-sca", jobId: "job-running", state: "running" }),
        makeTaskLink({ id: "tl-2", storyNodeId: "sca-tests", dependsOn: ["/repos/test::add-sca"], state: "waiting" }),
      ],
    } as any);
    useStore.setState({
      jobs: { "job-running": makeJob({ id: "job-running", state: "running" }) },
    });
    renderBoard("proj-1");
    await waitFor(() => expect(screen.getByText("sca-tests")).toBeInTheDocument());
    expect(screen.getByText("waiting")).toBeInTheDocument();
    expect(screen.getByLabelText(/sca-tests — waiting/)).toHaveClass("opacity-60");
  });

  it("renders a TaskLink card as satisfied once its dependency's job has completed", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValue({
      items: [
        makeTaskLink({ id: "tl-1", storyNodeId: "add-sca", jobId: "job-done", state: "completed" }),
        makeTaskLink({ id: "tl-2", storyNodeId: "sca-tests", dependsOn: ["/repos/test::add-sca"], state: "ready" }),
      ],
    } as any);
    useStore.setState({
      jobs: { "job-done": makeJob({ id: "job-done", state: "completed" }) },
    });
    renderBoard("proj-1");
    await waitFor(() => expect(screen.getByText("sca-tests")).toBeInTheDocument());
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
  });

  it("renders TaskLink cards from any of the Project's member repos", async () => {
    vi.mocked(fetchProject).mockResolvedValueOnce(makeProject({ repoPaths: ["/repos/test", "/repos/other"] }) as any);
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValue({
      items: [
        makeTaskLink({ id: "tl-1", repoPath: "/repos/test", storyNodeId: "add-sca" }),
        makeTaskLink({ id: "tl-2", repoPath: "/repos/other", storyNodeId: "other-task" }),
      ],
    } as any);
    renderBoard("proj-1");
    await waitFor(() => expect(screen.getByText("add-sca")).toBeInTheDocument());
    expect(screen.getByText("other-task")).toBeInTheDocument();
  });

  it("reconciles TaskLinks when a linked Job lifecycle event changes", async () => {
    const runningLink = makeTaskLink({
      id: "tl-live",
      storyNodeId: "live-task",
      state: "running",
      jobId: "job-live",
    });
    vi.mocked(fetchJobs).mockResolvedValueOnce({
      items: [makeJob({ id: "job-live", state: "running" })],
      cursor: null,
    } as any);
    vi.mocked(fetchProjectTaskLinks)
      .mockResolvedValueOnce({ items: [runningLink] } as any)
      .mockResolvedValueOnce({
        items: [{ ...runningLink, state: "completed" }],
      } as any);

    renderBoard();
    expect(await screen.findByLabelText("Task recipe: live-task — running")).toBeInTheDocument();
    await waitFor(() => expect(fetchProjectTaskLinks).toHaveBeenCalledTimes(1));

    act(() => {
      useStore.getState().dispatchSSEEvent("job.completed", {
        jobId: "job-live",
        resolution: "merged",
      });
    });

    expect(await screen.findByLabelText("Task recipe: live-task — completed")).toBeInTheDocument();
    expect(fetchProjectTaskLinks).toHaveBeenCalledTimes(2);
  });

  it("reconciles an auto-spawned TaskLink when its new Job enters the store", async () => {
    const readyLink = makeTaskLink({ id: "tl-auto", storyNodeId: "auto-task" });
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks)
      .mockResolvedValueOnce({ items: [readyLink] } as any)
      .mockResolvedValueOnce({
        items: [{ ...readyLink, state: "running", jobId: "job-auto" }],
      } as any);

    renderBoard();
    expect(await screen.findByLabelText("Task recipe: auto-task — ready")).toBeInTheDocument();
    await waitFor(() => expect(fetchProjectTaskLinks).toHaveBeenCalledTimes(1));

    act(() => {
      useStore.setState((state) => ({
        jobs: {
          ...state.jobs,
          "job-auto": makeJob({ id: "job-auto", state: "preparing" }),
        },
      }));
    });

    expect(await screen.findByLabelText("Task recipe: auto-task — running")).toHaveTextContent("job-auto");
    expect(fetchProjectTaskLinks).toHaveBeenCalledTimes(2);
  });

  it("starts a ready root task through the TaskLink API", async () => {
    const ready = makeTaskLink({ id: "tl-ready", storyNodeId: "root", state: "ready" });
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(fetchProjectTaskLinks).mockResolvedValue({ items: [ready] } as any);
    vi.mocked(startTaskLink).mockResolvedValueOnce({ ...ready, state: "running", jobId: "job-new" } as any);
    renderBoard();

    await screen.findByRole("button", { name: "Start task" });
    const tasks = screen.queryByRole("region", { name: "Tasks", exact: true });
    expect(tasks).not.toBeNull();
    fireEvent.click(within(tasks!).getByRole("button", { name: "Start task" }));
    await waitFor(() => expect(startTaskLink).toHaveBeenCalledWith("proj-1", "tl-ready"));
    expect(await screen.findByText("job-new")).toBeInTheDocument();
  });

  it("ingests the Project task graph from the board", async () => {
    vi.mocked(fetchJobs).mockResolvedValueOnce({ items: [], cursor: null } as any);
    vi.mocked(ingestProjectTasks).mockResolvedValueOnce({
      items: [makeTaskLink({ storyNodeId: "ingested-task" })],
    } as any);
    renderBoard();

    fireEvent.click(await screen.findByRole("button", { name: "Ingest tasks" }));
    await waitFor(() => expect(ingestProjectTasks).toHaveBeenCalledWith("proj-1"));
    expect(await screen.findByText("ingested-task")).toBeInTheDocument();
  });
});


