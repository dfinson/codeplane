/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Mock API client
vi.mock("../../api/client", () => ({
  createJob: vi.fn(),
  fetchProjects: vi.fn(),
  fetchCredentials: vi.fn().mockResolvedValue({ credentials: [] }),
  fetchModels: vi.fn(),
  fetchSDKs: vi.fn(),
  fetchSettings: vi.fn(),
  fetchPolicySettings: vi.fn(),
  fetchRepoDetail: vi.fn(),
  suggestNames: vi.fn(),
  fetchModelComparison: vi.fn().mockResolvedValue({ models: [] }),
  warmUtilitySession: vi.fn().mockResolvedValue(undefined),
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

vi.mock("../ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// Mock Combobox to render a simple select
vi.mock("../ui/combobox", () => ({
  Combobox: ({ label, items, value, onChange, disabled }: any) => (
    <div>
      <label>{label}</label>
      <select
        data-testid={`combo-${label}`}
        value={value ?? ""}
        disabled={disabled}
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
  fetchProjects,
  fetchCredentials,
  fetchSDKs,
  fetchSettings,
  fetchPolicySettings,
  suggestNames,
} from "../../api/client";
import { JobCreationScreen } from "../JobCreationScreen";
import { useStore, _resetSdkInitForTesting } from "../../store";

async function renderScreen(route = "/jobs/new") {
  // Simulate App.tsx calling initSdksAndModels on mount, so the store is
  // pre-populated with SDK + model data before the component renders.
  await useStore.getState().initSdksAndModels();

  render(
    <MemoryRouter initialEntries={[route]}>
      <JobCreationScreen />
    </MemoryRouter>,
  );

  await waitFor(() => {
    expect(fetchPolicySettings).toHaveBeenCalled();
    expect(fetchProjects).toHaveBeenCalled();
  });
}

function selectRepository() {
  fireEvent.change(screen.getByTestId("combo-Repository"), {
    target: { value: "/repos/my-app" },
  });
}

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

beforeEach(() => {
  vi.mocked(createJob).mockReset();
  vi.mocked(fetchCredentials).mockResolvedValue({ credentials: [] });
  vi.mocked(fetchProjects).mockResolvedValue({ items: [{ id: "p1", name: "My Project", repoPaths: ["/repos/my-app"] }] } as any);
  vi.mocked(fetchModels).mockResolvedValue([
    { id: "gpt-5.4-mini", name: "GPT-5.4 Mini" },
    { id: "gpt-5.4", name: "GPT-5.4", default: true },
  ]);
  vi.mocked(fetchSDKs).mockResolvedValue({ default: "copilot", sdks: [] });
  vi.mocked(fetchRepoDetail).mockResolvedValue({ currentBranch: "main", baseBranch: "main" } as any);
  vi.mocked(fetchPolicySettings).mockResolvedValue({
    config: { preset: "autonomous" },
  } as any);
  vi.mocked(fetchSettings).mockResolvedValue({
    maxConcurrentJobs: 2,
    autoPush: true,
    cleanupWorktree: true,
    deleteBranchAfterMerge: true,
    artifactRetentionDays: 30,
    maxArtifactSizeMb: 100,
    autoArchiveDays: 90,
    maxTurns: 3,
  } as any);
  vi.mocked(suggestNames).mockResolvedValue({
    title: "Fix the bug",
    branchName: "fix-the-bug",
    worktreeName: "fix-the-bug",
  } as any);
  mockNavigate.mockReset();

  // Reset the store's SDK/model catalogue and init guard so each test starts fresh
  _resetSdkInitForTesting();
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

  it("uses the chosen account for catalog, naming, and creation", async () => {
    vi.mocked(fetchCredentials).mockResolvedValue({ credentials: [{
      id: "account-a", provider: "copilot", label: "Personal inference", baseUrl: "https://github.com",
      createdAt: "2026-01-01", email: null, requiresEmailUpdate: false,
    }] });
    vi.mocked(createJob).mockResolvedValue({ id: "selected-job" } as any);
    await renderScreen();
    selectRepository();
    await screen.findByRole("option", { name: "Personal inference" });
    fireEvent.change(screen.getByLabelText("Copilot inference account"), { target: { value: "account-a" } });
    await waitFor(() => expect(fetchModels).toHaveBeenCalledWith("copilot", "account-a"));
    fireEvent.change(screen.getByTestId("prompt-input"), { target: { value: "Fix bug" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Create Job" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Create Job" }));
    await waitFor(() => expect(createJob).toHaveBeenCalledWith(expect.objectContaining({ copilotCredentialId: "account-a" })));
    expect(suggestNames).toHaveBeenCalledWith("Fix bug", "account-a");
  });

  it("does not fall back to ambient models when selected account discovery fails", async () => {
    vi.mocked(fetchCredentials).mockResolvedValue({ credentials: [{
      id: "account-a", provider: "copilot", label: "Unavailable", baseUrl: "https://github.com",
      createdAt: "2026-01-01", email: null, requiresEmailUpdate: false,
    }] });
    await renderScreen();
    selectRepository();
    vi.mocked(fetchModels).mockRejectedValueOnce(new Error("revoked"));
    await screen.findByRole("option", { name: "Unavailable" });
    fireEvent.change(screen.getByLabelText("Copilot inference account"), { target: { value: "account-a" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("No ambient fallback");
    fireEvent.change(screen.getByTestId("prompt-input"), { target: { value: "Fix bug" } });
    expect(screen.getByRole("button", { name: "Create Job" })).toBeDisabled();
    expect(screen.queryByRole("option", { name: "GPT-5.4" })).not.toBeInTheDocument();
  });

  it("renders Repository label", async () => {
    await renderScreen();
    expect(screen.getByText("Repository")).toBeInTheDocument();
  });

  it("prefills and locks the repository for a single-repo Project flow", async () => {
    vi.mocked(createJob).mockResolvedValueOnce({ id: "j-project" } as any);

    await renderScreen("/jobs/new?projectId=p1&repo=%2Frepos%2Fmy-app");

    expect(screen.getByTestId("combo-Repository")).toHaveValue("/repos/my-app");
    expect(screen.getByTestId("combo-Repository")).toBeDisabled();
    expect(screen.getByText(/Creating this Job in Project/i)).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("prompt-input"), { target: { value: "Fix the bug" } });
    fireEvent.click(screen.getByText("Create Job"));

    await waitFor(() => {
      expect(createJob).toHaveBeenCalledWith(expect.objectContaining({
        projectId: "p1",
        repo: "/repos/my-app",
      }));
    });
  });

  it("scopes repository choices to the requested multi-repo Project without auto-selecting one", async () => {
    vi.mocked(fetchProjects).mockResolvedValueOnce({
      items: [
        { id: "p1", name: "My Project", repoPaths: ["/repos/api", "/repos/web"] },
        { id: "p2", name: "Other Project", repoPaths: ["/repos/other"] },
      ],
    } as any);

    await renderScreen("/jobs/new?projectId=p1");

    const select = screen.getByTestId("combo-Repository");
    expect(select).toHaveValue("");
    expect(screen.getByText(/will not select one automatically/i)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "My Project · api" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "My Project · web" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Other Project · other" })).not.toBeInTheDocument();
  });

  it("requires an explicit repository selection", async () => {
    await renderScreen();

    expect(screen.getByTestId("combo-Repository")).toHaveValue("");
    expect(screen.getByText(/will not select one automatically/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create Job" })).toBeDisabled();
  });

  it("renders Create Job button", async () => {
    await renderScreen();
    selectRepository();
    expect(screen.getByText("Create Job")).toBeInTheDocument();
  });

  it("renders permission mode buttons", async () => {
    await renderScreen();
    selectRepository();
    expect(screen.getByText("Autonomous")).toBeInTheDocument();
    expect(screen.getByText("Supervised")).toBeInTheDocument();
  });

  it("renders model selection in the main form", async () => {
    await renderScreen();
    selectRepository();
    expect(screen.getByText("Model")).toBeInTheDocument();
    expect(screen.queryByText("Advanced options")).toBeInTheDocument();
  });

  it("submits a job", async () => {
    vi.mocked(createJob).mockResolvedValueOnce({ id: "j-new" } as any);
    await renderScreen();
    selectRepository();

    const textarea = screen.getByTestId("prompt-input");
    fireEvent.change(textarea, { target: { value: "Fix the bug" } });

    fireEvent.click(screen.getByText("Create Job"));

    await waitFor(() => {
      expect(createJob).toHaveBeenCalled();
    });
  });

  it("uses the configured preset by default", async () => {
    vi.mocked(createJob).mockResolvedValueOnce({ id: "j-auto" } as any);

    await renderScreen();
    selectRepository();

    fireEvent.change(screen.getByTestId("prompt-input"), { target: { value: "Ship it" } });
    fireEvent.click(screen.getByText("Create Job"));

    await waitFor(() => {
      expect(createJob).toHaveBeenCalledWith(
        expect.objectContaining({ preset: "autonomous" }),
      );
    });
  });

  it("uses the SDK's actual default model", async () => {
    vi.mocked(createJob).mockResolvedValueOnce({ id: "j-model" } as any);

    await renderScreen();
    selectRepository();

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
