/**
 * E2E tests: Full job lifecycle through the UI.
 *
 * Covers job creation, SSE-driven state transitions, log/transcript
 * rendering, job success, and resolution flow.
 */

import { test, expect } from "@playwright/test";
import type { Job, ProjectResponse } from "../src/api/types";
import { makeProjectBoard, mockLegacyRecipe, setupProjectBoardMocks } from "./helpers";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const NOW = new Date().toISOString();
const REPOSITORY = "/tmp/test-repo";

const MOCK_JOB: Job = {
  id: "job-1",
  title: "",
  prompt: "Fix the bug in auth module",
  state: "running",
  createdAt: NOW,
  updatedAt: NOW,
  completedAt: null,
  repo: REPOSITORY,
  projectId: "project-1",
  branch: "cpl/job-1",
  baseRef: "main",
  worktreePath: null,
  prUrl: null,
  resolution: null,
  archivedAt: null,
  failureReason: null,
  progressHeadline: null,
  model: "claude-sonnet-4-5-20250514",
  sdk: "copilot",
  source: "managed",
  mode: "standard",
  observationOnly: false,
};
const PROJECT: ProjectResponse = {
  id: "project-1", name: "test-repo", repoPaths: [REPOSITORY], createdAt: NOW, updatedAt: NOW,
};

const MOCK_SETTINGS = {
  maxConcurrentJobs: 2,
  permissionMode: "full_auto",
  autoPush: false,
  cleanupWorktree: true,
  deleteBranchAfterMerge: false,
  artifactRetentionDays: 30,
  maxArtifactSizeMb: 100,
  autoArchiveDays: 14,
  verify: false,
  selfReview: false,
  maxTurns: 3,
  verifyPrompt: "",
  selfReviewPrompt: "",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build SSE body from an array of {event, data} objects. */
function sseBody(events: { event: string; data: unknown }[]): string {
  return events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
}

/** Standard API mocks shared across lifecycle tests. */
async function setupBaseMocks(page: import("@playwright/test").Page, jobs: unknown[] = []) {
  await mockLegacyRecipe(page, MOCK_JOB.id);
  await page.route("**/api/events*", async (route) => {
    const snapshotPayload = { jobs, pendingApprovals: [] };
    await route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
      body: sseBody([
        { event: "session_heartbeat", data: {} },
        { event: "snapshot", data: snapshotPayload },
      ]),
    });
  });

  await page.route("**/api/jobs?*", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: jobs, cursor: null, hasMore: false }),
    });
  });

  // Settings endpoints used by job creation form
  await page.route("**/api/settings", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_SETTINGS),
    });
  });

  await page.route("**/api/settings/repos", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: ["/tmp/test-repo"] }),
    });
  });

  // Project board is scoped under a Project (AD-2) — mock the owning Project
  // so RepoLayout/RepoBoard resolve "/tmp/test-repo" to a real project route.
  await page.route("**/api/settings/projects", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [{ id: "project-1", name: "test-repo", repoPaths: ["/tmp/test-repo"] }],
      }),
    });
  });

  // Board is keyed by project id (not repo path); mock the single-project
  // fetch and its task-links so RepoBoard resolves cleanly.
  await page.route("**/api/settings/projects/project-1", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "project-1",
        name: "test-repo",
        repoPaths: ["/tmp/test-repo"],
        createdAt: NOW,
        updatedAt: NOW,
      }),
    });
  });
  await page.route("**/api/settings/projects/project-1/task-links", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
  });

  await page.route("**/api/sdks", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        default: "copilot",
        sdks: [{ id: "copilot", name: "GitHub Copilot", enabled: true, status: "ready" }],
      }),
    });
  });

  await page.route("**/api/models", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ id: "claude-sonnet-4-5-20250514", name: "Claude Sonnet 4.5" }]),
    });
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Job Creation", () => {
  test("navigates to /jobs/new, fills form, and submits", async ({ page }) => {
    const createdJob = { ...MOCK_JOB, state: "queued" };

    await setupBaseMocks(page);

    await page.route("**/api/jobs/suggest-names", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          title: "Fix the bug in auth module",
          description: "Fix the bug in auth module",
          branchName: "fix-auth-module",
          worktreeName: "fix-auth-module",
        }),
      });
    });

    // Mock POST /api/jobs to return created job
    await page.route("**/api/jobs", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      const body = route.request().postDataJSON();
      expect(body.repo).toBe("/tmp/test-repo");
      expect(body.prompt).toContain("Fix the bug");
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ id: createdJob.id }),
      });
    });

    // Mock the redirect target — job detail fetches
    await page.route("**/api/jobs/job-1", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(createdJob),
      });
    });
    await page.route("**/api/jobs/job-1/transcript*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/timeline*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/diff*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });
    await page.route("**/api/jobs/job-1/approvals*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.goto("/jobs/new");

    // Fill prompt
    const textarea = page.locator("textarea").first();
    await expect(textarea).toBeVisible();
    await page.getByRole("button", { name: "Select a repository…" }).click();
    await page.getByText("test-repo · test-repo").click();
    await textarea.fill("Fix the bug in auth module");

    // Submit
    const createBtn = page.locator("button", { hasText: "Create Job" });
    await expect(createBtn).toBeEnabled();
    await createBtn.click();

    // Should navigate to job detail
    await expect(page).toHaveURL(/\/jobs\/job-1/, { timeout: 10_000 });
  });
});

test.describe("Project board SSE Integration", () => {
  test("job appears on project board via SSE snapshot event", async ({ page }) => {
    await setupBaseMocks(page, [MOCK_JOB]);
    await setupProjectBoardMocks(page, PROJECT, () => makeProjectBoard(PROJECT, { in_progress: [MOCK_JOB] }));

    await page.goto("/projects/id/project-1/board");

    const active = page.getByRole("region", { name: "In Progress" });
    await expect(active.getByRole("link", { name: "Open Job", exact: true })).toHaveAttribute("href", "/jobs/job-1");
    await expect(active).toContainText("Job: running");
    await expect(page.getByLabel("Persisted Project totals")).toContainText("1 standalone Jobs");
  });

  test("SSE job_state_changed updates job card on project board", async ({ page }) => {
    // Start with a running job
    await setupBaseMocks(page, [MOCK_JOB]);
    let board = makeProjectBoard(PROJECT, { in_progress: [MOCK_JOB] });
    await setupProjectBoardMocks(page, PROJECT, () => board);
    let releaseEvent!: () => void;
    const released = new Promise<void>((resolve) => { releaseEvent = resolve; });
    await page.route("**/api/events*", async (route) => {
      await released;
      await route.fulfill({
        headers: { "Content-Type": "text/event-stream" },
        body: sseBody([
          { event: "snapshot", data: { jobs: [MOCK_JOB], pendingApprovals: [] } },
          { event: "job.state_changed", data: {
            id: "job-1-review", session_id: MOCK_JOB.id, kind: "job.state_changed",
            timestamp: NOW, payload: { state: "review" }, metadata: {},
          } },
        ]),
      });
    });

    await page.goto("/projects/id/project-1/board");
    const activeColumn = page.getByRole("region", { name: "In Progress" });
    await expect(activeColumn.getByRole("link", { name: "Open Job" })).toHaveAttribute("href", "/jobs/job-1");
    await expect(activeColumn).toContainText("Job: running");
    board = makeProjectBoard(PROJECT, { needs_decision: [{ ...MOCK_JOB, state: "review" }] });
    releaseEvent();
    const decisions = page.getByRole("region", { name: "Needs a decision" });
    await expect(decisions.getByRole("link", { name: "Open Job" })).toHaveAttribute("href", "/jobs/job-1");
    await expect(decisions).toContainText("Job: review");
    await expect(activeColumn.getByRole("link", { name: "Open Job" })).toHaveCount(0);
    await expect(page.getByLabel("Persisted Project totals")).toContainText("1 standalone Jobs / 1 Jobs in review");
  });
});

test.describe("Job Detail — Live Events", () => {
  test.beforeEach(async ({ page }) => {
    // Mock SSE with the running job in the snapshot
    await setupBaseMocks(page, [MOCK_JOB]);

    // Mock job detail API
    await page.route("**/api/jobs/job-1", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_JOB),
      });
    });
    await page.route("**/api/jobs/job-1/transcript*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/timeline*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/diff*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });
    await page.route("**/api/jobs/job-1/approvals*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
  });

  test("renders job detail page with header info", async ({ page }) => {
    await page.goto("/jobs/job-1");

    // Should display job ID
    await expect(page.getByText("job-1", { exact: true }).last()).toBeVisible({ timeout: 5_000 });
    // Should display the prompt
    await expect(page.getByText("Fix the bug in auth module").first()).toBeVisible();
    // Should display branch info
    await expect(page.getByText("cpl/job-1")).toBeVisible();
    // Should display repo name
  });

  test("shows cancel button for running job", async ({ page }) => {
    await page.goto("/jobs/job-1");

    await expect(page.getByText("job-1", { exact: true }).last()).toBeVisible({ timeout: 5_000 });
    const cancelBtn = page.locator("button", { hasText: "Cancel" });
    await expect(cancelBtn).toBeVisible();
  });

  test("shows tabs: Live, Files, Changes", async ({ page }) => {
    await page.goto("/jobs/job-1");

    await expect(page.getByText("job-1", { exact: true }).last()).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole("button", { name: "Live" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Files" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Metrics" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Review" })).toBeHidden();
  });
});

test.describe("Job Detail — Success & Resolution", () => {
  test("SSE job_review event updates UI to show resolution options", async ({ page }) => {
    const reviewJob = {
      ...MOCK_JOB,
      state: "review",
      resolution: "unresolved",
      completedAt: NOW,
    };

    await setupBaseMocks(page, [reviewJob]);

    await page.route("**/api/jobs/job-1", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(reviewJob),
      });
    });
    await page.route("**/api/jobs/job-1/transcript*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/timeline*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/diff*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [{ path: "src/main.ts", status: "modified", additions: 5, deletions: 2, hunks: [] }] }),
      });
    });
    await page.route("**/api/jobs/job-1/approvals*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.goto("/jobs/job-1");

    // Should show the review state and resolution controls
    await expect(page.locator("main")).toContainText("In Review", { timeout: 5_000 });

    // Should show resolution buttons: Merge, PR
    await expect(page.locator("button", { hasText: "Merge" })).toBeVisible();
    await expect(page.locator("button", { hasText: "PR" })).toBeVisible();
  });

  test("clicking Merge calls resolve API", async ({ page }) => {
    const reviewJob = {
      ...MOCK_JOB,
      state: "review",
      resolution: "unresolved",
      completedAt: NOW,
    };

    await setupBaseMocks(page, [reviewJob]);

    await page.route("**/api/jobs/job-1", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(reviewJob),
      });
    });
    await page.route("**/api/jobs/job-1/transcript*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/timeline*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/diff*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [{ path: "src/main.ts", status: "modified", additions: 5, deletions: 2, hunks: [] }] }),
      });
    });
    await page.route("**/api/jobs/job-1/approvals*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/snapshot*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          job: reviewJob,
          logs: [],
          transcript: [],
          diff: [{ path: "src/main.ts", status: "modified", additions: 5, deletions: 2, hunks: [] }],
          approvals: [],
          timeline: [],
        }),
      });
    });

    let resolveApiCalled = false;
    await page.route("**/api/jobs/job-1/resolve", async (route) => {
      resolveApiCalled = true;
      const body = route.request().postDataJSON();
      expect(body.action).toBe("smart_merge");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ resolution: "merged", prUrl: null }),
      });
    });

    await page.goto("/jobs/job-1");
    await expect(page.locator("main")).toContainText("In Review", { timeout: 10_000 });

    await page.locator("button", { hasText: "Merge" }).click();

    // Verify the API was called
    await page.waitForTimeout(500);
    expect(resolveApiCalled).toBe(true);
  });
});
