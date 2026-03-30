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

const MOCK_REPOS = ["/home/user/project-a", "/home/user/project-b"];

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

  await page.route("**/api/settings/repos", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: MOCK_REPOS }),
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

  test("displays registered repositories", async ({ page }) => {
    await page.goto("/settings");

    // Should show repo count
    await expect(page.getByText("Repositories (2)")).toBeVisible({ timeout: 5_000 });
    // Should show repo paths
    await expect(page.getByText("/home/user/project-a")).toBeVisible();
    await expect(page.getByText("/home/user/project-b")).toBeVisible();
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

  test("displays verification settings", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByText("Verification", { exact: true })).toBeVisible({ timeout: 5_000 });
    // Verify checkbox should be checked
    const verifyCheckbox = page.locator("input[type='checkbox']").first();
    await expect(verifyCheckbox).toBeChecked();
  });

  test("shows Add Repository button", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByText("Repositories (2)")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("button", { hasText: "Add Repository" })).toBeVisible();
  });
});

test.describe("Settings Screen — Update Settings", () => {
  test("shows Save button when settings are modified", async ({ page }) => {
    await setupSettingsMocks(page);

    await page.goto("/settings");
    await expect(page.getByText("Runtime")).toBeVisible({ timeout: 5_000 });

    // Save button should NOT be visible initially (no dirty state)
    await expect(page.locator("button", { hasText: "Save" })).toBeHidden();

    // Modify a setting — change max concurrent jobs
    const concurrencyInput = page.locator("input[inputmode='numeric']").first();
    await concurrencyInput.fill("4");

    // Save button should now be visible
    await expect(page.locator("button", { hasText: "Save" })).toBeVisible();
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
    await page.locator("button", { hasText: "Save" }).click();

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
    await page.locator("button", { hasText: "Reset" }).click();

    // Value should be back to original
    await expect(concurrencyInput).toHaveValue("2");
    // Save button should be hidden again
    await expect(page.locator("button", { hasText: "Save" })).toBeHidden();
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
    await expect(page.getByText("/home/user/project-a")).toBeVisible({ timeout: 5_000 });

    // Hover over the first repo row to reveal the delete button, then click it
    const repoText = page.getByText("/home/user/project-a");
    await repoText.hover();
    // The delete button is a sibling within the same group row
    await repoText.locator("..").locator("button").click();

    // Confirm the removal in the dialog
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.locator("button", { hasText: "Remove" }).click();

    await page.waitForTimeout(500);
    expect(unregisterCalled).toBe(true);
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

    await page.route("**/api/settings/repos", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
    });

    await page.goto("/settings");
    await expect(page.getByText("No repositories registered")).toBeVisible({ timeout: 5_000 });
  });
});
