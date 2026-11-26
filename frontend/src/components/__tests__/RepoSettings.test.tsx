import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import type {
  ProjectResponse,
  RepoDetailResponse,
  TrackerLinkListResponse,
  TrackerLinkResponse,
} from "../../api/types";

vi.mock("../../api/client", () => ({
  request: vi.fn().mockResolvedValue({}),
  fetchProject: vi.fn(),
  fetchRepoDetail: vi.fn(),
  fetchCredentials: vi.fn(),
  fetchTrackerLinks: vi.fn(),
  createTrackerLink: vi.fn(),
  detachTrackerLink: vi.fn(),
  updateProject: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import {
  createTrackerLink,
  detachTrackerLink,
  fetchCredentials,
  fetchProject,
  fetchRepoDetail,
  fetchTrackerLinks,
  updateProject,
} from "../../api/client";
import { RepoSettings } from "../RepoSettings";

describe("RepoSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each(["github", "jira", "azure_devops", null] as const)(
    "excludes Copilot credentials while preserving tracker selection for %s",
    async (provider) => {
      vi.mocked(fetchProject).mockResolvedValue({
        id: "project-1", name: "Alpha project", repoPaths: ["/repo/app"],
        createdAt: "2024-01-01T00:00:00Z", updatedAt: "2024-01-02T00:00:00Z",
      });
      const inferenceCredential = {
        id: "inference-1", provider: "copilot", label: "Inference only",
        baseUrl: "", email: null, requiresEmailUpdate: false, createdAt: "2024-01-01T00:00:00Z",
      };
      vi.mocked(fetchCredentials).mockResolvedValue({
        credentials: [
          inferenceCredential,
          ...(provider ? [{ ...inferenceCredential, id: "tracker-1", provider, label: "Tracker account" }] : []),
        ],
      });
      vi.mocked(fetchTrackerLinks).mockResolvedValue({ trackerLinks: [] });
      vi.mocked(createTrackerLink).mockResolvedValue({
        id: "link-1", projectId: "project-1", credentialId: "tracker-1",
        externalRef: "acme/1", createdAt: "2024-01-01T00:00:00Z", summary: null,
      });
      render(
        <MemoryRouter initialEntries={["/projects/id/project-1"]}>
          <Routes>
            <Route path="/projects/id/:projectId" element={<RepoSettings />} />
          </Routes>
        </MemoryRouter>,
      );
      await screen.findByText("Project Settings");
      expect(screen.queryByRole("option", { name: "Inference only" })).not.toBeInTheDocument();
      if (provider) {
        expect(screen.getByLabelText("Select tracker credential")).toHaveValue("tracker-1");
        fireEvent.change(screen.getByLabelText("Board or org ref"), { target: { value: "acme/1" } });
        fireEvent.click(screen.getByRole("button", { name: /attach/i }));
        await waitFor(() => expect(createTrackerLink).toHaveBeenCalledWith(
          "project-1", { credentialId: "tracker-1", externalRef: "acme/1" },
        ));
      } else {
        expect(screen.queryByLabelText("Select tracker credential")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /attach/i })).not.toBeInTheDocument();
        expect(createTrackerLink).not.toHaveBeenCalled();
      }
    },
  );

  it("allows editing the owning project membership and saves it", async () => {
    const onProjectUpdated = vi.fn();
    vi.mocked(fetchProject).mockResolvedValue({
      id: "project-1",
      name: "Alpha project",
      repoPaths: ["/repo/app", "/repo/shared"],
      createdAt: "2024-01-01T00:00:00Z",
      updatedAt: "2024-01-02T00:00:00Z",
    } as unknown as ProjectResponse);

    vi.mocked(fetchRepoDetail).mockResolvedValue({
      path: "/repo/app",
      originUrl: "https://github.com/example/app",
      baseBranch: "main",
      currentBranch: "feature/test",
      platform: "github",
      activeJobCount: 2,
      recentJobs: [],
    } as unknown as RepoDetailResponse);

    vi.mocked(fetchCredentials).mockResolvedValue({
      credentials: [{
        id: "cred-1",
        provider: "github",
        label: "Alpha GitHub",
        baseUrl: "https://api.github.com",
        email: null,
        requiresEmailUpdate: false,
        createdAt: "2024-01-01T00:00:00Z",
      }],
    } as unknown as Awaited<ReturnType<typeof fetchCredentials>>);

    vi.mocked(fetchTrackerLinks).mockResolvedValue({
      trackerLinks: [{
        id: "link-1",
        projectId: "project-1",
        credentialId: "cred-1",
        externalRef: "Alpha board",
        createdAt: "2024-01-01T00:00:00Z",
        summary: null,
      }],
    } as unknown as TrackerLinkListResponse);

    vi.mocked(createTrackerLink).mockResolvedValue({
      id: "link-2",
      projectId: "project-1",
      credentialId: "cred-1",
      externalRef: "Alpha board",
      createdAt: "2024-01-01T00:00:00Z",
      summary: null,
    } as unknown as TrackerLinkResponse);

    vi.mocked(updateProject).mockResolvedValue({
      id: "project-1",
      name: "Alpha project v2",
      repoPaths: ["/repo/app", "/repo/shared", "/repo/new-tool"],
      createdAt: "2024-01-01T00:00:00Z",
      updatedAt: "2024-01-03T00:00:00Z",
    } as unknown as ProjectResponse);

    render(
      <MemoryRouter initialEntries={["/projects/id/project-1"]}>
        <Routes>
          <Route element={<Outlet context={{ onProjectUpdated }} />}>
            <Route path="/projects/id/:projectId" element={<RepoSettings />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Project Settings")).toBeInTheDocument();
    });

    expect(screen.getByText("Integrations & board sync")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /manage integrations/i })).toHaveAttribute("href", "/settings");
    expect(screen.getByLabelText(/select tracker credential/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/board or org ref/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Detach Alpha board" }));
    expect(await screen.findByText("Detach tracker link?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Detach tracker link" }));
    await waitFor(() => expect(detachTrackerLink).toHaveBeenCalledWith("project-1", "link-1"));

    fireEvent.change(screen.getByLabelText(/board or org ref/i), { target: { value: "Alpha board v2" } });
    fireEvent.click(screen.getByRole("button", { name: /attach/i }));

    await waitFor(() => {
      expect(createTrackerLink).toHaveBeenCalledWith("project-1", {
        credentialId: "cred-1",
        externalRef: "Alpha board v2",
      });
    });

    const nameInput = screen.getByDisplayValue("Alpha project");
    fireEvent.change(nameInput, { target: { value: "Alpha project v2" } });

    const addInput = screen.getByPlaceholderText("/absolute/path/to/repo");
    fireEvent.change(addInput, { target: { value: "/repo/new-tool" } });
    fireEvent.click(screen.getByRole("button", { name: /add/i }));

    fireEvent.click(screen.getByRole("button", { name: /save project/i }));

    await waitFor(() => {
      expect(updateProject).toHaveBeenCalledWith("project-1", {
        name: "Alpha project v2",
        repoPaths: ["/repo/app", "/repo/shared", "/repo/new-tool"],
        confirmRepoRemoval: false,
      });
      expect(onProjectUpdated).toHaveBeenCalledWith(expect.objectContaining({
        id: "project-1",
        repoPaths: ["/repo/app", "/repo/shared", "/repo/new-tool"],
      }));
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove /repo/shared" }));
    fireEvent.click(screen.getByRole("button", { name: /save project/i }));

    expect(await screen.findByText("Remove repositories from this Project?")).toBeInTheDocument();
    expect(screen.getByText(/Historical Jobs remain in Job History/)).toBeInTheDocument();
  });

  it("clears the previous Project when navigation fails", async () => {
    vi.mocked(fetchProject)
      .mockResolvedValueOnce({
        id: "project-1",
        name: "Alpha project",
        repoPaths: ["/repo/app"],
        createdAt: "2024-01-01T00:00:00Z",
        updatedAt: "2024-01-02T00:00:00Z",
      } as unknown as ProjectResponse)
      .mockRejectedValueOnce(new Error("missing"));
    vi.mocked(fetchCredentials).mockResolvedValue({ credentials: [] });
    vi.mocked(fetchTrackerLinks).mockResolvedValue({ trackerLinks: [] } as TrackerLinkListResponse);

    render(
      <MemoryRouter initialEntries={["/projects/id/project-1"]}>
        <Routes>
          <Route
            path="/projects/id/:projectId"
            element={(
              <>
                <Link to="/projects/id/missing">Open missing Project</Link>
                <RepoSettings />
              </>
            )}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Alpha project")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Open missing Project" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load Project settings");
    expect(screen.queryByText("Alpha project")).not.toBeInTheDocument();
  });

  it("ignores a stale Project response after navigating to another Project", async () => {
    let resolveFirst!: (project: ProjectResponse) => void;
    const firstProject = new Promise<ProjectResponse>((resolve) => {
      resolveFirst = resolve;
    });
    vi.mocked(fetchProject)
      .mockReturnValueOnce(firstProject)
      .mockResolvedValueOnce({
        id: "project-2",
        name: "Current project",
        repoPaths: ["/repo/current"],
        createdAt: "2024-01-01T00:00:00Z",
        updatedAt: "2024-01-02T00:00:00Z",
      } as ProjectResponse);
    vi.mocked(fetchCredentials).mockResolvedValue({ credentials: [] });
    vi.mocked(fetchTrackerLinks).mockResolvedValue({ trackerLinks: [] } as TrackerLinkListResponse);
    vi.mocked(updateProject).mockResolvedValue({
      id: "project-2",
      name: "Current project updated",
      repoPaths: ["/repo/current"],
      createdAt: "2024-01-01T00:00:00Z",
      updatedAt: "2024-01-03T00:00:00Z",
    } as ProjectResponse);

    render(
      <MemoryRouter initialEntries={["/projects/id/project-1"]}>
        <Routes>
          <Route
            path="/projects/id/:projectId"
            element={(
              <>
                <Link to="/projects/id/project-2">Open current Project</Link>
                <RepoSettings />
              </>
            )}
          />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("link", { name: "Open current Project" }));
    expect(await screen.findByText("Current project")).toBeInTheDocument();

    resolveFirst({
      id: "project-1",
      name: "Stale project",
      repoPaths: ["/repo/stale"],
      createdAt: "2024-01-01T00:00:00Z",
      updatedAt: "2024-01-02T00:00:00Z",
    } as ProjectResponse);

    await waitFor(() => expect(fetchProject).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Current project")).toBeInTheDocument();
    expect(screen.queryByText("Stale project")).not.toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("Current project"), {
      target: { value: "Current project updated" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save project/i }));
    await waitFor(() => expect(updateProject).toHaveBeenCalledWith(
      "project-2",
      expect.objectContaining({ name: "Current project updated" }),
    ));
  });
});
