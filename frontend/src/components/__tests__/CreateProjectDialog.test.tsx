/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";

vi.mock("../../api/client", () => ({
  createProject: vi.fn(),
  registerRepo: vi.fn(),
  createRepo: vi.fn(),
  unregisterRepo: vi.fn(),
  browseDirectories: vi.fn(),
}));

import { createProject, registerRepo, createRepo, browseDirectories, unregisterRepo } from "../../api/client";
import { CreateProjectDialog } from "../CreateProjectDialog";

beforeEach(() => {
  vi.mocked(createProject).mockReset();
  vi.mocked(registerRepo).mockReset();
  vi.mocked(createRepo).mockReset();
  vi.mocked(unregisterRepo).mockReset();
  vi.mocked(unregisterRepo).mockResolvedValue(undefined);
  vi.mocked(browseDirectories).mockReset();
  vi.mocked(browseDirectories).mockResolvedValue({
    current: "/home/user",
    parent: "/home",
    items: [
      { name: "repo-a", path: "/home/user/repo-a", isGitRepo: true },
      { name: "not-a-repo", path: "/home/user/not-a-repo", isGitRepo: false },
    ],
  });
});

async function selectRowPath(name: string) {
  const row = (await screen.findByText(name)).closest("div");
  if (!row) throw new Error(`Could not find row for ${name}`);
  fireEvent.click(within(row).getByRole("button", { name: /use|selected/i }));
}

async function selectCurrentFolder() {
  const current = await screen.findByText("Current folder");
  const row = current.parentElement;
  if (!row) throw new Error("Could not find current folder row");
  fireEvent.click(within(row).getByRole("button", { name: "Use" }));
}

describe("CreateProjectDialog", () => {
  it("blocks creation with an error when no name is entered", async () => {
    render(<CreateProjectDialog open onClose={() => {}} onCreated={vi.fn()} />);

    await selectRowPath("repo-a");
    fireEvent.click(screen.getByRole("button", { name: "Add repository" }));
    fireEvent.click(screen.getByRole("button", { name: "Create Project" }));

    expect(await screen.findByText("Project name is required.")).toBeInTheDocument();
    expect(createProject).not.toHaveBeenCalled();
  });

  it("blocks creation with an error when no member repository is added", async () => {
    render(<CreateProjectDialog open onClose={() => {}} onCreated={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "My Project" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Project" }));

    expect(
      await screen.findByText("Add at least one member repository before creating the Project."),
    ).toBeInTheDocument();
    expect(createProject).not.toHaveBeenCalled();
  });

  it("creates a project from an added existing repository", async () => {
    vi.mocked(createProject).mockResolvedValueOnce({
      id: "proj-1",
      name: "My Project",
      repoPaths: ["/home/user/repo-a"],
    } as any);
    const onCreated = vi.fn();
    render(<CreateProjectDialog open onClose={() => {}} onCreated={onCreated} />);

    await selectRowPath("repo-a");
    fireEvent.click(screen.getByRole("button", { name: "Add repository" }));
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "My Project" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Project" }));

    await waitFor(() =>
      expect(createProject).toHaveBeenCalledWith({
        name: "My Project",
        repoPaths: ["/home/user/repo-a"],
      }),
    );
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
  });

  it("clones a repository into an explicit destination and adds it to the member list", async () => {
    vi.mocked(registerRepo).mockResolvedValueOnce({
      path: "/home/user/cloned-repo",
      source: "https://example.com/repo.git",
      cloned: true,
      registered: true,
    });
    render(<CreateProjectDialog open onClose={() => {}} onCreated={vi.fn()} />);

    fireEvent.click(screen.getAllByRole("button", { name: "Clone repository" })[0]!);
    fireEvent.change(screen.getByLabelText("Repository URL"), {
      target: { value: "https://example.com/repo.git" },
    });
    await selectCurrentFolder();
    fireEvent.click(screen.getAllByRole("button", { name: "Clone repository" })[1]!);

    await waitFor(() =>
      expect(registerRepo).toHaveBeenCalledWith(
        "https://example.com/repo.git",
        "/home/user/repo",
        "clone",
      ),
    );
    expect(await screen.findByText("/home/user/cloned-repo")).toBeInTheDocument();
  });

  it("creates a local repository from a selected parent directory", async () => {
    vi.mocked(createRepo).mockResolvedValueOnce({ path: "/home/user/new-repo", name: "new-repo" });
    render(<CreateProjectDialog open onClose={() => {}} onCreated={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Create local repository" }));
    await selectCurrentFolder();
    fireEvent.change(screen.getByLabelText("Repository name"), { target: { value: "new-repo" } });
    fireEvent.click(screen.getByRole("button", { name: "Create repository" }));

    await waitFor(() => expect(createRepo).toHaveBeenCalledWith("/home/user", "new-repo"));
    expect(await screen.findByText("/home/user/new-repo")).toBeInTheDocument();
  });

  it("surfaces an error and does not call onCreated when project creation fails", async () => {
    vi.mocked(createProject).mockRejectedValueOnce(new Error("boom"));
    const onCreated = vi.fn();
    render(<CreateProjectDialog open onClose={() => {}} onCreated={onCreated} />);

    await selectRowPath("repo-a");
    fireEvent.click(screen.getByRole("button", { name: "Add repository" }));
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "My Project" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Project" }));

    expect(await screen.findByText(/boom/)).toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("rolls back staged registration when creation fails and reports retained files", async () => {
    vi.mocked(registerRepo).mockResolvedValueOnce({
      path: "/home/user/cloned-repo",
      source: "https://example.com/repo.git",
      cloned: true,
      registered: true,
    });
    vi.mocked(createProject).mockRejectedValueOnce(new Error("Project save failed"));
    render(<CreateProjectDialog open onClose={() => {}} onCreated={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Clone repository" }));
    fireEvent.change(screen.getByLabelText("Repository URL"), {
      target: { value: "https://example.com/repo.git" },
    });
    await selectCurrentFolder();
    fireEvent.click(screen.getAllByRole("button", { name: "Clone repository" })[1]!);
    await screen.findByText("/home/user/cloned-repo");
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "My Project" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Project" }));

    await waitFor(() => expect(unregisterRepo).toHaveBeenCalledWith("/home/user/cloned-repo"));
    expect(
      await screen.findByText(/Repository files remain at: \/home\/user\/cloned-repo/),
    ).toBeInTheDocument();
  });

  it("does not compensate an existing repository path when project creation fails", async () => {
    vi.mocked(createProject).mockRejectedValueOnce(new Error("Project save failed"));
    render(<CreateProjectDialog open onClose={() => {}} onCreated={vi.fn()} />);

    await selectRowPath("repo-a");
    fireEvent.click(screen.getByRole("button", { name: "Add repository" }));
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "My Project" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Project" }));

    expect(await screen.findByText(/Project save failed/)).toBeInTheDocument();
    expect(unregisterRepo).not.toHaveBeenCalled();
  });
});
