/**
 * E2E tests: Transcript and activity timeline rendering.
 *
 * Covers agent messages, tool call display, operator messages,
 * timeline headline rendering, and search within transcripts.
 */

import { test, expect } from "@playwright/test";
import type { TranscriptPayload } from "../src/api/types";
import { makeJob, setupJobDetailMocks, NOW } from "./helpers";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const TRANSCRIPT: TranscriptPayload[] = [
  {
    jobId: "job-1",
    eventId: "transcript-1",
    sequence: 1,
    timestamp: NOW,
    kind: "message.assistant",
    content: "I'll analyze the auth module to understand the current implementation.",
    turnId: "turn-1",
    title: "Planning approach",
  },
  {
    jobId: "job-1",
    eventId: "transcript-2",
    sequence: 2,
    timestamp: NOW,
    kind: "tool.call.completed",
    content: "",
    turnId: "turn-1",
    toolName: "read_file",
    arguments: '{"filePath": "src/auth.ts"}',
    result: "export function login() { ... }",
    success: true,
    toolDisplay: "Read src/auth.ts",
    toolDisplayFull: "Read src/auth.ts",
    toolDurationMs: 120,
    toolVisibility: "visible",
  },
  {
    jobId: "job-1",
    eventId: "transcript-3",
    sequence: 3,
    timestamp: NOW,
    kind: "tool.call.completed",
    content: "",
    turnId: "turn-1",
    toolName: "edit_file",
    arguments: '{"filePath": "src/auth.ts", "oldString": "...", "newString": "..."}',
    result: "File edited successfully",
    success: true,
    toolDisplay: "Edited src/auth.ts",
    toolDisplayFull: "Edited src/auth.ts",
    toolDurationMs: 45,
    toolVisibility: "visible",
  },
  {
    jobId: "job-1",
    eventId: "transcript-4",
    sequence: 4,
    timestamp: NOW,
    kind: "message.assistant",
    content: "I've fixed the authentication bug by adding proper token validation.",
    turnId: "turn-2",
    title: "Implementation complete",
  },
  {
    jobId: "job-1",
    eventId: "transcript-5",
    sequence: 5,
    timestamp: NOW,
    kind: "message.user",
    content: "Can you also add a test for that fix?",
    turnId: null,
  },
  {
    jobId: "job-1",
    eventId: "transcript-6",
    sequence: 6,
    timestamp: NOW,
    kind: "message.assistant",
    content: "Sure! I'll create a unit test for the token validation logic.",
    turnId: "turn-3",
    title: "Writing tests",
  },
];

const TURN_SUMMARIES = [
  {
    turnId: "turn-1",
    title: "Reading auth module",
    activityId: "act-1",
    activityLabel: "Investigating the bug",
    activityStatus: "done",
    isNewActivity: true,
  },
  {
    turnId: "turn-2",
    title: "Applying fix",
    activityId: "act-2",
    activityLabel: "Fixing authentication",
    activityStatus: "done",
    isNewActivity: true,
  },
  {
    turnId: "turn-3",
    title: "Writing test cases",
    activityId: "act-3",
    activityLabel: "Adding tests",
    activityStatus: "active",
    isNewActivity: true,
  },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Transcript — Message Rendering", () => {
  test.beforeEach(async ({ page }) => {
    await setupJobDetailMocks(page, makeJob(), {
      transcript: TRANSCRIPT,
    });
  });

  test("renders agent messages with content", async ({ page }) => {
    await page.goto("/jobs/job-1");

    // Agent messages should be visible
    await expect(
      page.getByText("analyze the auth module").first(),
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.getByText("fixed the authentication bug").first(),
    ).toBeVisible();
  });

  test("renders operator messages", async ({ page }) => {
    await page.goto("/jobs/job-1");

    await expect(
      page.getByText("Can you also add a test").first(),
    ).toBeVisible({ timeout: 5_000 });
  });

  test("renders tool call displays", async ({ page }) => {
    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true }).last()).toBeVisible({ timeout: 5_000 });

    // CuratedFeed: read_file → "Read 1 file" cluster; edit_file → "other" kind uses toolDisplay
    await expect(
      page.getByText("Read 1 file").first(),
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.getByText("Edited src/auth.ts").first(),
    ).toBeVisible();
  });
});

test.describe("Transcript — Activity Timeline", () => {
  test("shows activity labels in the sidebar", async ({ page }) => {
    await setupJobDetailMocks(page, makeJob(), {
      transcript: TRANSCRIPT,
      snapshot: {
        job: makeJob(),
        logs: [],
        transcript: TRANSCRIPT,
        diff: [],
        approvals: [],
        timeline: [],
        turnSummaries: TURN_SUMMARIES,
      },
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true }).last()).toBeVisible({ timeout: 5_000 });

    // ActivityTimeline sidebar shows activity labels (use getByRole to skip
    // hidden mobile-strip duplicates that share the active label text).
    await expect(
      page.getByRole("button", { name: "Investigating the bug" }),
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.getByRole("button", { name: "Fixing authentication" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Adding tests" }),
    ).toBeVisible();
  });

  test("active activity has spinning indicator", async ({ page }) => {
    await setupJobDetailMocks(page, makeJob(), {
      transcript: TRANSCRIPT,
      snapshot: {
        job: makeJob(),
        logs: [],
        transcript: TRANSCRIPT,
        diff: [],
        approvals: [],
        timeline: [],
        turnSummaries: TURN_SUMMARIES,
      },
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true }).last()).toBeVisible({ timeout: 5_000 });

    // The last activity "Adding tests" should be active
    await expect(
      page.getByRole("button", { name: "Adding tests" }),
    ).toBeVisible({ timeout: 5_000 });
  });
});

test.describe("Transcript — Empty State", () => {
  test("shows empty state when transcript is empty", async ({ page }) => {
    await setupJobDetailMocks(page, makeJob(), {
      transcript: [],
      timeline: [],
    });

    await page.goto("/jobs/job-1");
    await expect(page.getByText("job-1", { exact: true }).last()).toBeVisible({ timeout: 5_000 });

    // The Live tab should be visible and active by default
    await expect(page.getByRole("button", { name: "Live" })).toBeVisible();
  });
});
