/**
 * E2E tests: Approval flow.
 *
 * Covers approval rendering in the CuratedFeed, approve/reject actions,
 * and SSE-driven approval_requested events.
 */

import { test, expect } from "@playwright/test";
import { makeJob, mockLegacyRecipe, sseBody, setupBaseMocks } from "./helpers";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const NOW = new Date().toISOString();

const MOCK_JOB = makeJob({ state: "waiting_for_approval" });

const MOCK_APPROVAL = {
  id: "approval-1",
  jobId: "job-1",
  description: "Agent wants to run: npm install lodash",
  proposedAction: "npm install lodash",
  requestedAt: NOW,
  resolvedAt: null,
  resolution: null,
  requiresExplicitApproval: false,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Set up mocks for job detail page with pending approvals. */
async function setupApprovalMocks(
  page: import("@playwright/test").Page,
  approvals: unknown[] = [MOCK_APPROVAL],
) {
  await setupBaseMocks(page, [MOCK_JOB]);
  await mockLegacyRecipe(page, MOCK_JOB.id);

  await page.route("**/api/jobs/job-1/snapshot*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job: MOCK_JOB,
        logs: [],
        transcript: [],
        diff: [],
        approvals,
        timeline: [],
      }),
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
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
  });
  await page.route("**/api/jobs/job-1/approvals*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(approvals),
    });
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Approval Banner", () => {
  test("shows approval card when pending approvals exist", async ({ page }) => {
    await setupApprovalMocks(page);
    await page.goto("/jobs/job-1");

    await expect(page.getByText("Agent wants to run: npm install lodash")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("pre", { hasText: "npm install lodash" })).toBeVisible();
  });

  test("shows multiple approval cards when multiple pending", async ({ page }) => {
    const secondApproval = {
      ...MOCK_APPROVAL,
      id: "approval-2",
      description: "Agent wants to write to package.json",
      proposedAction: null,
      requestedAt: new Date(Date.now() + 1000).toISOString(),
    };
    await setupApprovalMocks(page, [MOCK_APPROVAL, secondApproval]);
    await page.goto("/jobs/job-1");

    await expect(page.getByText("Agent wants to run: npm install lodash")).toBeVisible({ timeout: 8_000 });
    await expect(page.getByText("Agent wants to write to package.json")).toBeVisible();
  });

  test("shows Approve and Reject buttons for each approval", async ({ page }) => {
    await setupApprovalMocks(page);
    await page.goto("/jobs/job-1");

    await expect(page.getByText("Agent wants to run: npm install lodash")).toBeVisible({ timeout: 8_000 });
    await expect(page.locator("button", { hasText: "Approve" }).first()).toBeVisible();
    await expect(page.locator("button", { hasText: "Reject" }).first()).toBeVisible();
  });
});

test.describe("Approve Action", () => {
  test("clicking Approve calls resolve API with approved", async ({ page }) => {
    await setupApprovalMocks(page);

    let resolveApiCalled = false;
    await page.route("**/api/approvals/approval-1/resolve", async (route) => {
      resolveApiCalled = true;
      const body = route.request().postDataJSON();
      expect(body.resolution).toBe("approved");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...MOCK_APPROVAL, resolution: "approved", resolvedAt: NOW }),
      });
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("Agent wants to run: npm install lodash")).toBeVisible({ timeout: 8_000 });
    await page.locator("button", { hasText: "Approve" }).first().click();

    await page.waitForTimeout(500);
    expect(resolveApiCalled).toBe(true);
  });
});

test.describe("Reject Action", () => {
  test("clicking Reject calls resolve API with rejected", async ({ page }) => {
    await setupApprovalMocks(page);

    let resolveApiCalled = false;
    await page.route("**/api/approvals/approval-1/resolve", async (route) => {
      resolveApiCalled = true;
      const body = route.request().postDataJSON();
      expect(body.resolution).toBe("rejected");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...MOCK_APPROVAL, resolution: "rejected", resolvedAt: NOW }),
      });
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("Agent wants to run: npm install lodash")).toBeVisible({ timeout: 8_000 });
    await page.locator("button", { hasText: "Reject" }).first().click();

    await page.waitForTimeout(500);
    expect(resolveApiCalled).toBe(true);
  });
});

test.describe("SSE-Driven Approval Events", () => {
  test("approval_requested SSE event shows approval on job detail", async ({ page }) => {
    await mockLegacyRecipe(page, MOCK_JOB.id);
    // SSE delivers an approval_requested event after the initial snapshot
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
          {
            event: "permission.requested",
            data: {
              id: "evt-1",
              kind: "permission.requested",
              session_id: "job-1",
              timestamp: NOW,
              payload: {
                approval_id: "approval-1",
                description: "Agent wants to execute: rm -rf /tmp/cache",
                proposed_action: "rm -rf /tmp/cache",
              },
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
        body: JSON.stringify({ items: [MOCK_JOB], cursor: null, hasMore: false }),
      });
    });
    await page.route("**/api/settings", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });
    await page.route("**/api/settings/repos", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });
    await page.route("**/api/sdks", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ default: "copilot", sdks: [{ id: "copilot", name: "GitHub Copilot", enabled: true, status: "ready" }] }),
      });
    });
    await page.route("**/api/models", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
    });
    await page.route("**/api/jobs/job-1", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_JOB) });
    });
    await page.route("**/api/jobs/job-1/snapshot*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ job: MOCK_JOB, logs: [], transcript: [], diff: [], approvals: [], timeline: [] }),
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

    await page.goto("/jobs/job-1");
    await expect(page.getByText("Agent wants to execute: rm -rf /tmp/cache")).toBeVisible({ timeout: 8_000 });
  });
});
