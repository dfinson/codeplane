/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Mock the API client
vi.mock("../../api/client", () => ({
  fetchSettings: vi.fn(),
  updateSettings: vi.fn(),
  fetchRepos: vi.fn(),
  unregisterRepo: vi.fn(),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock AddRepoModal
vi.mock("../AddRepoModal", () => ({
  AddRepoModal: () => null,
}));

import { fetchSettings, fetchRepos, updateSettings } from "../../api/client";
import { SettingsScreen } from "../SettingsScreen";

const defaultSettings = {
  maxConcurrentJobs: 2,
  permissionMode: "auto",
  autoPush: true,
  cleanupWorktree: true,
  deleteBranchAfterMerge: true,
  artifactRetentionDays: 30,
  maxArtifactSizeMb: 100,
  autoArchiveDays: 90,
  verify: false,
  selfReview: false,
  maxTurns: 3,
  verifyPrompt: "",
  selfReviewPrompt: "",
};

beforeEach(() => {
  vi.mocked(fetchSettings).mockResolvedValue(defaultSettings as any);
  vi.mocked(fetchRepos).mockResolvedValue({ items: ["/repos/my-app"] } as any);
  vi.mocked(updateSettings).mockReset();
});

describe("SettingsScreen", () => {
  it("renders Settings heading after loading", async () => {
    render(
      <MemoryRouter>
        <SettingsScreen />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument();
    });
  });

  it("loads and displays repos", async () => {
    render(
      <MemoryRouter>
        <SettingsScreen />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("/repos/my-app")).toBeInTheDocument();
    });
  });

  it("displays Repositories section with count", async () => {
    render(
      <MemoryRouter>
        <SettingsScreen />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("Repositories (1)")).toBeInTheDocument();
    });
  });

  it("displays Runtime section", async () => {
    render(
      <MemoryRouter>
        <SettingsScreen />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("Runtime")).toBeInTheDocument();
    });
  });

  it("displays Verification section", async () => {
    render(
      <MemoryRouter>
        <SettingsScreen />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("Verification")).toBeInTheDocument();
    });
  });

  it("shows error toast when settings fail to load", async () => {
    const { toast } = await import("sonner");
    vi.mocked(fetchSettings).mockRejectedValueOnce(new Error("fail"));
    vi.mocked(fetchRepos).mockRejectedValueOnce(new Error("fail"));
    render(
      <MemoryRouter>
        <SettingsScreen />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Failed to load settings");
    });
  });
});
