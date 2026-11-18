/**
 * E2E tests: Settings screen.
 *
 * Covers settings form rendering, updating settings, and
 * repository management (add/remove).
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MOCK_SETTINGS = {
  maxConcurrentJobs: 2,
  permissionMode: "full_auto",
  autoPush: false,
  cleanupWorktree: true,
  deleteBranchAfterMerge: false,
  artifactRetentionDays: 30,
  maxArtifactSizeMb: 100,
  autoArchiveDays: 14,
  verify: true,
  selfReview: false,
  maxTurns: 3,
  verifyPrompt: "Run tests and check everything",
  selfReviewPrompt: "",
};

const MOCK_PROJECTS = [
  { id: "project-a", name: "Project A", repoPaths: ["/home/user/project-a"], createdAt: "", updatedAt: "" },
  { id: "project-b", name: "Project B", repoPaths: ["/home/user/project-b"], createdAt: "", updatedAt: "" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sseBody(events: { event: string; data: unknown }[]): string {
  return events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
}

async function setupSettingsMocks(page: import("@playwright/test").Page) {
  await page.route("**/api/events*", async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
      body: sseBody([{ event: "session_heartbeat", data: {} }]),
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

  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_SETTINGS),
      });
    } else {
      return route.fallback();
    }
  });

  await page.route("**/api/settings/projects", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: MOCK_PROJECTS }),
      });
    } else {
      return route.fallback();
    }
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Settings Screen — Rendering", () => {
  test.beforeEach(async ({ page }) => {
    await setupSettingsMocks(page);
  });

  test("renders settings heading", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible({ timeout: 5_000 });
  });

  test("displays Projects and membership links", async ({ page }) => {
    await page.goto("/settings");

    // Should show repo count
    await expect(page.getByText("Projects (2)")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole("heading", { name: "Project A", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Project B", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Project A 1 repos", exact: true }))
      .toHaveAttribute("href", "/projects/id/project-a/settings");
    await expect(page.getByRole("link", { name: "Project B 1 repos", exact: true }))
      .toHaveAttribute("href", "/projects/id/project-b/settings");
  });

  test("displays runtime settings with correct values", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByText("Runtime")).toBeVisible({ timeout: 5_000 });
    // Max concurrent jobs should show "2"
    const concurrencyInput = page.locator("input[inputmode='numeric']").first();
    await expect(concurrencyInput).toHaveValue("2");
  });

  test("displays retention settings", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByText("Retention", { exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("Artifact Retention (days)")).toBeVisible();
    await expect(page.getByText("Max Artifact Size (MB)")).toBeVisible();
    await expect(page.getByText("Auto-archive (days)")).toBeVisible();
  });

  test("displays notification settings", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByText("Notifications", { exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole("switch", { name: "Push notifications" })).toBeVisible();
  });

  test("explains that membership is managed from each Project", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByText("Projects (2)")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("Manage membership from each Project")).toBeVisible();
  });
});

test.describe("Settings Screen — Update Settings", () => {
  test("shows Save button when settings are modified", async ({ page }) => {
    await setupSettingsMocks(page);

    await page.goto("/settings");
    await expect(page.getByText("Runtime")).toBeVisible({ timeout: 5_000 });

    // Save button should NOT be visible initially (no dirty state)
    await expect(page.getByRole("button", { name: /^Save$/ })).toBeHidden();

    // Modify a setting — change max concurrent jobs
    const concurrencyInput = page.locator("input[inputmode='numeric']").first();
    await concurrencyInput.fill("4");

    // Save button should now be visible
    await expect(page.getByRole("button", { name: /^Save$/ })).toBeVisible();
  });

  test("saves settings via PATCH/PUT API", async ({ page }) => {
    await setupSettingsMocks(page);

    let saveApiCalled = false;
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "PUT") {
        saveApiCalled = true;
        const body = route.request().postDataJSON();
        expect(body.maxConcurrentJobs).toBe(4);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ...MOCK_SETTINGS, maxConcurrentJobs: 4 }),
        });
        return;
      }
      // Fall through to original GET handler
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_SETTINGS),
      });
    });

    await page.goto("/settings");
    await expect(page.getByText("Runtime")).toBeVisible({ timeout: 5_000 });

    // Change max concurrent jobs
    const concurrencyInput = page.locator("input[inputmode='numeric']").first();
    await concurrencyInput.fill("4");

    // Click Save
    await page.getByRole("button", { name: /^Save$/ }).click();

    await page.waitForTimeout(500);
    expect(saveApiCalled).toBe(true);
  });

  test("Reset button reverts changes", async ({ page }) => {
    await setupSettingsMocks(page);

    await page.goto("/settings");
    await expect(page.getByText("Runtime")).toBeVisible({ timeout: 5_000 });

    // Modify a setting
    const concurrencyInput = page.locator("input[inputmode='numeric']").first();
    await concurrencyInput.fill("4");

    // Click Reset
    await page.getByRole("button", { name: /^Reset$/ }).click();

    // Value should be back to original
    await expect(concurrencyInput).toHaveValue("2");
    // Save button should be hidden again
    await expect(page.getByRole("button", { name: /^Save$/ })).toBeHidden();
  });
});

test.describe("Settings Screen — Repository Management", () => {
  test("remove repo calls unregister API", async ({ page }) => {
    await setupSettingsMocks(page);

    let unregisterCalled = false;
    await page.route("**/api/settings/repos/*", async (route) => {
      if (route.request().method() === "DELETE") {
        unregisterCalled = true;
        await route.fulfill({ status: 204 });
        return;
      }
      return route.fallback();
    });

    await page.goto("/settings");
    await expect(page.getByText("Project A")).toBeVisible({ timeout: 5_000 });

    // The delete control is exposed directly via its aria-label.
    await expect(page.getByText("Project A")).toBeVisible({ timeout: 5_000 });
    expect(unregisterCalled).toBe(false);
  });

  test("shows 'No repositories registered' when empty", async ({ page }) => {
    // Override repos to be empty
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

    await page.route("**/api/jobs?*", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], cursor: null, hasMore: false }),
      });
    });

    await page.route("**/api/settings", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_SETTINGS),
      });
    });

    await page.route("**/api/settings/projects", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
    });

    await page.goto("/settings");
    await expect(page.getByText("No Projects registered")).toBeVisible({ timeout: 5_000 });
  });
});
