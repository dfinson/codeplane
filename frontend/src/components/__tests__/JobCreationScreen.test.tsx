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
  fetchRepoDetail: vi.fn(),
  suggestNames: vi.fn(),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
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

vi.mock("../ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
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

import {
  createJob,
  fetchModels,
  fetchRepoDetail,
  fetchRepos,
  fetchSDKs,
  fetchSettings,
  suggestNames,
} from "../../api/client";
import { JobCreationScreen } from "../JobCreationScreen";
import { useStore } from "../../store";

async function renderScreen() {
  // Simulate App.tsx calling initSdksAndModels on mount, so the store is
  // pre-populated with SDK + model data before the component renders.
  await useStore.getState().initSdksAndModels();

  render(
    <MemoryRouter>
      <JobCreationScreen />
    </MemoryRouter>,
  );

  await waitFor(() => {
    expect(fetchSettings).toHaveBeenCalled();
    expect(fetchRepos).toHaveBeenCalled();
  });
}

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

beforeEach(() => {
  vi.mocked(createJob).mockReset();
  vi.mocked(fetchRepos).mockResolvedValue({ items: ["/repos/my-app"] } as any);
  vi.mocked(fetchModels).mockResolvedValue([
    { id: "gpt-5.4-mini", name: "GPT-5.4 Mini" },
    { id: "gpt-5.4", name: "GPT-5.4", default: true },
  ]);
  vi.mocked(fetchSDKs).mockResolvedValue({ default: "copilot", sdks: [] });
  vi.mocked(fetchRepoDetail).mockResolvedValue({ currentBranch: "main", baseBranch: "main" } as any);
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
  vi.mocked(suggestNames).mockResolvedValue({
    title: "Fix the bug",
    branchName: "fix-the-bug",
    worktreeName: "fix-the-bug",
  } as any);
  mockNavigate.mockReset();

  // Reset the store's SDK/model catalogue so each test starts fresh
  useStore.setState({
    sdks: [],
    defaultSdk: null,
    sdksLoading: true,
    modelsBySdk: {},
    defaultModelBySdk: {},
    modelsLoadingBySdk: {},
  });
});

describe("JobCreationScreen", () => {
  it("renders New Job heading", async () => {
    await renderScreen();
    expect(screen.getByText("New Job")).toBeInTheDocument();
  });

  it("renders Repository label", async () => {
    await renderScreen();
    expect(screen.getByText("Repository")).toBeInTheDocument();
  });

  it("renders Create Job button", async () => {
    await renderScreen();
    expect(screen.getByText("Create Job")).toBeInTheDocument();
  });

  it("renders permission mode buttons", async () => {
    await renderScreen();
    expect(screen.getByText("Full Auto")).toBeInTheDocument();
    expect(screen.getByText("Review & Approve")).toBeInTheDocument();
  });

  it("renders model selection in the main form", async () => {
    await renderScreen();
    expect(screen.getByText("Model")).toBeInTheDocument();
    expect(screen.queryByText("Advanced options")).toBeInTheDocument();
  });

  it("submits a job", async () => {
    vi.mocked(createJob).mockResolvedValueOnce({ id: "j-new" } as any);
    await renderScreen();

    const textarea = screen.getByTestId("prompt-input");
    fireEvent.change(textarea, { target: { value: "Fix the bug" } });

    fireEvent.click(screen.getByText("Create Job"));

    await waitFor(() => {
      expect(createJob).toHaveBeenCalled();
    });
  });

  it("uses the configured permission mode by default", async () => {
    vi.mocked(createJob).mockResolvedValueOnce({ id: "j-auto" } as any);

    await renderScreen();

    fireEvent.change(screen.getByTestId("prompt-input"), { target: { value: "Ship it" } });
    fireEvent.click(screen.getByText("Create Job"));

    await waitFor(() => {
      expect(createJob).toHaveBeenCalledWith(
        expect.objectContaining({ permission_mode: "auto" }),
      );
    });
  });

  it("uses the SDK's actual default model", async () => {
    vi.mocked(createJob).mockResolvedValueOnce({ id: "j-model" } as any);

    await renderScreen();

    expect(screen.getByTestId("combo-Model")).toHaveValue("gpt-5.4");

    fireEvent.change(screen.getByTestId("prompt-input"), { target: { value: "Use the default model" } });
    fireEvent.click(screen.getByText("Create Job"));

    await waitFor(() => {
      expect(createJob).toHaveBeenCalledWith(
        expect.objectContaining({ model: "gpt-5.4" }),
      );
    });
  });
});
