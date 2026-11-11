/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import {
  MemoryRouter,
  Routes,
  Route,
  useNavigate,
  useOutletContext,
} from "react-router-dom";

vi.mock("../../api/client", () => ({
  fetchProjects: vi.fn(),
  fetchProjectsSummary: vi.fn(),
}));

import { fetchProjects, fetchProjectsSummary } from "../../api/client";
import { RepoLayout } from "../RepoLayout";
import type { RepoLayoutOutletContext } from "../RepoLayout";

beforeEach(() => {
  vi.mocked(fetchProjects).mockReset();
  vi.mocked(fetchProjectsSummary).mockReset();
  vi.mocked(fetchProjectsSummary).mockResolvedValue({ items: [] } as any);
});

function renderLayout(route = "/projects") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="/projects/id/:projectId/board" element={<div>Project board</div>} />
        <Route path="/projects/id/:projectId/repos/:repoPath/*" element={<RepoLayout />} />
        <Route path="/projects/id/:projectId/*" element={<RepoLayout />} />
        <Route path="/projects/*" element={<RepoLayout />} />
      </Routes>
    </MemoryRouter>,
  );
}

function MembershipUpdater() {
  const { onProjectUpdated } = useOutletContext<RepoLayoutOutletContext>();
  return (
    <button
      onClick={() => onProjectUpdated({
        id: "multi",
        name: "multi-project",
        repoPaths: ["/repos/keep", "/repos/new"],
        createdAt: "",
        updatedAt: "",
      })}
    >
      Apply membership
    </button>
  );
}

function ProjectCreator() {
  const { onProjectCreated } = useOutletContext<RepoLayoutOutletContext>();
  const navigate = useNavigate();
  return (
    <button onClick={() => {
      onProjectCreated({
        id: "created",
        name: "created-project",
        repoPaths: ["/repos/created"],
        createdAt: "",
        updatedAt: "",
      });
      navigate("/projects/id/created/settings");
    }}>
      Create and open
    </button>
  );
}

describe("RepoLayout project sidebar", () => {
  it("removes retired Health navigation while retaining real shell destinations", async () => {
    const repo = "C:\\repos\\api";
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{ id: "single", name: "single-project", repoPaths: [repo], createdAt: "", updatedAt: "" }],
    });
    render(
      <MemoryRouter initialEntries={[`/projects/id/single/repos/${encodeURIComponent(repo)}/jobs`]}>
        <Routes>
          <Route path="/projects" element={<RepoLayout />}>
            <Route path="id/:projectId/board" element={<div>Board destination</div>} />
            <Route path="id/:projectId/chats" element={<div>Chats destination</div>} />
            <Route path="id/:projectId/repos/:repoPath/jobs" element={<div>Jobs destination</div>} />
            <Route path="id/:projectId/repos/:repoPath/cost" element={<div>Cost destination</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Jobs destination")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Health" })).toBeNull();
    for (const destination of ["Cost", "Board", "Chats", "Jobs"]) {
      fireEvent.click(screen.getByRole("button", { name: destination }));
      expect(await screen.findByText(`${destination} destination`)).toBeInTheDocument();
    }
  });

  it("filters Projects by a partial, case-insensitive name match", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [
        { id: "alpha", name: "alpha-project", repoPaths: ["/repos/alpha-project"] },
        { id: "beta", name: "beta-project", repoPaths: ["/repos/beta-project"] },
      ],
    } as any);

    renderLayout();

    await waitFor(() => expect(screen.getByText("alpha-project")).toBeInTheDocument());
    expect(screen.getByText("beta-project")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter projects"), {
      target: { value: "ALPHA" },
    });

    expect(screen.getByText("alpha-project")).toBeInTheDocument();
    expect(screen.queryByText("beta-project")).not.toBeInTheDocument();
  });

  it("shows a no-matches state when the filter matches nothing", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{ id: "alpha", name: "alpha-project", repoPaths: ["/repos/alpha-project"] }],
    } as any);

    renderLayout();

    await waitFor(() => expect(screen.getByText("alpha-project")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Filter projects"), {
      target: { value: "zzz" },
    });

    expect(screen.queryByText("alpha-project")).not.toBeInTheDocument();
    expect(screen.getByText("No matches")).toBeInTheDocument();
  });

  it("does not render a filter box when there are no Projects", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({ items: [] } as any);

    renderLayout();

    await waitFor(() => expect(screen.getByText("No Projects")).toBeInTheDocument());
    expect(screen.queryByLabelText("Filter projects")).not.toBeInTheDocument();
  });

  it("keeps multi-repo projects unselected but shows a disabled repository sub-nav", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{
        id: "multi",
        name: "multi-project",
        repoPaths: ["/repos/api", "/repos/web"],
      }],
    } as any);

    renderLayout("/projects/id/multi");

    const selector = await screen.findByLabelText("Repository");
    expect(selector).toHaveValue("");
    expect(screen.getByText("Select a repository above to view Jobs and Cost.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jobs" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Health" })).toBeNull();
    expect(screen.getByRole("button", { name: "Cost" })).toBeDisabled();
  });

  it("auto-selects the only repository and enables the repository sub-nav", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{ id: "single", name: "single-project", repoPaths: ["/repos/api"] }],
    } as any);

    renderLayout("/projects/id/single");

    await waitFor(() => expect(screen.getByLabelText("Repository")).toHaveValue("/repos/api"));
    expect(screen.queryByText("Select a repository above to view Jobs and Cost.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jobs" })).not.toBeDisabled();
  });

  it("reveals repository basenames through an accessible disclosure", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{ id: "multi", name: "multi-project", repoPaths: ["/repos/api", "/repos/web"] }],
    } as any);

    renderLayout("/projects/id/multi");

    fireEvent.click(await screen.findByRole("button", { name: "Show repositories in this project" }));
    expect(screen.getAllByText("api").length).toBeGreaterThan(0);
    expect(screen.getAllByText("web").length).toBeGreaterThan(0);
  });

  it("labels the project board tab consistently", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{ id: "multi", name: "multi-project", repoPaths: ["/repos/api"] }],
    } as any);

    renderLayout("/projects/id/multi");

    expect(await screen.findByRole("button", { name: "Board" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Agent Runs" })).not.toBeInTheDocument();
  });

  it("labels the project-scoped settings action clearly", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{ id: "multi", name: "multi-project", repoPaths: ["/repos/api"] }],
    } as any);

    render(
      <MemoryRouter initialEntries={["/projects/id/multi"]}>
        <Routes>
          <Route path="/projects" element={<RepoLayout />}>
            <Route path="id/:projectId" element={<div>Overview</div>} />
            <Route path="id/:projectId/settings" element={<div>Project settings page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Project settings" }));
    expect(await screen.findByText("Project settings page")).toBeInTheDocument();
  });

  it("redirects a repository path that is not a member of the active Project", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{
        id: "multi",
        name: "multi-project",
        repoPaths: ["/repos/api"],
      }],
    } as any);

    renderLayout("/projects/id/multi/repos/%2Frepos%2Fother/jobs");

    expect(await screen.findByText("Project board")).toBeInTheDocument();
  });

  it("updates repository navigation from a nested membership save without reloading", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{
        id: "multi",
        name: "multi-project",
        repoPaths: ["/repos/old", "/repos/keep"],
      }],
    } as any);

    render(
      <MemoryRouter initialEntries={["/projects/id/multi/settings"]}>
        <Routes>
          <Route path="/projects" element={<RepoLayout />}>
            <Route path="id/:projectId/settings" element={<MembershipUpdater />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("option", { name: "old" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Apply membership" }));

    expect(screen.queryByRole("option", { name: "old" })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "new" })).toBeInTheDocument();
  });

  it("lets direct project routes mount while the sidebar list resolves project membership", async () => {
    let resolveProjects!: (value: { items: never[] }) => void;
    vi.mocked(fetchProjects).mockReturnValueOnce(new Promise((resolve) => {
      resolveProjects = resolve;
    }));

    render(
      <MemoryRouter initialEntries={["/projects/id/missing/settings"]}>
        <Routes>
          <Route path="/projects" element={<RepoLayout />}>
            <Route path="id/:projectId/settings" element={<div>Unknown child content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.queryByText("Unknown child content")).not.toBeInTheDocument();
    resolveProjects({ items: [] });
    expect(await screen.findByText("No Projects")).toBeInTheDocument();
    expect(screen.getByText("Unknown child content")).toBeInTheDocument();
  });

  it("adds a newly created Project to the shell before navigating to its child", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [{
        id: "seed",
        name: "seed-project",
        repoPaths: ["/repos/seed"],
      }],
    } as any);

    render(
      <MemoryRouter initialEntries={["/projects/id/seed/create-test"]}>
        <Routes>
          <Route path="/projects" element={<RepoLayout />}>
            <Route path="id/:projectId/create-test" element={<ProjectCreator />} />
            <Route path="id/:projectId/settings" element={<div>Created project page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Create and open" }));

    expect(await screen.findByText("Created project page")).toBeInTheDocument();
    expect(screen.getAllByText("created-project")).toHaveLength(2);
  });
});

