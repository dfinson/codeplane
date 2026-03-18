/**
 * E2E tests: Job action buttons.
 *
 * Covers cancel, retry (rerun), send message, and archive actions
 * from the job detail screen.
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const NOW = new Date().toISOString();

function makeJob(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1",
    title: "Test Job",
    prompt: "Fix the bug",
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
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sseBody(events: { event: string; data: unknown }[]): string {
  return events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
}

async function setupJobDetailMocks(
  page: import("@playwright/test").Page,
  job: ReturnType<typeof makeJob>,
) {
  await page.route("**/api/events*", async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
      body: sseBody([
        { event: "session_heartbeat", data: {} },
        { event: "snapshot", data: { jobs: [job], pendingApprovals: [] } },
      ]),
    });
  });

  await page.route("**/api/jobs?*", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [job], cursor: null, hasMore: false }),
    });
  });

  await page.route("**/api/jobs/job-1", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(job),
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
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Cancel Running Job", () => {
  test("cancel button calls POST /api/jobs/job-1/cancel", async ({ page }) => {
    const runningJob = makeJob({ state: "running" });
    await setupJobDetailMocks(page, runningJob);

    let cancelCalled = false;
    await page.route("**/api/jobs/job-1/cancel", async (route) => {
      cancelCalled = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...runningJob, state: "canceled" }),
      });
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    const cancelBtn = page.locator("button", { hasText: "Cancel" });
    await expect(cancelBtn).toBeVisible();
    await cancelBtn.click();

    await page.waitForTimeout(500);
    expect(cancelCalled).toBe(true);
  });

  test("cancel button is visible for queued jobs", async ({ page }) => {
    const queuedJob = makeJob({ state: "queued" });
    await setupJobDetailMocks(page, queuedJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    await expect(page.locator("button", { hasText: "Cancel" })).toBeVisible();
  });

  test("cancel button is hidden for succeeded jobs", async ({ page }) => {
    const succeededJob = makeJob({ state: "succeeded", resolution: "merged", completedAt: NOW });
    await setupJobDetailMocks(page, succeededJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    await expect(page.locator("button", { hasText: "Cancel" })).toBeHidden();
  });
});

test.describe("Retry Failed Job", () => {
  test("retry button calls POST /api/jobs/job-1/rerun", async ({ page }) => {
    const failedJob = makeJob({ state: "failed", failureReason: "Out of memory" });
    await setupJobDetailMocks(page, failedJob);

    let rerunCalled = false;
    await page.route("**/api/jobs/job-1/rerun", async (route) => {
      rerunCalled = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "job-2" }),
      });
    });

    // Mock the redirect target
    await page.route("**/api/jobs/job-2", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(makeJob({ id: "job-2", state: "queued" })),
      });
    });
    await page.route("**/api/jobs/job-2/transcript*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-2/timeline*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-2/diff*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/job-2/approvals*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    const retryBtn = page.locator("button", { hasText: "Retry" });
    await expect(retryBtn).toBeVisible();
    await retryBtn.click();

    // Should navigate to the new job
    await expect(page).toHaveURL(/\/jobs\/job-2/, { timeout: 10_000 });
    expect(rerunCalled).toBe(true);
  });

  test("retry button is hidden for running jobs", async ({ page }) => {
    const runningJob = makeJob({ state: "running" });
    await setupJobDetailMocks(page, runningJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    await expect(page.locator("button", { hasText: "Retry" })).toBeHidden();
  });
});

test.describe("Send Message to Running Job", () => {
  test("message input is available on job detail for running jobs", async ({ page }) => {
    const runningJob = makeJob({ state: "running" });
    await setupJobDetailMocks(page, runningJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    // TranscriptPanel should have the message input when interactive=true
    const textarea = page.locator("textarea[placeholder*='Send']");
    await expect(textarea).toBeVisible({ timeout: 5_000 });
  });

  test("sending message calls POST /api/jobs/job-1/messages", async ({ page }) => {
    const runningJob = makeJob({ state: "running" });
    await setupJobDetailMocks(page, runningJob);

    let messageCalled = false;
    await page.route("**/api/jobs/job-1/messages", async (route) => {
      messageCalled = true;
      const body = route.request().postDataJSON();
      expect(body.content).toBe("Please also add tests");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    const textarea = page.locator("textarea[placeholder*='Send']");
    await expect(textarea).toBeVisible({ timeout: 5_000 });
    await textarea.fill("Please also add tests");

    // Press Enter to send (or click send button)
    await textarea.press("Enter");

    await page.waitForTimeout(500);
    expect(messageCalled).toBe(true);
  });
});

test.describe("Archive Completed Job", () => {
  test("archive button visible for failed jobs", async ({ page }) => {
    const failedJob = makeJob({ state: "failed", failureReason: "Out of memory" });
    await setupJobDetailMocks(page, failedJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    await expect(page.locator("button", { hasText: "Archive" })).toBeVisible();
  });

  test("archive button visible for canceled jobs", async ({ page }) => {
    const canceledJob = makeJob({ state: "canceled" });
    await setupJobDetailMocks(page, canceledJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    await expect(page.locator("button", { hasText: "Archive" })).toBeVisible();
  });

  test("archive button hidden for running jobs", async ({ page }) => {
    const runningJob = makeJob({ state: "running" });
    await setupJobDetailMocks(page, runningJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    await expect(page.locator("button", { hasText: "Archive" })).toBeHidden();
  });

  test("Complete & Archive button visible for resolved succeeded jobs", async ({ page }) => {
    const resolvedJob = makeJob({
      state: "succeeded",
      resolution: "merged",
      completedAt: NOW,
    });
    await setupJobDetailMocks(page, resolvedJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1")).toBeVisible({ timeout: 5_000 });

    await expect(page.locator("button", { hasText: "Complete & Archive" })).toBeVisible();
  });
});
