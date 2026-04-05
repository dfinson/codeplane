/**
 * Playwright capture script — screenshots of the real CodePlane frontend
 * with mock API data injected via route interception.
 *
 * Usage:
 *   1. Start the Vite dev server:  cd frontend && npx vite --port 5173
 *   2. Run captures:               cd demo-video && npx tsx capture/run.ts
 *   3. Screenshots land in:        demo-video/public/captures/
 */

import { chromium, type Page, type BrowserContext } from "playwright";
import * as path from "path";
import * as fs from "fs";
import {
  mockJobs,
  mockHealth,
  mockSdks,
  mockModels,
  mockSettings,
  mockRepoList,
  mockScorecard,
  mockModelComparison,
  mockAnalyticsTools,
  mockAnalyticsRepos,
  mockCostDrivers,
  mockObservations,
  mockOverview,
  runningJobTranscript,
  runningJobTimeline,
  runningJobPlan,
  approvalJobApprovals,
  reviewJobDiff,
} from "./fixtures";

const BASE = "http://localhost:5173";
const OUT = path.resolve(__dirname, "..", "public", "captures");

// ---------------------------------------------------------------------------
// Route interception: mock every API endpoint the frontend calls
// ---------------------------------------------------------------------------

async function setupRoutes(page: Page) {
  // Health
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: mockHealth }),
  );

  // SDKs
  await page.route("**/api/sdks", (route) =>
    route.fulfill({ json: mockSdks }),
  );

  // Models
  await page.route("**/api/models**", (route) =>
    route.fulfill({ json: mockModels }),
  );

  // Settings
  await page.route("**/api/settings", (route) => {
    if (route.request().url().includes("/repos")) return route.continue();
    return route.fulfill({ json: mockSettings });
  });
  await page.route("**/api/settings/repos", (route) =>
    route.fulfill({ json: mockRepoList }),
  );
  await page.route("**/api/settings/repos/*", (route) =>
    route.fulfill({
      json: {
        path: route.request().url().split("/repos/")[1],
        originUrl: "https://github.com/acme/repo.git",
        baseBranch: "main",
        currentBranch: "main",
        activeJobCount: 1,
        platform: "github",
      },
    }),
  );

  // Jobs list
  await page.route("**/api/jobs?**", (route) =>
    route.fulfill({
      json: { items: mockJobs, cursor: null, hasMore: false },
    }),
  );
  await page.route(/\/api\/jobs$/, (route) =>
    route.fulfill({
      json: { items: mockJobs, cursor: null, hasMore: false },
    }),
  );

  // Job detail — match specific job IDs
  await page.route(/\/api\/jobs\/j-\w+\/snapshot/, (route) => {
    const jobId = route.request().url().match(/\/jobs\/(j-\w+)\//)?.[1];
    const job = mockJobs.find((j) => j.id === jobId) ?? mockJobs[0];
    return route.fulfill({
      json: {
        job,
        logs: [],
        transcript: jobId === "j-0007" ? runningJobTranscript : [],
        diff: jobId === "j-0003" ? reviewJobDiff : [],
        approvals: jobId === "j-0008" ? approvalJobApprovals : [],
        timeline: jobId === "j-0007" ? runningJobTimeline : [],
      },
    });
  });

  await page.route(/\/api\/jobs\/j-\w+\/transcript/, (route) => {
    const jobId = route.request().url().match(/\/jobs\/(j-\w+)\//)?.[1];
    return route.fulfill({
      json: jobId === "j-0007" ? runningJobTranscript : [],
    });
  });

  await page.route(/\/api\/jobs\/j-\w+\/timeline/, (route) => {
    const jobId = route.request().url().match(/\/jobs\/(j-\w+)\//)?.[1];
    return route.fulfill({
      json: jobId === "j-0007" ? runningJobTimeline : [],
    });
  });

  await page.route(/\/api\/jobs\/j-\w+\/diff/, (route) => {
    const jobId = route.request().url().match(/\/jobs\/(j-\w+)\//)?.[1];
    return route.fulfill({
      json: jobId === "j-0003" ? reviewJobDiff : [],
    });
  });

  await page.route(/\/api\/jobs\/j-\w+\/approvals/, (route) => {
    const jobId = route.request().url().match(/\/jobs\/(j-\w+)\//)?.[1];
    return route.fulfill({
      json: jobId === "j-0008" ? approvalJobApprovals : [],
    });
  });

  await page.route(/\/api\/jobs\/j-\w+\/artifacts/, (route) =>
    route.fulfill({ json: { items: [] } }),
  );

  await page.route(/\/api\/jobs\/j-\w+\/telemetry/, (route) =>
    route.fulfill({
      json: {
        turns: [],
        tokensByRole: {},
        costByCategory: {},
        totalCostUsd: 0.42,
        totalTokens: 28400,
        cacheStats: { hits: 0, misses: 0 },
      },
    }),
  );

  // Single job fetch
  await page.route(/\/api\/jobs\/j-\w+$/, (route) => {
    const jobId = route.request().url().match(/\/jobs\/(j-\w+)$/)?.[1];
    const job = mockJobs.find((j) => j.id === jobId);
    if (job) return route.fulfill({ json: job });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });

  // Analytics
  await page.route("**/api/analytics/scorecard**", (route) =>
    route.fulfill({ json: mockScorecard }),
  );
  await page.route("**/api/analytics/model-comparison**", (route) =>
    route.fulfill({ json: mockModelComparison }),
  );
  await page.route("**/api/analytics/tools**", (route) =>
    route.fulfill({ json: mockAnalyticsTools }),
  );
  await page.route("**/api/analytics/repos**", (route) =>
    route.fulfill({ json: mockAnalyticsRepos }),
  );
  await page.route("**/api/analytics/cost-drivers**", (route) =>
    route.fulfill({ json: mockCostDrivers }),
  );
  await page.route("**/api/analytics/observations**", (route) =>
    route.fulfill({ json: mockObservations }),
  );
  await page.route("**/api/analytics/overview**", (route) =>
    route.fulfill({ json: mockOverview }),
  );
  await page.route("**/api/analytics/jobs**", (route) =>
    route.fulfill({ json: { period: 7, jobs: [], total: 0 } }),
  );
  await page.route("**/api/analytics/pricing**", (route) =>
    route.fulfill({ json: {} }),
  );
  await page.route("**/api/analytics/fleet-cost-drivers**", (route) =>
    route.fulfill({ json: mockCostDrivers }),
  );

  // SSE — never respond so EventSource stays connecting (no error loop)
  await page.route("**/api/events**", (route) => {
    // Intentionally never fulfilled — the request hangs.
    // We patch the connection status in the DOM after load.
  });
}

// ---------------------------------------------------------------------------
// DOM fixup: patch the connection status badge to show "Connected"
// ---------------------------------------------------------------------------

async function patchConnectionBadge(page: Page) {
  await page.evaluate(() => {
    const badge = document.querySelector(
      '[aria-label*="Connection status"]',
    ) as HTMLElement | null;
    if (!badge) return;
    badge.setAttribute("aria-label", "Connection status: Connected");
    // Find the dot (first child span or first element)
    const dot = badge.querySelector("span:first-child") as HTMLElement | null;
    if (dot) {
      dot.style.backgroundColor = "hsl(142 71% 45%)"; // green-500
      dot.style.boxShadow = "0 0 0 2px hsl(142 71% 45% / 0.2)";
    }
    // Update text
    const walker = document.createTreeWalker(
      badge,
      NodeFilter.SHOW_TEXT,
      null,
    );
    let text: Text | null;
    while ((text = walker.nextNode() as Text | null)) {
      if (text.textContent?.trim()) {
        text.textContent = "Connected";
        break;
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Capture functions
// ---------------------------------------------------------------------------

async function captureDashboard(page: Page) {
  console.log("  → Dashboard (desktop)");
  await page.goto(BASE);
  // Wait for kanban columns to appear
  await page.waitForSelector('[role="region"]', { timeout: 10_000 });
  await page.waitForTimeout(800); // let all cards settle
  await patchConnectionBadge(page);
  await page.screenshot({
    path: path.join(OUT, "dashboard-desktop.png"),
    type: "png",
  });
}

async function captureJobRunning(page: Page) {
  console.log("  → Job detail — running (live tab)");
  await page.goto(`${BASE}/jobs/j-0007`);
  // Wait for the job title or job detail to render
  await page.waitForSelector("text=Add customer email search", {
    timeout: 10_000,
  });
  await page.waitForTimeout(1200); // let transcript render
  await patchConnectionBadge(page);
  await page.screenshot({
    path: path.join(OUT, "job-running-live.png"),
    type: "png",
  });
}

async function captureJobDiff(page: Page) {
  console.log("  → Job detail — diff (changes tab)");
  await page.goto(`${BASE}/jobs/j-0003`);
  await page.waitForSelector("text=Add pagination", { timeout: 10_000 });
  await page.waitForTimeout(800);
  // Click the Changes tab
  const changesTab = page.getByRole("tab", { name: /changes/i });
  if (await changesTab.isVisible()) {
    await changesTab.click();
    await page.waitForTimeout(1500); // Monaco needs time to mount
  }
  await patchConnectionBadge(page);
  await page.screenshot({
    path: path.join(OUT, "job-diff.png"),
    type: "png",
  });
}

async function captureJobApproval(page: Page) {
  console.log("  → Job detail — approval banner");
  await page.goto(`${BASE}/jobs/j-0008`);
  await page.waitForSelector("text=Add keyboard shortcut hints", {
    timeout: 10_000,
  });
  await page.waitForTimeout(1000);
  await patchConnectionBadge(page);
  await page.screenshot({
    path: path.join(OUT, "job-approval.png"),
    type: "png",
  });
}

async function captureAnalytics(page: Page) {
  console.log("  → Analytics (top section)");
  await page.goto(`${BASE}/analytics`);
  // Wait for the spinner to disappear and content to render
  await page.waitForSelector("text=Budget", { timeout: 15_000 });
  await page.waitForTimeout(1200);
  await patchConnectionBadge(page);
  await page.screenshot({
    path: path.join(OUT, "analytics-top.png"),
    type: "png",
  });

  // Scroll to model comparison
  console.log("  → Analytics (model comparison)");
  await page.evaluate(() => {
    // Find the model comparison heading and scroll to it
    const heading = Array.from(document.querySelectorAll("h3, h2")).find(
      (el) =>
        el.textContent?.toLowerCase().includes("model") &&
        el.textContent?.toLowerCase().includes("comparison"),
    );
    heading?.scrollIntoView({ behavior: "instant", block: "start" });
  });
  await page.waitForTimeout(500);
  await page.screenshot({
    path: path.join(OUT, "analytics-models.png"),
    type: "png",
  });
}

async function captureMobileDashboard(context: BrowserContext) {
  console.log("  → Dashboard (mobile)");
  const page = await context.newPage();
  await setupRoutes(page);
  await page.goto(BASE);
  await page.waitForSelector('button:has-text("In Progress")', {
    timeout: 10_000,
  });
  await page.waitForTimeout(800);
  await patchConnectionBadge(page);
  await page.screenshot({
    path: path.join(OUT, "dashboard-mobile.png"),
    type: "png",
  });

  // Also capture mobile job detail
  console.log("  → Job detail (mobile)");
  await page.goto(`${BASE}/jobs/j-0007`);
  await page.waitForSelector("text=Add customer email search", {
    timeout: 10_000,
  });
  await page.waitForTimeout(1200);
  await patchConnectionBadge(page);
  await page.screenshot({
    path: path.join(OUT, "job-mobile.png"),
    type: "png",
  });

  await page.close();
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  console.log(`\nCapturing CodePlane screenshots → ${OUT}\n`);
  fs.mkdirSync(OUT, { recursive: true });

  const browser = await chromium.launch({ headless: true });

  // Desktop context: 1920×1080 @ 2x = 3840×2160
  console.log("Desktop captures (1920×1080 @ 2x):");
  const desktopCtx = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  const page = await desktopCtx.newPage();

  // Fake EventSource to prevent reconnect loops
  await page.addInitScript(() => {
    (window as any).EventSource = class FakeEventSource extends EventTarget {
      readyState = 0;
      url: string;
      withCredentials = false;
      onopen: ((ev: Event) => void) | null = null;
      onmessage: ((ev: MessageEvent) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;

      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSED = 2;

      constructor(url: string | URL, _init?: EventSourceInit) {
        super();
        this.url = String(url);
        queueMicrotask(() => {
          this.readyState = 1;
          const openEvt = new Event("open");
          this.onopen?.(openEvt as any);
          this.dispatchEvent(openEvt);
          // Fire snapshot with all jobs
          const snapshotData = JSON.stringify({
            jobs: (window as any).__mockJobs ?? [],
            pendingApprovals: (window as any).__mockApprovals ?? [],
          });
          const snapshotEvt = new MessageEvent("snapshot", { data: snapshotData });
          this.dispatchEvent(snapshotEvt);
          // Heartbeat
          const hb = new MessageEvent("session_heartbeat", { data: "{}" });
          this.dispatchEvent(hb);
        });
      }
      close() {
        this.readyState = 2;
      }
    } as any;
  });

  // Inject mock data for the fake EventSource to use
  await page.addInitScript(
    (data: { jobs: unknown[]; approvals: unknown[] }) => {
      (window as any).__mockJobs = data.jobs;
      (window as any).__mockApprovals = data.approvals;
    },
    { jobs: mockJobs, approvals: approvalJobApprovals },
  );

  await setupRoutes(page);

  await captureDashboard(page);
  await captureJobRunning(page);
  await captureJobDiff(page);
  await captureJobApproval(page);
  await captureAnalytics(page);

  await desktopCtx.close();

  // Mobile context: 390×844 @ 3x (iPhone 14 Pro)
  console.log("\nMobile captures (390×844 @ 3x):");
  const mobileCtx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3,
    colorScheme: "dark",
    isMobile: true,
    hasTouch: true,
  });
  // Apply the same init scripts to the mobile context
  const mobilePage = await mobileCtx.newPage();
  await mobilePage.addInitScript(() => {
    (window as any).EventSource = class FakeEventSource extends EventTarget {
      readyState = 0;
      url: string;
      withCredentials = false;
      onopen: ((ev: Event) => void) | null = null;
      onmessage: ((ev: MessageEvent) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSED = 2;
      constructor(url: string | URL, _init?: EventSourceInit) {
        super();
        this.url = String(url);
        queueMicrotask(() => {
          this.readyState = 1;
          const openEvt = new Event("open");
          this.onopen?.(openEvt as any);
          this.dispatchEvent(openEvt);
          const hb = new MessageEvent("session_heartbeat", { data: "{}" });
          this.dispatchEvent(hb);
        });
      }
      close() {
        this.readyState = 2;
      }
    } as any;
  });
  await setupRoutes(mobilePage);
  await mobilePage.goto(BASE);
  await mobilePage.waitForSelector('button:has-text("In Progress")', {
    timeout: 10_000,
  });
  await mobilePage.waitForTimeout(800);
  await patchConnectionBadge(mobilePage);
  await mobilePage.screenshot({
    path: path.join(OUT, "dashboard-mobile.png"),
    type: "png",
  });

  console.log("  → Job detail (mobile)");
  await mobilePage.goto(`${BASE}/jobs/j-0007`);
  await mobilePage.waitForSelector("text=Add customer email search", {
    timeout: 10_000,
  });
  await mobilePage.waitForTimeout(1200);
  await patchConnectionBadge(mobilePage);
  await mobilePage.screenshot({
    path: path.join(OUT, "job-mobile.png"),
    type: "png",
  });

  await mobileCtx.close();
  await browser.close();

  // List captures
  console.log("\n✓ Captures complete:");
  const files = fs.readdirSync(OUT);
  for (const f of files) {
    const stat = fs.statSync(path.join(OUT, f));
    console.log(`  ${f} (${(stat.size / 1024).toFixed(0)} KB)`);
  }
}

main().catch((err) => {
  console.error("Capture failed:", err);
  process.exit(1);
});
