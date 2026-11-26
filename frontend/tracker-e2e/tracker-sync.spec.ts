import { expect, test } from "@playwright/test";
import type { ChildProcess } from "node:child_process";
import { spawn, spawnSync } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import type { ProjectBoard, TaskLinkInspection } from "../src/api/types";

const DEFAULT_BASE_URL = "http://127.0.0.1:18765";
const JIRA_TOKEN = "local-e2e-token";
const JIRA_EMAIL = "tracker-e2e@codeplane.local";
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

let backend: ChildProcess | undefined;
let backendOutput = "";
let codeplaneHome = "";
let jiraStatus = "To Do";
let jiraRequestCount = 0;
let jiraFailureMode = false;
let closeJira: (() => Promise<void>) | undefined;
let backendBaseUrl = "";
let projectId = "";
let credentialId = "";
let trackerLinkId = "";
let taskLinkId = "";
let memberRepoPath = "";
let addedRepoPath = "";
let backendSpawnError: Error | undefined;

interface TrackerLinksResponse {
  trackerLinks: Array<{
    id: string;
    summary: {
      tickets: Array<{ title: string; status: string }>;
      lastSyncedAt: string | null;
      lastError: string | null;
    } | null;
  }>;
}

interface TaskLinksResponse {
  items: Array<{
    id: string;
    trackerTicketRef: string | null;
    state: string;
    jobId: string | null;
  }>;
}

test.describe.configure({ mode: "serial" });

async function requestJson<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: init?.body
      ? { "Content-Type": "application/json", ...init.headers }
      : init?.headers,
  });
  if (!response.ok) {
    throw new Error(
      `${init?.method ?? "GET"} ${url} failed: ${response.status} ${await response.text()}`,
    );
  }
  return response.json() as Promise<T>;
}

async function startFakeJira(): Promise<string> {
  const jiraAuthorization = `Basic ${Buffer.from(`${JIRA_EMAIL}:${JIRA_TOKEN}`).toString("base64")}`;
  const server = createServer((request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    if (request.method === "GET" && url.pathname === "/rest/api/3/search/jql") {
      if (jiraFailureMode) {
        response.writeHead(503, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "temporary Jira outage" }));
        return;
      }
      const validRequest =
        request.headers.authorization === jiraAuthorization &&
        url.searchParams.get("jql")?.includes('project = "TEST"') &&
        url.searchParams.get("fields") === "summary,status" &&
        url.searchParams.get("maxResults") === "100";
      if (!validRequest) {
        response.writeHead(400, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "invalid Jira request" }));
        return;
      }

      jiraRequestCount += 1;
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(
        JSON.stringify({
          issues: [
            {
              id: "10001",
              key: "TEST-1",
              fields: {
                summary: "Deterministic Jira ticket",
                status: { name: jiraStatus },
              },
            },
          ],
        }),
      );
      return;
    }
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: "not found" }));
  });

  await new Promise<void>((resolveReady, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveReady);
  });
  const address = server.address() as AddressInfo;
  closeJira = () =>
    new Promise<void>((resolveClosed, reject) => {
      if (!server.listening) {
        resolveClosed();
        return;
      }
      server.close((error) => (error ? reject(error) : resolveClosed()));
    });
  return `http://127.0.0.1:${address.port}`;
}

function initializeGitRepo(path: string): void {
  const commands = [
    ["init"],
    ["config", "user.email", "tracker-e2e@codeplane.local"],
    ["config", "user.name", "CodePlane Tracker E2E"],
    ["add", "README.md"],
    ["commit", "-m", "Initial fixture"],
  ];
  for (const args of commands) {
    const result = spawnSync("git", args, {
      cwd: path,
      windowsHide: true,
      encoding: "utf8",
    });
    if (result.status !== 0) {
      throw new Error(
        `git ${args.join(" ")} failed in ${path}: ${result.stderr || result.stdout}`,
      );
    }
  }
}

async function waitForBackend(url: string): Promise<void> {
  const deadline = Date.now() + 120_000;
  let lastError: unknown;
  while (Date.now() < deadline) {
    if (backendSpawnError) {
      throw new Error(`CodePlane failed to start: ${backendSpawnError.message}`);
    }
    if (backend && (backend.exitCode !== null || backend.signalCode !== null)) {
      throw new Error(
        `CodePlane exited before becoming ready (${backend.exitCode ?? backend.signalCode}).\n${backendOutput}`,
      );
    }
    try {
      const response = await fetch(`${url}/api/health`);
      if (response.ok) return;
      lastError = `${response.status} ${await response.text()}`;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 500));
  }
  throw new Error(
    `CodePlane did not become ready: ${String(lastError)}\n${backendOutput}`,
  );
}

async function assertPortAvailable(port: number): Promise<void> {
  const probe = createServer();
  await new Promise<void>((resolveReady, reject) => {
    probe.once("error", reject);
    probe.listen(port, "127.0.0.1", resolveReady);
  });
  await new Promise<void>((resolveClosed, reject) => {
    probe.close((error) => (error ? reject(error) : resolveClosed()));
  });
}

async function stopProcess(child: ChildProcess): Promise<void> {
  if (
    child.exitCode !== null ||
    child.signalCode !== null ||
    child.pid === undefined
  ) {
    return;
  }
  const exited = new Promise<void>((resolveExit) =>
    child.once("exit", () => resolveExit()),
  );
  if (process.platform === "win32") {
    const result = spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      windowsHide: true,
      encoding: "utf8",
    });
    if (result.status !== 0) {
      throw new Error(`taskkill failed: ${result.stderr || result.stdout}`);
    }
  } else {
    process.kill(-child.pid, "SIGTERM");
  }

  const exitedPromptly = await Promise.race([
    exited.then(() => true),
    new Promise<false>((resolveTimeout) =>
      setTimeout(() => resolveTimeout(false), 5_000),
    ),
  ]);
  if (!exitedPromptly && process.platform !== "win32") {
    process.kill(-child.pid, "SIGKILL");
    await exited;
  } else if (!exitedPromptly) {
    throw new Error(`CodePlane process ${child.pid} did not exit after taskkill`);
  }
}

async function cleanup(): Promise<void> {
  const results = await Promise.allSettled([
    backend ? stopProcess(backend) : Promise.resolve(),
  ]);
  results.push(
    ...(await Promise.allSettled([
      closeJira ? closeJira() : Promise.resolve(),
      codeplaneHome
        ? rm(codeplaneHome, { recursive: true, force: true })
        : Promise.resolve(),
    ])),
  );
  const failures = results.filter(
    (result): result is PromiseRejectedResult => result.status === "rejected",
  );
  if (failures.length > 0) {
    throw new AggregateError(
      failures.map((failure) => failure.reason),
      "Tracker E2E cleanup failed",
    );
  }
}

async function waitForScheduledSummary(): Promise<TrackerLinksResponse> {
  const deadline = Date.now() + 80_000;
  let lastObserved: TrackerLinksResponse | undefined;
  while (Date.now() < deadline) {
    lastObserved = await requestJson<TrackerLinksResponse>(
      `${backendBaseUrl}/api/projects/${projectId}/tracker-links`,
    );
    const ticket = lastObserved.trackerLinks[0]?.summary?.tickets[0];
    if (
      ticket?.title === "Deterministic Jira ticket" &&
      ticket.status === "To Do"
    ) {
      return lastObserved;
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 750));
  }
  throw new Error(
    `Scheduled tracker summary was not persisted. Last response: ${JSON.stringify(lastObserved)}`,
  );
}

test.beforeAll(async () => {
  backendBaseUrl =
    process.env.CODEPLANE_TRACKER_E2E_BASE_URL ?? DEFAULT_BASE_URL;
  const parsedBaseUrl = new URL(backendBaseUrl);
  backendBaseUrl = parsedBaseUrl.origin;
  const backendPort = Number(parsedBaseUrl.port);

  try {
    if (
      parsedBaseUrl.protocol !== "http:" ||
      parsedBaseUrl.hostname !== "127.0.0.1" ||
      parsedBaseUrl.pathname !== "/" ||
      parsedBaseUrl.search !== "" ||
      parsedBaseUrl.hash !== "" ||
      !Number.isInteger(backendPort) ||
      backendPort < 1
    ) {
      throw new Error(
        "CODEPLANE_TRACKER_E2E_BASE_URL must be an http://127.0.0.1 URL with an explicit port",
      );
    }
    await assertPortAvailable(backendPort);
    const jiraBaseUrl = await startFakeJira();
    codeplaneHome = await mkdtemp(resolve(tmpdir(), "codeplane-tracker-e2e-"));
    memberRepoPath = resolve(codeplaneHome, "member-repo");
    addedRepoPath = resolve(codeplaneHome, "added-repo");
    await Promise.all([
      mkdir(memberRepoPath, { recursive: true }),
      mkdir(addedRepoPath, { recursive: true }),
    ]);
    await Promise.all([
      writeFile(resolve(memberRepoPath, "README.md"), "# Member repository\n"),
      writeFile(resolve(addedRepoPath, "README.md"), "# Added repository\n"),
    ]);
    initializeGitRepo(memberRepoPath);
    initializeGitRepo(addedRepoPath);

    const launchScript = [
      "import uvicorn",
      "from backend.app_factory import create_app",
      `uvicorn.run(create_app(), host="127.0.0.1", port=${backendPort}, log_level="warning")`,
    ].join("; ");

    backend = spawn(
      "uv",
      ["run", "--no-sync", "python", "-c", launchScript],
      {
        cwd: repoRoot,
        detached: true,
        env: {
          ...process.env,
          CODEPLANE_HOME: codeplaneHome,
          CODEPLANE_E2E_FAKE_AGENT: "1",
          PYTHONIOENCODING: "utf-8",
          PYTHONUTF8: "1",
        },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    backend.once("error", (error) => {
      backendSpawnError = error;
    });
    backend.stdout?.on("data", (chunk) => {
      backendOutput += chunk.toString();
    });
    backend.stderr?.on("data", (chunk) => {
      backendOutput += chunk.toString();
    });

    await waitForBackend(backendBaseUrl);
    const project = await requestJson<{ id: string }>(
      `${backendBaseUrl}/api/settings/projects`,
      {
        method: "POST",
        body: JSON.stringify({
          name: "Tracker E2E Project",
          repoPaths: [repoRoot, memberRepoPath],
        }),
      },
    );
    projectId = project.id;
    const credential = await requestJson<{ id: string }>(
      `${backendBaseUrl}/api/settings/credentials`,
      {
        method: "POST",
        body: JSON.stringify({
          provider: "jira",
          label: "Local Jira",
          baseUrl: jiraBaseUrl,
          pat: JIRA_TOKEN,
          email: JIRA_EMAIL,
        }),
      },
    );
    credentialId = credential.id;
  } catch (error) {
    try {
      await cleanup();
    } catch (cleanupError) {
      throw new AggregateError(
        [error, cleanupError],
        "Tracker E2E setup and cleanup failed",
      );
    }
    throw error;
  }
});

test.afterAll(async () => {
  await cleanup();
});

test("navigates project-first scope and manages membership and tracker attachment", async ({
  page,
}) => {
  await page.goto(`${backendBaseUrl}/projects`);

  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await page.getByRole("button", { name: /Tracker E2E Project/ }).click();
  await expect(page).toHaveURL(
    `${backendBaseUrl}/projects/id/${projectId}/board`,
  );
  await expect(page.getByRole("region", { name: "In Progress" })).toBeVisible();

  await page.getByLabel("Repository", { exact: true }).selectOption(memberRepoPath);
  await expect(page).toHaveURL(
    `${backendBaseUrl}/projects/id/${projectId}/repos/${encodeURIComponent(memberRepoPath)}/jobs`,
  );
  await expect(
    page.getByRole("navigation", { name: "Repository navigation" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Project Settings" }),
  ).toBeVisible();

  await page.getByPlaceholder("/absolute/path/to/repo").fill(addedRepoPath);
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByRole("button", { name: "Save project" }).click();
  await expect(page.getByText("Project updated")).toBeVisible();
  await expect(page.getByText("3 total")).toBeVisible();

  await page.getByRole("button", { name: `Remove ${addedRepoPath}` }).click();
  await page.getByRole("button", { name: "Save project" }).click();
  await expect(
    page.getByRole("heading", {
      name: "Remove repositories from this Project?",
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Remove repositories" }).click();
  await expect(page.getByText("2 total")).toBeVisible();

  await expect(page.getByLabel("Select tracker credential")).toHaveValue(
    credentialId,
  );
  await page.getByLabel("Board or org ref").fill("TEST");
  await page.getByRole("button", { name: "Attach" }).click();
  await expect(page.getByText("Board link attached")).toBeVisible();
  await expect(page.getByText("Local Jira").last()).toBeVisible();

  const links = await requestJson<TrackerLinksResponse>(
    `${backendBaseUrl}/api/projects/${projectId}/tracker-links`,
  );
  expect(links.trackerLinks).toHaveLength(1);
  trackerLinkId = links.trackerLinks[0]?.id ?? "";
  expect(trackerLinkId).not.toBe("");
});

test("retries provider refresh and assigns a tracker ticket through the UI", async ({
  page,
}) => {
  await waitForScheduledSummary();
  expect(jiraRequestCount).toBeGreaterThanOrEqual(2);

  await page.goto(`${backendBaseUrl}/settings`);
  await expect(
    page.getByText("Deterministic Jira ticket", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("To Do", { exact: true })).toBeVisible();

  jiraFailureMode = true;
  await page.getByRole("button", { name: "Refresh TEST" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Tracker provider read failed" }),
  ).toBeVisible();
  await expect(page.getByText("Deterministic Jira ticket", { exact: true })).toBeVisible();
  await expect(page.getByText("To Do", { exact: true })).toBeVisible();

  jiraFailureMode = false;
  jiraStatus = "In Progress";
  const requestsBeforeRetry = jiraRequestCount;
  await page.getByRole("button", { name: "Refresh TEST" }).click();
  await expect(page.getByText("In Progress", { exact: true })).toBeVisible();
  expect(jiraRequestCount).toBeGreaterThanOrEqual(requestsBeforeRetry + 1);

  await page.getByRole("button", { name: "Assign task for TEST-1" }).click();
  await page.getByLabel("Task repository").selectOption(memberRepoPath);
  await page.getByLabel("Task prompt").fill("Implement TEST-1 from the synced Jira ticket");
  await page.getByRole("button", { name: "Create TaskLink" }).click();
  await expect(page.getByText("Assigned TEST-1 as a task.")).toBeVisible();

  const taskLinks = await requestJson<TaskLinksResponse>(
    `${backendBaseUrl}/api/settings/projects/${projectId}/task-links`,
  );
  const assigned = taskLinks.items.find(
    (item) => item.trackerTicketRef === "TEST-1",
  );
  expect(assigned?.state).toBe("ready");
  taskLinkId = assigned?.id ?? "";
  expect(taskLinkId).not.toBe("");
});

test("starts the assigned TaskLink, preserves context, and returns through its chain chat", async ({
  page,
}) => {
  await page.goto(`${backendBaseUrl}/projects/id/${projectId}/board`);
  const taskCard = page.getByRole("article", { name: "Work item: TEST-1" });
  await expect(taskCard).toContainText("Task TEST-1: ready");
  await expect(taskCard.getByRole("link", { name: "Inspect task" })).toHaveAttribute(
    "href", `/projects/id/${projectId}/board/task/${taskLinkId}`,
  );
  const inspectionResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith(`/api/settings/projects/${projectId}/task-links/${taskLinkId}`) &&
      response.request().method() === "GET",
  );
  await taskCard.getByRole("link", { name: "Inspect task" }).click();
  const inspectionResponse = await inspectionResponsePromise;
  expect(inspectionResponse.ok()).toBe(true);
  const inspection: TaskLinkInspection = await inspectionResponse.json();
  expect(inspection.task).toMatchObject({
    id: taskLinkId, projectId, state: "ready", trackerTicketRef: "TEST-1", trackerLinkId, repoPath: memberRepoPath,
  });
  const inspector = page.getByRole("region", { name: "Task inspector" });
  await expect(inspector).toContainText("Implement TEST-1 from the synced Jira ticket");

  const startResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(
        `/api/settings/projects/${projectId}/task-links/${taskLinkId}/start`,
      ) && response.request().method() === "POST",
  );
  await inspector.getByRole("button", { name: "Start inspected task" }).click();
  const startResponse = await startResponsePromise;
  expect(startResponse.status()).toBe(200);
  const started = (await startResponse.json()) as {
    state: string;
    jobId: string | null;
  };
  expect(started.state).toBe("running");
  expect(started.jobId).toBeTruthy();

  await expect(page).toHaveURL(`${backendBaseUrl}/jobs/${started.jobId}`);
  const boardResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith(`/api/settings/projects/${projectId}/board`) &&
      response.request().method() === "GET",
  );
  await page.goto(`${backendBaseUrl}/projects/id/${projectId}/board`);
  const boardResponse = await boardResponsePromise;
  expect(boardResponse.ok()).toBe(true);
  const board: ProjectBoard = await boardResponse.json();
  const cards = Object.values(board.columns).flatMap((column) => column.items);
  expect(cards).toHaveLength(1);
  expect(cards[0].job?.id).toBe(started.jobId);
  expect(board.totals).toMatchObject({ taskCount: 1, linkedJobCount: 1, standaloneJobCount: 0 });
  for (const column of ["ready_blocked", "in_progress", "needs_decision", "failed"] as const) {
    expect(board.columns[column]!.items).toHaveLength(board.totals.workCounts[column]!);
  }
  const linkedCard = page.getByRole("article", { name: "Work item: TEST-1" });
  await expect(linkedCard).toContainText("Task TEST-1: running");
  await expect(linkedCard).toContainText(memberRepoPath);
  const openJob = linkedCard.getByRole("link", { name: "Open Job", exact: true });
  await expect(openJob).toHaveAttribute("href", `/jobs/${started.jobId}`);
  await openJob.click();
  await expect(page).toHaveURL(`${backendBaseUrl}/jobs/${started.jobId}`);

  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(
    breadcrumb.getByRole("link", { name: "Tracker E2E Project" }),
  ).toBeVisible();
  await expect(
    breadcrumb.getByRole("link", { name: "member-repo" }),
  ).toBeVisible();
  await expect(breadcrumb.getByRole("link", { name: "TEST-1" })).toBeVisible();

  await breadcrumb.getByRole("link", { name: "Tracker E2E Project" }).click();
  await expect(page).toHaveURL(
    `${backendBaseUrl}/projects/id/${projectId}/board`,
  );
  await page.getByRole("button", { name: "Chats", exact: true }).click();

  await page.getByPlaceholder("New chat title").fill("Supervise TEST-1");
  await expect(page.getByLabel("Chat Project")).toHaveValue(projectId);
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page).toHaveURL(
    new RegExp(`/projects/id/${projectId}/chats/[^/]+$`),
  );
  const chatId = page.url().split("/").pop() ?? "";
  expect(chatId).not.toBe("");

  // Seed the real backend association so the linked-chain return path can be
  // verified independently of the TaskLink start flow.
  await requestJson(`${backendBaseUrl}/api/chats/${chatId}/attach-chain`, {
    method: "POST",
    body: JSON.stringify({ taskLinkId }),
  });
  await page.reload();
  await expect(
    page.getByRole("link", { name: "Supervising chain" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Supervising chain" }).click();
  await expect(page).toHaveURL(
    `${backendBaseUrl}/projects/id/${projectId}/board/task/${taskLinkId}`,
  );
  await page.getByLabel("Back to overview").click();
  await expect(page).toHaveURL(
    `${backendBaseUrl}/projects/id/${projectId}`,
  );
});
