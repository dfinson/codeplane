/**
 * E2E tests: Workspace browser & artifact viewer.
 *
 * Covers navigating to the Files and Artifacts tabs on the job detail
 * screen, verifying file tree rendering, file content preview, and
 * artifact list rendering.
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const NOW = new Date().toISOString();

const MOCK_JOB = {
  id: "job-1",
  title: "Test Job",
  prompt: "Add a feature",
  state: "completed",
  createdAt: NOW,
  updatedAt: NOW,
  completedAt: NOW,
  repo: "/tmp/test-repo",
  branch: "cpl/job-1",
  baseRef: "main",
  worktreePath: "/tmp/worktrees/job-1",
  prUrl: null,
  resolution: "merged",
  archivedAt: null,
  failureReason: null,
  progressHeadline: null,
  model: "claude-sonnet-4-5-20250514",
  sdk: "copilot",
};

const MOCK_WORKSPACE_ENTRIES = [
  { path: "src", type: "directory", sizeBytes: null },
  { path: "README.md", type: "file", sizeBytes: 1024 },
  { path: "package.json", type: "file", sizeBytes: 512 },
];

const MOCK_SRC_CHILDREN = [
  { path: "src/index.ts", type: "file", sizeBytes: 2048 },
  { path: "src/utils.ts", type: "file", sizeBytes: 768 },
];

const MOCK_FILE_CONTENT = `export function hello(): string {
  return "Hello, world!";
}`;

const MOCK_ARTIFACTS = [
  {
    id: "art-1",
    jobId: "job-1",
    name: "agent_summary.md",
    type: "agent_summary",
    mimeType: "text/markdown",
    sizeBytes: 2048,
    phase: "completion",
    createdAt: NOW,
  },
  {
    id: "art-2",
    jobId: "job-1",
    name: "diff_snapshot.patch",
    type: "diff_snapshot",
    mimeType: "text/plain",
    sizeBytes: 4096,
    phase: "completion",
    createdAt: NOW,
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sseBody(events: { event: string; data: unknown }[]): string {
  return events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
}

async function setupJobDetailMocks(page: import("@playwright/test").Page) {
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
        { event: "snapshot", data: { jobs: [MOCK_JOB], pendingApprovals: [] } },
      ]),
    });
  });

  await page.route("**/api/jobs?*", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [MOCK_JOB], cursor: null, hasMore: false }),
    });
  });

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
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Workspace Browser — Files Tab", () => {
  test.beforeEach(async ({ page }) => {
    await setupJobDetailMocks(page);

    // Mock workspace file listing (root)
    await page.route("**/api/jobs/job-1/workspace?*", async (route) => {
      const url = new URL(route.request().url());
      const path = url.searchParams.get("path");

      if (path === "src") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: MOCK_SRC_CHILDREN, cursor: null, hasMore: false }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: MOCK_WORKSPACE_ENTRIES, cursor: null, hasMore: false }),
        });
      }
    });

    // Catch workspace requests without query params too
    await page.route("**/api/jobs/job-1/workspace", async (route) => {
      if (route.request().url().includes("?")) return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: MOCK_WORKSPACE_ENTRIES, cursor: null, hasMore: false }),
      });
    });

    // Mock file content fetch
    await page.route("**/api/jobs/job-1/workspace/file*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ content: MOCK_FILE_CONTENT }),
      });
    });
  });

  test("file tree renders when switching to Files tab", async ({ page }) => {
    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    // Click the Files tab
    await page.getByRole("tab", { name: "Files" }).click();

    // File tree should show root entries
    await expect(page.getByText("src")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("README.md")).toBeVisible();
    await expect(page.getByText("package.json")).toBeVisible();
  });

  test("clicking a file loads its content", async ({ page }) => {
    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await page.getByRole("tab", { name: "Files" }).click();
    await expect(page.getByText("README.md")).toBeVisible({ timeout: 5_000 });

    // Click on a file
    await page.getByText("README.md").click();

    // The file content should appear (Monaco editor or text display)
    // "Select a file to preview" should no longer be visible
    await expect(page.getByText("Select a file to preview")).toBeHidden({ timeout: 5_000 });
  });

  test("expanding a directory loads its children", async ({ page }) => {
    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await page.getByRole("tab", { name: "Files" }).click();
    await expect(page.getByText("src")).toBeVisible({ timeout: 5_000 });

    // Click the directory to expand it
    await page.locator("button", { hasText: "src" }).click();

    // Children should appear
    await expect(page.getByText("index.ts")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("utils.ts")).toBeVisible();
  });

  test("shows 'Files' heading in tree panel", async ({ page }) => {
    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await page.getByRole("tab", { name: "Files" }).click();

    await expect(page.getByText("Files", { exact: false })).toBeVisible({ timeout: 5_000 });
  });
});

test.describe("Artifact Viewer — Artifacts Tab", () => {
  test.beforeEach(async ({ page }) => {
    await setupJobDetailMocks(page);

    // Mock artifact listing
    await page.route("**/api/jobs/job-1/artifacts*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: MOCK_ARTIFACTS }),
      });
    });
  });

  test("artifacts tab shows artifact list", async ({ page }) => {
    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    // Click the Artifacts tab
    await page.getByRole("tab", { name: "Artifacts" }).click();

    // Should show artifact names
    await expect(page.getByText("agent_summary.md")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("diff_snapshot.patch")).toBeVisible();
  });

  test("artifacts tab shows artifact types", async ({ page }) => {
    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await page.getByRole("tab", { name: "Artifacts" }).click();

    // Should show type badges
    await expect(page.getByText("agent_summary")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("diff_snapshot")).toBeVisible();
  });

  test("shows empty state when no artifacts", async ({ page }) => {
    // Override artifact mock to return empty
    await page.route("**/api/jobs/job-1/artifacts*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true })).toBeVisible({ timeout: 5_000 });

    await page.getByRole("tab", { name: "Artifacts" }).click();

    await expect(page.getByText("No artifacts available")).toBeVisible({ timeout: 5_000 });
  });
});
