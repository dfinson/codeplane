/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock fetch for tests while preserving the original for Vitest internals.
// ---------------------------------------------------------------------------
const originalFetch = globalThis.fetch;
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

import {
  fetchHealth,
  fetchJobs,
  fetchJob,
  fetchJobLogs,
  fetchJobTranscript,
  fetchJobDiff,
  fetchJobTimeline,
  createJob,
  cancelJob,
  rerunJob,
  resolveJob,
  archiveJob,
  pauseJob,
  continueJob,
  resumeJob,
  fetchSettings,
  updateSettings,
  fetchRepos,
  registerRepo,
  unregisterRepo,
  fetchApprovals,
  resolveApproval,
  trustJob,
  fetchArtifacts,
  fetchWorkspaceFiles,
  fetchWorkspaceFile,
  sendOperatorMessage,
  transcribeAudio,
  fetchModels,
  fetchSDKs,
  ApiError,
} from "../client";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function noContentResponse() {
  return { ok: true, status: 204, statusText: "No Content", json: async () => undefined };
}

function getFirstFetchCall(): [input: RequestInfo | URL, init?: RequestInit] {
  const firstCall = mockFetch.mock.calls[0];
  expect(firstCall).toBeDefined();
  return firstCall as [RequestInfo | URL, RequestInit?];
}

function getFirstFetchUrl(): string {
  const [input] = getFirstFetchCall();
  return String(input);
}

function getFirstFetchInit(): RequestInit {
  const [, init] = getFirstFetchCall();
  expect(init).toBeDefined();
  return init as RequestInit;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  // Re-stub fetch for each test
  globalThis.fetch = mockFetch;
  mockFetch.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  // Restore real fetch so Vitest worker communication is not broken
  globalThis.fetch = originalFetch;
});

// ---- ApiError -------------------------------------------------------------

describe("ApiError", () => {
  it("has correct status and detail", () => {
    const err = new ApiError(404, "Not found");
    expect(err.status).toBe(404);
    expect(err.detail).toBe("Not found");
    expect(err.name).toBe("ApiError");
    expect(err.message).toBe("Not found");
  });
});

// ---- Health ---------------------------------------------------------------

describe("fetchHealth", () => {
  it("returns health object", async () => {
    const body = { status: "ok" };
    mockFetch.mockResolvedValueOnce(jsonResponse(body));
    const result = await fetchHealth();
    expect(result).toEqual(body);
    expect(mockFetch).toHaveBeenCalledWith("/api/health", expect.objectContaining({}));
  });
});

// ---- Jobs -----------------------------------------------------------------

describe("fetchJobs", () => {
  it("fetches job list without params", async () => {
    const body = { items: [], cursor: null };
    mockFetch.mockResolvedValueOnce(jsonResponse(body));
    const result = await fetchJobs();
    expect(result.items).toEqual([]);
    expect(mockFetch).toHaveBeenCalledWith("/api/jobs", expect.anything());
  });

  it("appends state and limit query params", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ items: [], cursor: null }));
    await fetchJobs({ state: "running", limit: 10 });
    const url = getFirstFetchUrl();
    expect(url).toContain("state=running");
    expect(url).toContain("limit=10");
  });

  it("appends cursor param", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ items: [], cursor: null }));
    await fetchJobs({ cursor: "abc" });
    const url = getFirstFetchUrl();
    expect(url).toContain("cursor=abc");
  });

  it("appends archived param", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ items: [], cursor: null }));
    await fetchJobs({ archived: true });
    const url = getFirstFetchUrl();
    expect(url).toContain("archived=true");
  });
});

describe("fetchJob", () => {
  it("fetches a single job", async () => {
    const job = { id: "j-1", state: "running" };
    mockFetch.mockResolvedValueOnce(jsonResponse(job));
    const result = await fetchJob("j-1");
    expect(result).toEqual(job);
    expect(mockFetch).toHaveBeenCalledWith("/api/jobs/j-1", expect.anything());
  });

  it("throws ApiError on 404", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ detail: "Job not found" }, 404));
    await expect(fetchJob("missing")).rejects.toThrow(ApiError);
    try {
      mockFetch.mockResolvedValueOnce(jsonResponse({ detail: "Job not found" }, 404));
      await fetchJob("missing");
    } catch (e) {
      expect((e as ApiError).status).toBe(404);
      expect((e as ApiError).detail).toBe("Job not found");
    }
  });
});

describe("createJob", () => {
  it("creates a job successfully", async () => {
    const body = { id: "j-new", state: "queued" };
    mockFetch.mockResolvedValueOnce(jsonResponse(body));
    const result = await createJob({ repo: "/repo", prompt: "Fix it" } as any);
    expect(result).toEqual(body);
    const [url] = getFirstFetchCall();
    const init = getFirstFetchInit();
    expect(url).toBe("/api/jobs");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ repo: "/repo", prompt: "Fix it" });
  });

  it("throws ApiError on 422 validation error", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse(
        { detail: [{ loc: ["body", "prompt"], msg: "Field required", type: "value_error" }] },
        422,
      ),
    );
    await expect(createJob({} as any)).rejects.toThrow(ApiError);
  });

  it("formats 422 array detail correctly", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse(
        { detail: [{ loc: ["body", "prompt"], msg: "Field required" }] },
        422,
      ),
    );
    try {
      await createJob({} as any);
    } catch (e) {
      expect((e as ApiError).detail).toBe("prompt: Field required");
    }
  });
});

describe("cancelJob", () => {
  it("cancels a job", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: "j-1", state: "canceled" }));
    const result = await cancelJob("j-1");
    expect(result.state).toBe("canceled");
    const [url] = getFirstFetchCall();
    const init = getFirstFetchInit();
    expect(url).toBe("/api/jobs/j-1/cancel");
    expect(init.method).toBe("POST");
  });
});

describe("rerunJob", () => {
  it("reruns a job", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: "j-2", state: "queued" }));
    const result = await rerunJob("j-1");
    expect(result.id).toBe("j-2");
    expect(getFirstFetchUrl()).toBe("/api/jobs/j-1/rerun");
  });
});

describe("resolveJob", () => {
  it("resolves a job with merge", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ resolution: "merged", error: null }));
    const result = await resolveJob("j-1", "merge");
    expect(result.resolution).toBe("merged");
    expect(result.error).toBeNull();
    const body = JSON.parse(getFirstFetchInit().body as string);
    expect(body.action).toBe("merge");
  });
});

describe("archiveJob", () => {
  it("archives a job (204)", async () => {
    mockFetch.mockResolvedValueOnce(noContentResponse());
    const result = await archiveJob("j-1");
    expect(result).toBeUndefined();
    expect(getFirstFetchUrl()).toBe("/api/jobs/j-1/archive");
  });
});

describe("pauseJob", () => {
  it("pauses a job", async () => {
    mockFetch.mockResolvedValueOnce(noContentResponse());
    await pauseJob("j-1");
    expect(getFirstFetchUrl()).toBe("/api/jobs/j-1/pause");
    expect(getFirstFetchInit().method).toBe("POST");
  });
});

describe("continueJob", () => {
  it("continues a job with instruction", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: "j-1", state: "running", branch: null, worktreePath: null, createdAt: "2025-01-01" }));
    const result = await continueJob("j-1", "Do more");
    expect(result.id).toBe("j-1");
    const body = JSON.parse(getFirstFetchInit().body as string);
    expect(body.instruction).toBe("Do more");
  });
});

describe("resumeJob", () => {
  it("resumes a job with instruction", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: "j-1", state: "running", branch: null, worktreePath: null, createdAt: "2025-01-01", updatedAt: "2025-01-01" }));
    const result = await resumeJob("j-1", "Continue");
    expect(result.state).toBe("running");
    const body = JSON.parse(getFirstFetchInit().body as string);
    expect(body.instruction).toBe("Continue");
  });

  it("resumes a job without requiring extra instruction", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: "j-1", state: "running", branch: null, worktreePath: null, createdAt: "2025-01-01", updatedAt: "2025-01-01" }));
    await resumeJob("j-1");
    const body = JSON.parse(getFirstFetchInit().body as string);
    expect(body).toEqual({});
  });
});

// ---- Logs / Transcript / Diff ---------------------------------------------

describe("fetchJobLogs", () => {
  it("fetches logs for a job", async () => {
    const logs = [{ jobId: "j-1", seq: 1, level: "info", message: "Hello" }];
    mockFetch.mockResolvedValueOnce(jsonResponse(logs));
    const result = await fetchJobLogs("j-1");
    expect(result).toEqual(logs);
    const url = getFirstFetchUrl();
    expect(url).toContain("/api/jobs/j-1/logs");
    expect(url).toContain("level=debug");
  });

  it("respects level param", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse([]));
    await fetchJobLogs("j-1", "error");
    const url = getFirstFetchUrl();
    expect(url).toContain("level=error");
  });
});

describe("fetchJobTranscript", () => {
  it("fetches transcript", async () => {
    const entries = [{ seq: 1, role: "agent", content: "Done" }];
    mockFetch.mockResolvedValueOnce(jsonResponse(entries));
    const result = await fetchJobTranscript("j-1");
    expect(result).toEqual(entries);
    expect(getFirstFetchUrl()).toContain("/api/jobs/j-1/transcript");
  });
});

describe("fetchJobTimeline", () => {
  it("fetches timeline and adds active=false", async () => {
    const raw = [{ headline: "h", headlinePast: "hp", summary: "s", timestamp: "2025-01-01" }];
    mockFetch.mockResolvedValueOnce(jsonResponse(raw));
    const result = await fetchJobTimeline("j-1");
    const firstEntry = result[0];
    expect(firstEntry).toBeDefined();
    expect(firstEntry?.active).toBe(false);
    expect(firstEntry?.headline).toBe("h");
  });
});

describe("fetchJobDiff", () => {
  it("fetches diff", async () => {
    const diff = [{ path: "a.ts", status: "modified", additions: 1, deletions: 0, hunks: [] }];
    mockFetch.mockResolvedValueOnce(jsonResponse(diff));
    const result = await fetchJobDiff("j-1");
    expect(result).toEqual(diff);
    expect(getFirstFetchUrl()).toContain("/api/jobs/j-1/diff");
  });
});

// ---- Settings -------------------------------------------------------------

describe("fetchSettings", () => {
  it("returns settings", async () => {
    const settings = { maxConcurrentJobs: 2, permissionMode: "full_auto" };
    mockFetch.mockResolvedValueOnce(jsonResponse(settings));
    const result = await fetchSettings();
    expect(result).toEqual(settings);
  });
});

describe("updateSettings", () => {
  it("updates settings with PUT", async () => {
    const settings = { maxConcurrentJobs: 3 };
    mockFetch.mockResolvedValueOnce(jsonResponse(settings));
    const result = await updateSettings(settings as any);
    expect(result).toEqual(settings);
    expect(getFirstFetchInit().method).toBe("PUT");
  });
});

// ---- Repos ----------------------------------------------------------------

describe("fetchRepos", () => {
  it("returns repo list", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ items: ["/repos/a"] }));
    const result = await fetchRepos();
    expect(result.items).toEqual(["/repos/a"]);
  });
});

describe("registerRepo", () => {
  it("registers a repo", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ path: "/repos/a", source: "/repos/a", cloned: false }));
    const result = await registerRepo("/repos/a");
    expect(result.path).toBe("/repos/a");
    const body = JSON.parse(getFirstFetchInit().body as string);
    expect(body.source).toBe("/repos/a");
  });
});

describe("unregisterRepo", () => {
  it("deletes a repo", async () => {
    mockFetch.mockResolvedValueOnce(noContentResponse());
    await unregisterRepo("/repos/a");
    expect(getFirstFetchInit().method).toBe("DELETE");
  });
});

// ---- Approvals ------------------------------------------------------------

describe("fetchApprovals", () => {
  it("returns approvals for a job", async () => {
    const approvals = [{ id: "apr-1", jobId: "j-1", description: "Allow?" }];
    mockFetch.mockResolvedValueOnce(jsonResponse(approvals));
    const result = await fetchApprovals("j-1");
    expect(result).toEqual(approvals);
  });
});

describe("resolveApproval", () => {
  it("resolves an approval", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: "apr-1", resolution: "approved" }));
    const result = await resolveApproval("apr-1", "approved");
    expect(result.resolution).toBe("approved");
    const body = JSON.parse(getFirstFetchInit().body as string);
    expect(body.resolution).toBe("approved");
  });
});

describe("trustJob", () => {
  it("trusts a job", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ resolved: 3 }));
    const result = await trustJob("j-1");
    expect(result.resolved).toBe(3);
    expect(getFirstFetchUrl()).toBe("/api/jobs/j-1/approvals/trust");
  });
});

// ---- Artifacts ------------------------------------------------------------

describe("fetchArtifacts", () => {
  it("returns artifacts", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ items: [] }));
    const result = await fetchArtifacts("j-1");
    expect(result.items).toEqual([]);
    expect(getFirstFetchUrl()).toContain("/api/jobs/j-1/artifacts");
  });
});

// ---- Workspace ------------------------------------------------------------

describe("fetchWorkspaceFiles", () => {
  it("fetches workspace file list", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ items: [], cursor: null, hasMore: false }));
    const result = await fetchWorkspaceFiles("j-1");
    expect(result.items).toEqual([]);
  });

  it("appends path param", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ items: [], cursor: null, hasMore: false }));
    await fetchWorkspaceFiles("j-1", { path: "src/" });
    const url = getFirstFetchUrl();
    expect(url).toContain("path=src");
  });
});

describe("fetchWorkspaceFile", () => {
  it("fetches a single workspace file", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ path: "a.ts", content: "hello" }));
    const result = await fetchWorkspaceFile("j-1", "a.ts");
    expect(result.content).toBe("hello");
    const url = getFirstFetchUrl();
    expect(url).toContain("path=a.ts");
  });
});

// ---- Operator Messages ----------------------------------------------------

describe("sendOperatorMessage", () => {
  it("sends a message", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ seq: 1, timestamp: "2025-01-01" }));
    const result = await sendOperatorMessage("j-1", "Hello");
    expect(result.seq).toBe(1);
    const body = JSON.parse(getFirstFetchInit().body as string);
    expect(body.content).toBe("Hello");
  });
});

// ---- Voice ----------------------------------------------------------------

describe("transcribeAudio", () => {
  it("returns transcription text", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ text: "Hello world" }),
    });
    const blob = new Blob(["audio"], { type: "audio/webm" });
    const result = await transcribeAudio(blob);
    expect(result).toBe("Hello world");
    const [url] = getFirstFetchCall();
    const init = getFirstFetchInit();
    expect(url).toBe("/api/voice/transcribe");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("throws ApiError on transcription failure", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      json: async () => ({ detail: "No audio" }),
    });
    const blob = new Blob(["audio"]);
    await expect(transcribeAudio(blob)).rejects.toThrow(ApiError);
  });
});

// ---- Models / SDKs --------------------------------------------------------

describe("fetchModels", () => {
  it("returns model list", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse([{ id: "gpt-4", name: "GPT-4" }]));
    const result = await fetchModels();
    const firstModel = result[0];
    expect(firstModel).toBeDefined();
    expect(firstModel?.id).toBe("gpt-4");
  });
});

describe("fetchSDKs", () => {
  it("returns SDK list", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ default: "copilot", sdks: [] }));
    const result = await fetchSDKs();
    expect(result.default).toBe("copilot");
  });
});

// ---- Error formatting edge cases ------------------------------------------

describe("request error handling", () => {
  it("uses statusText when json body is null", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: async () => { throw new Error("no json"); },
    });
    try {
      await fetchHealth();
    } catch (e) {
      expect((e as ApiError).detail).toBe("Internal Server Error");
    }
  });

  it("uses HTTP status when detail is not a string or array", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Server Error",
      json: async () => ({ detail: 42 }),
    });
    try {
      await fetchHealth();
    } catch (e) {
      expect((e as ApiError).detail).toBe("Server Error");
    }
  });
});
