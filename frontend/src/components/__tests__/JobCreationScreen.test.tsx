/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Mock API client
vi.mock("../../api/client", () => ({
  createJob: vi.fn(),
  fetchRepos: vi.fn(),
  fetchModels: vi.fn(),
  fetchSDKs: vi.fn(),
  fetchSettings: vi.fn(),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock VoiceButton to avoid audio deps
vi.mock("../VoiceButton", () => ({
  PromptWithVoice: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <textarea
      data-testid="prompt-input"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

// Mock AddRepoModal
vi.mock("../AddRepoModal", () => ({
  AddRepoModal: () => null,
}));

// Mock Combobox to render a simple select
vi.mock("../ui/combobox", () => ({
  Combobox: ({ label, items, value, onChange }: any) => (
    <div>
      <label>{label}</label>
      <select
        data-testid={`combo-${label}`}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">Select…</option>
        {items?.map((item: any) => (
          <option key={item.value} value={item.value}>{item.label}</option>
        ))}
      </select>
    </div>
  ),
}));

import { createJob, fetchRepos, fetchModels, fetchSDKs, fetchSettings } from "../../api/client";
import { JobCreationScreen } from "../JobCreationScreen";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

beforeEach(() => {
  vi.mocked(createJob).mockReset();
  vi.mocked(fetchRepos).mockResolvedValue({ items: ["/repos/my-app"] } as any);
  vi.mocked(fetchModels).mockResolvedValue([]);
  vi.mocked(fetchSDKs).mockResolvedValue({ default: "copilot", sdks: [] });
  vi.mocked(fetchSettings).mockResolvedValue({
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
  } as any);
  mockNavigate.mockReset();
});

describe("JobCreationScreen", () => {
  it("renders New Job heading", async () => {
    render(
      <MemoryRouter>
        <JobCreationScreen />
      </MemoryRouter>,
    );
    expect(screen.getByText("New Job")).toBeInTheDocument();
  });

  it("renders Repository label", async () => {
    render(
      <MemoryRouter>
        <JobCreationScreen />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("Repository")).toBeInTheDocument();
    });
  });

  it("renders Create Job button", async () => {
    render(
      <MemoryRouter>
        <JobCreationScreen />
      </MemoryRouter>,
    );
    expect(screen.getByText("Create Job")).toBeInTheDocument();
  });

  it("renders permission mode buttons", () => {
    render(
      <MemoryRouter>
        <JobCreationScreen />
      </MemoryRouter>,
    );
    expect(screen.getByText("Full Auto")).toBeInTheDocument();
    expect(screen.getByText("Review & Approve")).toBeInTheDocument();
    expect(screen.getByText("Observe Only")).toBeInTheDocument();
  });

  it("submits a job", async () => {
    vi.mocked(createJob).mockResolvedValueOnce({ id: "j-new" } as any);
    render(
      <MemoryRouter>
        <JobCreationScreen />
      </MemoryRouter>,
    );

    // Wait for repos to load
    await waitFor(() => {
      expect(fetchRepos).toHaveBeenCalled();
    });

    // Fill prompt
    const textarea = screen.getByTestId("prompt-input");
    fireEvent.change(textarea, { target: { value: "Fix the bug" } });

    // Click Create Job
    fireEvent.click(screen.getByText("Create Job"));

    await waitFor(() => {
      expect(createJob).toHaveBeenCalled();
    });
  });
});
