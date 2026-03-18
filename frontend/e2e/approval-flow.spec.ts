/**
 * E2E tests: Approval flow.
 *
 * Covers SSE-driven approval_requested events, the approval banner UI,
 * approve/reject actions, and the "Approve All" trust session flow.
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

const MOCK_APPROVAL = {
  id: "approval-1",
  jobId: "job-1",
  description: "Agent wants to run: npm install lodash",
  proposedAction: "npm install lodash",
  requestedAt: NOW,
  resolvedAt: null,
  resolution: null,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sseBody(events: { event: string; data: unknown }[]): string {
  return events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
}

/** Set up mocks for job detail page with pending approvals via SSE snapshot. */
async function setupApprovalMocks(
  page: import("@playwright/test").Page,
  approvals: unknown[] = [MOCK_APPROVAL],
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
        { event: "snapshot", data: { jobs: [MOCK_JOB], pendingApprovals: approvals } },
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
  test("shows approval banner when pending approvals exist", async ({ page }) => {
    await setupApprovalMocks(page);

    await page.goto("/jobs/job-1");

    // Approval banner should appear with the description
    await expect(page.getByText("Approval Required")).toBeVisible({ timeout: 8_000 });
    await expect(page.getByText("Agent wants to run: npm install lodash")).toBeVisible();
    // Should show the proposed action in a code block
    await expect(page.locator("pre", { hasText: "npm install lodash" })).toBeVisible();
  });

  test("shows pending approval count", async ({ page }) => {
    const secondApproval = {
      ...MOCK_APPROVAL,
      id: "approval-2",
      description: "Agent wants to write to package.json",
      proposedAction: null,
    };
    await setupApprovalMocks(page, [MOCK_APPROVAL, secondApproval]);

    await page.goto("/jobs/job-1");

    // Should show "2 pending approvals"
    await expect(page.getByText("2 pending approvals")).toBeVisible({ timeout: 8_000 });
  });

  test("shows Approve All button", async ({ page }) => {
    await setupApprovalMocks(page);

    await page.goto("/jobs/job-1");

    await expect(page.getByText("Approval Required")).toBeVisible({ timeout: 8_000 });
    await expect(page.locator("button", { hasText: "Approve All" })).toBeVisible();
  });
});

test.describe("Approve Action", () => {
  test("clicking Approve calls resolve API with 'approved'", async ({ page }) => {
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

    await expect(page.getByText("Approval Required")).toBeVisible({ timeout: 8_000 });
    await page.locator("button", { hasText: "Approve" }).first().click();

    await page.waitForTimeout(500);
    expect(resolveApiCalled).toBe(true);
  });
});

test.describe("Reject Action", () => {
  test("clicking Reject calls resolve API with 'rejected'", async ({ page }) => {
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

    await expect(page.getByText("Approval Required")).toBeVisible({ timeout: 8_000 });
    await page.locator("button", { hasText: "Reject" }).click();

    await page.waitForTimeout(500);
    expect(resolveApiCalled).toBe(true);
  });
});

test.describe("Trust Session (Approve All)", () => {
  test("clicking Approve All calls trust API", async ({ page }) => {
    await setupApprovalMocks(page);

    let trustApiCalled = false;
    await page.route("**/api/jobs/job-1/approvals/trust", async (route) => {
      trustApiCalled = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ resolved: 1 }),
      });
    });

    await page.goto("/jobs/job-1");

    await expect(page.getByText("1 pending approval")).toBeVisible({ timeout: 8_000 });
    await page.locator("button", { hasText: "Approve All" }).click();

    await page.waitForTimeout(500);
    expect(trustApiCalled).toBe(true);
  });
});

test.describe("SSE-Driven Approval Events", () => {
  test("approval_requested SSE event shows banner on job detail", async ({ page }) => {
    // Start with NO pending approvals, then deliver one via SSE
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
          // Deliver approval_requested after snapshot
          {
            event: "approval_requested",
            data: {
              approvalId: "approval-1",
              jobId: "job-1",
              description: "Agent wants to execute: rm -rf /tmp/cache",
              proposedAction: "rm -rf /tmp/cache",
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

    await page.goto("/jobs/job-1");

    // The SSE-driven approval should appear
    await expect(page.getByText("Approval Required")).toBeVisible({ timeout: 8_000 });
    await expect(page.getByText("Agent wants to execute: rm -rf /tmp/cache")).toBeVisible();
  });
});
