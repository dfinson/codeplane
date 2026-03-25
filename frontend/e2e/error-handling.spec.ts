/**
 * E2E tests: Error handling and edge cases.
 *
 * Covers job failure display, API error handling, SSE disconnection
 * indicators, and navigating to nonexistent jobs.
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

async function setupBaseMocks(
  page: import("@playwright/test").Page,
  jobs: unknown[] = [],
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
        { event: "snapshot", data: { jobs, pendingApprovals: [] } },
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
}

async function setupJobDetailMocks(
  page: import("@playwright/test").Page,
  job: ReturnType<typeof makeJob>,
) {
  await setupBaseMocks(page, [job]);

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

test.describe("Job Failure Display", () => {
  test("failed job shows failure reason banner", async ({ page }) => {
    const failedJob = makeJob({
      state: "failed",
      failureReason: "Agent process exited with code 1: Out of memory",
    });
    await setupJobDetailMocks(page, failedJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    // Should display failure banner
    await expect(page.getByText("Job failed")).toBeVisible();
    await expect(page.getByText("Agent process exited with code 1: Out of memory")).toBeVisible();
  });

  test("failed job with no reason shows fallback message", async ({ page }) => {
    const failedJob = makeJob({
      state: "failed",
      failureReason: null,
    });
    await setupJobDetailMocks(page, failedJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await expect(page.getByText("Job failed")).toBeVisible();
    await expect(page.getByText("No additional details available")).toBeVisible();
  });

  test("SSE job_failed event shows failure banner", async ({ page }) => {
    const runningJob = makeJob({ state: "running" });

    // SSE delivers the job_failed event after snapshot
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
          { event: "snapshot", data: { jobs: [runningJob], pendingApprovals: [] } },
          {
            event: "job_failed",
            data: {
              jobId: "job-1",
              reason: "Timeout: agent exceeded 30 minute limit",
              timestamp: NOW,
            },
          },
        ]),
      });
    });

    await page.route("**/api/jobs?*", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [runningJob], cursor: null, hasMore: false }),
      });
    });

    await page.route("**/api/jobs/job-1", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(runningJob),
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

    await page.goto("/jobs/job-1");

    // The SSE job_failed event should update the UI
    await expect(page.getByText("Job failed")).toBeVisible({ timeout: 8_000 });
    await expect(page.getByText("Timeout: agent exceeded 30 minute limit")).toBeVisible();
  });
});

test.describe("Canceled Job Display", () => {
  test("canceled job shows canceled banner", async ({ page }) => {
    const canceledJob = makeJob({ state: "canceled" });
    await setupJobDetailMocks(page, canceledJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await expect(page.getByText("Job canceled")).toBeVisible();
  });
});

test.describe("Nonexistent Job", () => {
  test("shows 'Job not found' for nonexistent job ID", async ({ page }) => {
    await setupBaseMocks(page);

    // Return 404 for nonexistent job
    await page.route("**/api/jobs/nonexistent-job", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Job not found" }),
      });
    });

    await page.route("**/api/jobs/nonexistent-job/transcript*", async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/nonexistent-job/timeline*", async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/nonexistent-job/diff*", async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/jobs/nonexistent-job/approvals*", async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: "[]" });
    });

    await page.goto("/jobs/nonexistent-job");

    // Should show not found message
    await expect(page.getByText("Job not found")).toBeVisible({ timeout: 5_000 });
    // Should have a back button
    await expect(page.locator("button", { hasText: "Back to Dashboard" })).toBeVisible();
  });
});

test.describe("SSE Connection Status", () => {
  test("shows connected status when SSE heartbeat received", async ({ page }) => {
    // Keep the SSE connection alive so the browser doesn't immediately
    // fire onerror (which would flip status away from "Connected").
    await page.route("**/api/events*", async (route) => {
      // Fulfill with a streaming response that stays open long enough
      // for the assertion to pass by padding with keep-alive comments.
      const heartbeat = "event: session_heartbeat\ndata: {}\n\n";
      const keepAlive = ": keep-alive\n\n".repeat(100);
      await route.fulfill({
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "X-Accel-Buffering": "no",
        },
        body: heartbeat + keepAlive,
      });
    });

    await page.route("**/api/jobs?*", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], cursor: null, hasMore: false }),
      });
    });

    await page.goto("/");

    // The connection status badge should appear
    await expect(page.getByText("Connected")).toBeVisible({ timeout: 10_000 });
  });
});

test.describe("Model Downgrade Banner", () => {
  test("shows model downgrade warning when applicable", async ({ page }) => {
    const downgradedJob = makeJob({
      state: "failed",
      failureReason: "Model downgraded: requested claude-opus-4-20250514 but received claude-sonnet-4-5-20250514",
      modelDowngraded: true,
      requestedModel: "claude-opus-4-20250514",
      actualModel: "claude-sonnet-4-5-20250514",
    });
    await setupJobDetailMocks(page, downgradedJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await expect(page.getByText("Model downgraded", { exact: true })).toBeVisible();
    // Model names appear in both the failure reason and the downgrade banner
    await expect(page.locator("text=claude-opus-4-20250514").first()).toBeVisible();
    await expect(page.locator("text=claude-sonnet-4-5-20250514").first()).toBeVisible();
  });
});

test.describe("Completed Job Display", () => {
  test("completed + merged job shows success banner", async ({ page }) => {
    const mergedJob = makeJob({
      state: "completed",
      resolution: "merged",
      completedAt: NOW,
    });
    await setupJobDetailMocks(page, mergedJob);

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await expect(page.getByText("Job completed")).toBeVisible();
    await expect(page.getByText("Changes merged into base branch")).toBeVisible();
  });

  test("review + conflict shows merge conflict banner", async ({ page }) => {
    const conflictJob = makeJob({
      state: "review",
      resolution: "conflict",
      completedAt: NOW,
    });
    await setupJobDetailMocks(page, conflictJob);

    // Need diff data to show resolution buttons
    await page.route("**/api/jobs/job-1/diff*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{ path: "src/main.ts", status: "modified", additions: 5, deletions: 2, hunks: [] }]),
      });
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await expect(page.getByText("Merge conflict", { exact: false }).first()).toBeVisible();
    await expect(page.locator("button", { hasText: "Resolve with Agent" })).toBeVisible();
  });
});
