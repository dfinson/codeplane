/**
 * End-to-end tests for CodePlane UI.
 *
 * These tests verify the full stack: backend + frontend running together.
 * The Playwright config starts the CodePlane server automatically.
 */

import { test, expect } from "@playwright/test";
import type { Job, ProjectResponse } from "../src/api/types";
import { makeProjectBoard, setupProjectBoardMocks, sseBody } from "./helpers";

test.describe("Health & Navigation", () => {
  test("loads the projects overview", async ({ page }) => {
    await page.goto("/");
    // Should see the CodePlane header
    await expect(page.getByText("CodePlane").first()).toBeVisible();
    // Root is the project-first landing surface, not a flat job dashboard.
    await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  });

  test("shows connection status", async ({ page }) => {
    await page.goto("/");
    // SSE should connect — connection status indicator should be visible
    await expect(page.getByLabel(/Connection status/)).toBeVisible({ timeout: 10_000 });
  });

  test("navigates to create job screen", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Search or navigate").click();
    await page.locator('[role="dialog"] button', { hasText: "New Job" }).first().click();
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

test.describe("Project board", () => {
  test("shows kanban columns on a project board", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/");
    // Kanban board is now scoped under a project, not the flat root dashboard.
    await page.locator('[aria-label="Project list"] a').first().click();
    await page.waitForURL(/\/projects\/id\/.+\/board/);
    await expect(page.getByRole("region", { name: "In Progress" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Needs a decision" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Failed" })).toBeVisible();
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

const REPOSITORY = "/repos/example-app";
const JOB_FIXTURE: Job = {
  id: "e2e-job-running-01",
  repo: REPOSITORY,
  projectId: "e2e-project-1",
  prompt: "Refactor the authentication module",
  state: "running",
  sdk: "copilot",
  source: "managed",
  mode: "standard",
  observationOnly: false,
  baseRef: "main",
  worktreePath: null,
  branch: "refactor/auth",
  createdAt: new Date(Date.now() - 120_000).toISOString(),
  updatedAt: new Date(Date.now() - 10_000).toISOString(),
  completedAt: null,
  prUrl: null,
};

const COMPLETED_JOB_FIXTURE: Job = {
  ...JOB_FIXTURE,
  id: "e2e-job-done-02",
  state: "failed",
  failureReason: "Out of memory",
  completedAt: new Date(Date.now() - 5_000).toISOString(),
};

const BOARD_PROJECT_URL = "/projects/id/e2e-project-1/board";
const BOARD_PROJECT: ProjectResponse = {
  id: "e2e-project-1", name: "example-app", repoPaths: [REPOSITORY],
  createdAt: JOB_FIXTURE.createdAt, updatedAt: JOB_FIXTURE.updatedAt,
};

test.describe("React #185 – kanban renders job cards without infinite loop", () => {
  test("project board renders job cards fetched via REST without crashing", async ({
    page,
  }) => {
    // Capture console errors — React logs caught errors to console even when
    // an ErrorBoundary handles them (both in dev and prod builds).
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    // Send one buffered heartbeat, then keep the disconnected state stable
    // while the legitimate failed board read completes its bounded retries.
    let streams = 0;
    await page.route("**/api/events*", async (route) => {
      if (streams++ > 0) return route.abort();
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

    let board = makeProjectBoard(BOARD_PROJECT);
    await setupProjectBoardMocks(page, BOARD_PROJECT, () => board);
    await page.goto(BOARD_PROJECT_URL);
    await expect(page.getByLabel("Persisted Project totals")).toContainText("0 standalone Jobs");
    await expect(page.getByText("No work on this page.")).toHaveCount(4);
    await expect(page.getByText("Something went wrong")).not.toBeVisible();

    const boardUrl = "**/api/settings/projects/e2e-project-1/board";
    await page.route(boardUrl, async (route) => {
      await route.fulfill({ status: 503, json: { detail: "Fixture board temporarily unavailable" } });
    });
    await page.getByRole("button", { name: "Refresh Board", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Unable to refresh Project Board", { timeout: 10_000 });
    await expect(page.getByLabel("Persisted Project totals")).toContainText("0 standalone Jobs");
    await expect(page.getByText("Something went wrong")).not.toBeVisible();
    await page.unroute(boardUrl);
    board = makeProjectBoard(BOARD_PROJECT, { in_progress: [JOB_FIXTURE], failed: [COMPLETED_JOB_FIXTURE] });
    await page.route(boardUrl, async (route) => { await route.fulfill({ json: board }); });
    await page.getByRole("button", { name: "Refresh Board", exact: true }).click();

    // The running job appears in the kanban board card. Use .first() to guard
    // against Playwright's strict-mode violation if the id also appears elsewhere.
    await expect(page.getByText(JOB_FIXTURE.id).first()).toBeVisible({
      timeout: 5_000,
    });

    // The failed job appears in the Failed kanban column.
    await expect(page.getByText(COMPLETED_JOB_FIXTURE.id).first()).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.getByLabel("Persisted Project totals")).toContainText("2 standalone Jobs");
    await expect(page.getByRole("region", { name: "In Progress" }).getByRole("link", { name: "Open Job" }))
      .toHaveAttribute("href", `/jobs/${JOB_FIXTURE.id}`);
    await expect(page.getByRole("region", { name: "Failed" }).getByRole("link", { name: "Open Job" }))
      .toHaveAttribute("href", `/jobs/${COMPLETED_JOB_FIXTURE.id}`);

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
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    let board = makeProjectBoard(BOARD_PROJECT);
    let reads = 0;
    await setupProjectBoardMocks(page, BOARD_PROJECT, () => { reads++; return board; });
    let releaseEvents!: () => void;
    const released = new Promise<void>((resolve) => { releaseEvents = resolve; });
    await page.route(`**/api/jobs/${JOB_FIXTURE.id}`, async (route) => {
      await route.fulfill({ json: JOB_FIXTURE });
    });
    await page.route("**/api/events*", async (route) => {
      await released;
      await route.fulfill({
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "X-Accel-Buffering": "no",
        },
        body: sseBody([
          { event: "session.heartbeat", data: {} },
          { event: "snapshot", data: { jobs: [JOB_FIXTURE, COMPLETED_JOB_FIXTURE], pendingApprovals: [] } },
          { event: "job.state_changed", data: {
            id: "board-state-1", session_id: JOB_FIXTURE.id, kind: "job.state_changed",
            timestamp: JOB_FIXTURE.updatedAt, payload: { state: "running" }, metadata: {},
          } },
        ]),
      });
    });

    await page.goto(BOARD_PROJECT_URL);
    await expect(page.getByLabel("Persisted Project totals")).toContainText("0 standalone Jobs");
    await expect(page.getByText("No work on this page.")).toHaveCount(4);
    const readsBeforeEvents = reads;
    board = makeProjectBoard(BOARD_PROJECT, { in_progress: [JOB_FIXTURE], failed: [COMPLETED_JOB_FIXTURE] });
    releaseEvents();

    // Buffered SSE invalidates the persisted board rather than deriving cards from the Job page.
    await expect(page.getByText(JOB_FIXTURE.id).first()).toBeVisible({
      timeout: 8_000,
    });
    await expect(page.getByText(COMPLETED_JOB_FIXTURE.id).first()).toBeVisible({
      timeout: 8_000,
    });
    expect(reads).toBeGreaterThan(readsBeforeEvents);
    await expect(page.getByLabel("Persisted Project totals")).toContainText("2 standalone Jobs");
    await expect(page.getByRole("region", { name: "In Progress" })).toContainText("Job: running");
    await expect(page.getByRole("region", { name: "Failed" })).toContainText("Job: failed");

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
