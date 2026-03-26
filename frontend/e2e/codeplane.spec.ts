/**
 * End-to-end tests for CodePlane UI.
 *
 * These tests verify the full stack: backend + frontend running together.
 * The Playwright config starts the CodePlane server automatically.
 */

import { test, expect } from "@playwright/test";

test.describe("Health & Navigation", () => {
  test("loads the dashboard", async ({ page }) => {
    await page.goto("/");
    // Should see the CodePlane header
    await expect(page.getByText("CodePlane").first()).toBeVisible();
  });

  test("shows connection status", async ({ page }) => {
    await page.goto("/");
    // SSE should connect — connection status indicator should be visible
    await expect(page.getByLabel(/Connection status/)).toBeVisible({ timeout: 10_000 });
  });

  test("navigates to create job screen", async ({ page }) => {
    await page.goto("/");
    await page.click("text=New Job");
    await expect(page).toHaveURL(/\/jobs\/new/);
    await expect(page.getByRole("heading", { name: "New Job" })).toBeVisible();
  });

  test("navigates to settings screen", async ({ page }) => {
    await page.goto("/");
    // Open the nav menu slideout, then click Settings
    await page.getByLabel("Open navigation menu").click();
    await page.getByText("Settings").click();
    await expect(page).toHaveURL(/\/settings/);
  });
});

test.describe("Dashboard", () => {
  test("shows kanban columns on desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/");
    // Should see 3 kanban columns (each is role="region" with aria-label)
    await expect(page.getByRole("region", { name: "In Progress" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Awaiting Input" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Failed" })).toBeVisible();
  });

  test("shows mobile filter tabs on small viewport", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");
    // Kanban board hidden on mobile, mobile tab buttons visible
    await expect(page.getByRole("button", { name: "In Progress" })).toBeVisible();
  });
});

test.describe("Job Creation", () => {
  test("shows repo selector and prompt input", async ({ page }) => {
    await page.goto("/jobs/new");
    // Should have the prompt textarea
    const textarea = page.locator("textarea").first();
    await expect(textarea).toBeVisible();
  });

  test("has a create button", async ({ page }) => {
    await page.goto("/jobs/new");
    const createBtn = page.locator("button", { hasText: "Create Job" });
    await expect(createBtn).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// React #185 — Kanban board must not crash when job cards appear
// ---------------------------------------------------------------------------

/**
 * Reproduces the "Too many re-renders" (React error #185) that occurred when
 * Zustand store updates from SSE events were dispatched via queueMicrotask.
 *
 * Root cause: EventSource fires multiple SSE events in the same browser
 * macrotask when they arrive in the same TCP chunk. Each event handler queued
 * a queueMicrotask(() => dispatchSSEEvent(...)). All those microtasks land in
 * a single microtask checkpoint. Each Zustand set() causes React's
 * useSyncExternalStore to schedule its own queueMicrotask(flushSyncWork).
 * These interleaved microtasks make React's getSnapshot() return a different
 * value from what was captured at the start of the render, triggering the
 * "tearing" detection loop → React #185.
 *
 * Fix: dispatch SSE store updates via setTimeout(fn, 0) (macrotask) so they
 * can never land in the same microtask checkpoint as React's flush scheduling.
 */

const JOB_FIXTURE = {
  id: "e2e-job-running-01",
  repo: "/repos/example-app",
  prompt: "Refactor the authentication module",
  state: "running",
  strategy: "single_agent",
  baseRef: "main",
  worktreePath: null,
  branch: "refactor/auth",
  createdAt: new Date(Date.now() - 120_000).toISOString(),
  updatedAt: new Date(Date.now() - 10_000).toISOString(),
  completedAt: null,
  prUrl: null,
};

const COMPLETED_JOB_FIXTURE = {
  ...JOB_FIXTURE,
  id: "e2e-job-done-02",
  state: "failed",
  failureReason: "Out of memory",
  completedAt: new Date(Date.now() - 5_000).toISOString(),
};

test.describe("React #185 – kanban renders job cards without infinite loop", () => {
  test("dashboard renders job cards fetched via REST without crashing", async ({
    page,
  }) => {
    // Capture console errors — React logs caught errors to console even when
    // an ErrorBoundary handles them (both in dev and prod builds).
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    // Mock SSE: send one heartbeat immediately so the onopen + heartbeat
    // dispatch land right when the jobs fetch also resolves — this is the
    // race condition that triggers #185.
    await page.route("**/api/events*", async (route) => {
      await route.fulfill({
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "X-Accel-Buffering": "no",
        },
        body: "event: session_heartbeat\ndata: {}\n\n",
      });
    });

    // Mock jobs API: return jobs in two different columns (Active + History)
    // so that both KanbanColumn instances transition from empty to non-empty.
    await page.route("**/api/jobs*", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [JOB_FIXTURE, COMPLETED_JOB_FIXTURE],
          cursor: null,
          hasMore: false,
        }),
      });
    });

    await page.goto("/");

    // The running job appears in both the kanban board (visible, desktop) and
    // the MobileJobList DOM (hidden via CSS). Use .first() to target the kanban
    // card without triggering Playwright's strict-mode violation.
    await expect(page.getByText(JOB_FIXTURE.id).first()).toBeVisible({
      timeout: 5_000,
    });

    // The failed job appears in the Failed kanban column.
    await expect(page.getByText(COMPLETED_JOB_FIXTURE.id).first()).toBeVisible({
      timeout: 5_000,
    });

    // If React #185 fires the ErrorBoundary catches it and shows this heading.
    // Check AFTER the job cards have appeared so we catch late-firing crashes
    // triggered by SSE reconnection events.
    await expect(page.getByText("Something went wrong")).not.toBeVisible();

    // No React loop error in the console.
    const reactLoopError = consoleErrors.find(
      (m) =>
        m.includes("Too many re-renders") ||
        m.includes("Minified React error #185") ||
        (m.includes("185") && m.includes("react.dev/errors")),
    );
    expect(
      reactLoopError,
      `React infinite-render error found in console: ${reactLoopError}`,
    ).toBeUndefined();
  });

  test("SSE snapshot event transitions empty kanban to job cards without crash", async ({
    page,
  }) => {
    /**
     * This test covers the exact scenario from the bug report:
     *
     * 1. Dashboard mounted with empty store (no jobs) → kanban shows "No jobs"
     * 2. SSE `snapshot` event arrives with a job → store updates state.jobs
     * 3. KanbanBoard re-renders → KanbanColumn receives non-empty jobs array
     * 4. KanbanColumn transitions from empty placeholder to <JobCard> children
     *    inside the column, which was the specific component tree
     *    mentioned in the issue report.
     * 5. Verify no React #185 crash (ErrorBoundary must stay inactive).
     *
     * The snapshot event deliberately triggers the "empty → non-empty" children
     * change inside Mantine Stack/Paper that the original crash report highlighted.
     * Multiple SSE events are sent in one response body to exercise the concurrent
     * event delivery path that the setTimeout fix protects against.
     */
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    // Start with empty jobs so the kanban renders "No jobs" first.
    await page.route("**/api/jobs*", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], cursor: null, hasMore: false }),
      });
    });

    // SSE mock: heartbeat + snapshot in the same response body so the browser
    // delivers them as part of the same buffered stream.  The useSSE.ts fix
    // (setTimeout instead of queueMicrotask) ensures these are dispatched as
    // separate macrotasks even when buffered together.
    const snapshotPayload = JSON.stringify({
      jobs: [JOB_FIXTURE, COMPLETED_JOB_FIXTURE],
      pendingApprovals: [],
    });
    await page.route("**/api/events*", async (route) => {
      await route.fulfill({
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "X-Accel-Buffering": "no",
        },
        body: [
          "event: session_heartbeat\ndata: {}\n\n",
          `event: snapshot\ndata: ${snapshotPayload}\n\n`,
        ].join(""),
      });
    });

    await page.goto("/");

    // SSE snapshot must populate the kanban (empty → non-empty transition).
    await expect(page.getByText(JOB_FIXTURE.id).first()).toBeVisible({
      timeout: 8_000,
    });
    await expect(page.getByText(COMPLETED_JOB_FIXTURE.id).first()).toBeVisible({
      timeout: 8_000,
    });

    // Check for ErrorBoundary AFTER the transition, so we catch late crashes.
    await expect(page.getByText("Something went wrong")).not.toBeVisible();

    const reactLoopError = consoleErrors.find(
      (m) =>
        m.includes("Too many re-renders") ||
        m.includes("Minified React error #185") ||
        (m.includes("185") && m.includes("react.dev/errors")),
    );
    expect(
      reactLoopError,
      `React infinite-render error in console: ${reactLoopError}`,
    ).toBeUndefined();
  });
});

test.describe("API Health", () => {
  test("health endpoint returns healthy", async ({ request }) => {
    const response = await request.get("/api/health");
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.status).toBe("healthy");
    expect(body.version).toBeDefined();
  });

  test("jobs endpoint returns list", async ({ request }) => {
    const response = await request.get("/api/jobs");
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.items).toBeDefined();
    expect(Array.isArray(body.items)).toBe(true);
  });

  test("repos endpoint returns list", async ({ request }) => {
    const response = await request.get("/api/settings/repos");
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.items).toBeDefined();
  });
});
