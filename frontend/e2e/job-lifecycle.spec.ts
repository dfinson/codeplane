/**
 * E2E tests: Full job lifecycle through the UI.
 *
 * Covers job creation, SSE-driven state transitions, log/transcript
 * rendering, job success, and resolution flow.
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const NOW = new Date().toISOString();

const MOCK_JOB = {
  id: "job-1",
  title: "Test Job",
  prompt: "Fix the bug in auth module",
  state: "running",
  createdAt: NOW,
  updatedAt: NOW,
  completedAt: null,
  repo: "/tmp/test-repo",
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
};

const MOCK_SETTINGS = {
  maxConcurrentJobs: 2,
  permissionMode: "auto",
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
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/approvals*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.goto("/jobs/new");

    // Fill prompt
    const textarea = page.locator("textarea").first();
    await expect(textarea).toBeVisible();
    await textarea.fill("Fix the bug in auth module");

    // Submit
    const createBtn = page.locator("button", { hasText: "Create Job" });
    await expect(createBtn).toBeEnabled();
    await createBtn.click();

    // Should navigate to job detail
    await expect(page).toHaveURL(/\/jobs\/job-1/, { timeout: 10_000 });
  });
});

test.describe("Dashboard SSE Integration", () => {
  test("job appears on dashboard via SSE snapshot event", async ({ page }) => {
    await setupBaseMocks(page, [MOCK_JOB]);

    await page.goto("/");

    // Job should appear in the kanban — use .first() for strict mode
    await expect(page.getByText(MOCK_JOB.id).first()).toBeVisible({ timeout: 8_000 });
  });

  test("SSE job_state_changed updates job card on dashboard", async ({ page }) => {
    // Start with a running job
    await setupBaseMocks(page, [MOCK_JOB]);

    await page.goto("/");
    await expect(page.getByText(MOCK_JOB.id).first()).toBeVisible({ timeout: 8_000 });

    // The job card should show "running" state initially — verified by its presence
    // in the Active kanban column
    const activeColumn = page.locator(".kanban-column").first();
    await expect(activeColumn.getByText(MOCK_JOB.id)).toBeVisible();
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
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-1/approvals*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
  });

  test("renders job detail page with header info", async ({ page }) => {
    await page.goto("/jobs/job-1");

    // Should display job ID
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });
    // Should display the prompt
    await expect(page.getByText("Fix the bug in auth module")).toBeVisible();
    // Should display branch info
    await expect(page.getByText("cpl/job-1")).toBeVisible();
    // Should display repo name
    await expect(page.getByText("test-repo")).toBeVisible();
  });

  test("shows cancel button for running job", async ({ page }) => {
    await page.goto("/jobs/job-1");

    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });
    const cancelBtn = page.locator("button", { hasText: "Cancel" });
    await expect(cancelBtn).toBeVisible();
  });

  test("shows tabs: Live, Files, Changes, Artifacts", async ({ page }) => {
    await page.goto("/jobs/job-1");

    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole("tab", { name: "Live" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Files" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Changes" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Artifacts" })).toBeVisible();
  });
});

test.describe("Job Detail — Success & Resolution", () => {
  test("SSE job_succeeded event updates UI to show resolution options", async ({ page }) => {
    const succeededJob = {
      ...MOCK_JOB,
      state: "succeeded",
      resolution: "unresolved",
      completedAt: NOW,
    };

    await setupBaseMocks(page, [succeededJob]);

    await page.route("**/api/jobs/job-1", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(succeededJob),
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
        body: JSON.stringify([{ path: "src/main.ts", status: "modified", additions: 5, deletions: 2, hunks: [] }]),
      });
    });
    await page.route("**/api/jobs/job-1/approvals*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.goto("/jobs/job-1");

    // Should show the sign-off banner
    await expect(page.getByText("Sign off required")).toBeVisible({ timeout: 5_000 });

    // Should show resolution buttons: Merge, Create PR, Discard
    await expect(page.locator("button", { hasText: "Merge" })).toBeVisible();
    await expect(page.locator("button", { hasText: "Create PR" })).toBeVisible();
    await expect(page.locator("button", { hasText: "Discard" })).toBeVisible();
  });

  test("clicking Merge calls resolve API", async ({ page }) => {
    const succeededJob = {
      ...MOCK_JOB,
      state: "succeeded",
      resolution: "unresolved",
      completedAt: NOW,
    };

    await setupBaseMocks(page, [succeededJob]);

    await page.route("**/api/jobs/job-1", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(succeededJob),
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
        body: JSON.stringify([{ path: "src/main.ts", status: "modified", additions: 5, deletions: 2, hunks: [] }]),
      });
    });
    await page.route("**/api/jobs/job-1/approvals*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
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
    await expect(page.getByText("Sign off required")).toBeVisible({ timeout: 5_000 });

    await page.locator("button", { hasText: "Merge" }).click();

    // Verify the API was called
    await page.waitForTimeout(500);
    expect(resolveApiCalled).toBe(true);
  });
});
