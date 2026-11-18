import { test, expect } from "@playwright/test";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";

let server: ChildProcess;
let directory = "";
let repository = "";
let home = "";
let output = "";
const base = "http://127.0.0.1:18769";
test.describe.configure({ mode: "serial" });
test.beforeAll(async () => {
  directory = await mkdtemp(path.join(tmpdir(), "recipe-browser-"));
  repository = path.join(directory, "repository");
  await mkdir(repository);
  execFileSync("git", ["init", "-b", "main", repository]);
  execFileSync("git", ["-C", repository, "config", "user.name", "Recipe Browser"]);
  execFileSync("git", ["-C", repository, "config", "user.email", "recipe@example.invalid"]);
  await writeFile(path.join(repository, "README.md"), "Recipe browser fixture\n");
  execFileSync("git", ["-C", repository, "add", "."]);
  execFileSync("git", ["-C", repository, "commit", "-m", "Fixture"]);
  home = path.join(directory, "home");
  await mkdir(home);
  await writeFile(path.join(home, "config.yaml"), "server:\n  host: 127.0.0.1\n  port: 18769\nruntime:\n  max_concurrent_jobs: 1\n");
  await startServer();
});

async function startServer() {
  server = spawn("uv", ["run", "--no-sync", "python", "-m", "backend.tests.recipe_browser_server"], {
    cwd: path.resolve(".."),
    env: { ...process.env, CODEPLANE_HOME: home, RECIPE_BROWSER_PORT: "18769", CODEPLANE_E2E_FAKE_AGENT: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  server.stdout?.on("data", (chunk) => { output += chunk.toString(); });
  server.stderr?.on("data", (chunk) => { output += chunk.toString(); });
  for (let count = 0; count < 120; count++) {
    if (server.exitCode !== null) throw new Error(output);
    try { if ((await fetch(`${base}/api/health`)).ok) return; } catch { /* Wait for this owned fixture server. */ }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Fixture server did not become ready: ${output}`);
}

async function stopServer() {
  if (!server || server.exitCode !== null) return;
  const stopped = new Promise<void>((resolve) => {
    const timer = setTimeout(() => { server.kill("SIGKILL"); }, 15_000);
    server.once("exit", () => { clearTimeout(timer); resolve(); });
  });
  server.kill("SIGTERM");
  await stopped;
}

test.afterAll(async () => {
  await stopServer();
  if (directory) await rm(directory, { recursive: true, force: true });
});

test("restarts at a human gate and accepts only its exact retained decision", async ({ page, request }) => {
  const projectResponse = await request.post("/api/settings/projects", { data: { name: "Recipe qualification", repoPaths: [repository] } });
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json();
  const draft = await (await request.post("/api/recipes", { data: {
    name: "Restarted human gate", entrySteps: ["decision"], steps: [{
      id: "decision", title: "Approve exact evidence", runner: { kind: "human", proposedAction: "Accept this exact retained input snapshot." },
      capabilities: [], workspace: { mode: "none" },
      inputs: { proposal: { type: "string", binding: { source: "literal", value: "Pinned proposal" } } },
      outputs: {}, routes: { approved: { terminal: "succeeded" }, rejected: { terminal: "failed" } },
    }],
  } })).json();
  const revision = await (await request.post(`/api/recipes/${draft.id}/publish`, { data: { expectedVersion: 1 } })).json();
  const create = await request.post("/api/jobs", { data: {
    projectId: project.id, repo: repository, prompt: "Decide the retained proposal",
    title: "Human restart gate", branch: "human-restart", worktreeName: "human-restart",
    preset: "supervised", recipe: { revisionId: revision.id },
  } });
  expect(create.ok(), await create.text()).toBeTruthy();
  const job = await create.json();
  const current = async () => (await (await request.get(`/api/jobs/${job.id}/recipe/current`)).json()).run;
  const run = await current();
  let step: { id: string; currentAttemptId: string; state: string };
  await expect.poll(async () => {
    step = (await (await request.get(`/api/jobs/${job.id}/recipe/runs/${run.id}/steps`)).json()).items[0];
    return step.state;
  }).toBe("needs_decision");
  const originalAttempt = step!.currentAttemptId;
  await stopServer();
  await startServer();
  await page.goto(`/jobs/${job.id}?run=${run.id}&step=${step!.id}&attempt=${originalAttempt}`);
  const gate = page.getByRole("region", { name: "Exact human gate decision" });
  await expect(gate).toBeVisible({ timeout: 30_000 });
  await expect(gate.getByText("Accept this exact retained input snapshot.")).toBeVisible();
  await gate.getByRole("checkbox").check();
  await gate.getByRole("button", { name: "Approve exact proposal" }).click();
  await expect.poll(async () => (await current()).state).toBe("succeeded");
  const history = await (await request.get(`/api/jobs/${job.id}/recipe/runs/${run.id}/steps/${step!.id}`)).json();
  expect(history.attempts).toHaveLength(1);
  expect(history.attempts[0].id).toBe(originalAttempt);
  expect(history.attempts[0].outcome).toBe("approved");
});

test("publishes, executes a real command and inspects exact evidence and review rationale", async ({ page, request }) => {
  const project = (await (await request.get("/api/settings/projects")).json()).items[0];
  const draftResponse = await request.post("/api/recipes", { data: {
    name: "Actual command browser journey", entrySteps: ["check"], steps: [{
      id: "check", title: "Actual Git check", instructions: "Inspect the pinned code.",
      runner: { kind: "command", argv: ["git", "status", "--porcelain"], timeoutSeconds: 30 },
      capabilities: ["read", "shell", "write", "external_access"], workspace: { mode: "mutable" },
      outputs: { result: { type: "object" }, code: { type: "code_ref" } },
      routes: { succeeded: { terminal: "succeeded" }, failed: { terminal: "failed" } },
    }],
  } });
  expect(draftResponse.ok()).toBeTruthy();
  const draft = await draftResponse.json();
  await page.goto(`/recipes/${draft.id}`);
  await expect(page.getByRole("heading", { name: "Step definition" })).toBeVisible();
  await page.getByRole("button", { name: "Publish revision" }).click();
  await expect(page.getByLabel("Publication receipt")).toBeVisible();
  const revisions = await (await request.get(`/api/recipes/${draft.id}/revisions`)).json();
  const create = await request.post("/api/jobs", { data: {
    projectId: project.id, repo: repository, prompt: "Run the actual Git check",
    title: "Actual Git check", branch: "recipe-browser", worktreeName: "recipe-browser",
    preset: "autonomous", recipe: { revisionId: revisions.items[0].id },
  } });
  expect(create.ok(), await create.text()).toBeTruthy();
  const job = await create.json();
  await page.goto(`/jobs/${job.id}`);
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Actual Git check", exact: true }).last()).toBeVisible();
  await expect.poll(async () => (await (await request.get(`/api/jobs/${job.id}`)).json()).state, { timeout: 60_000 }).toBe("review");
  await page.reload();
  await page.getByText("Output", { exact: true }).click();
  await expect(page.getByText("Exact argv")).toBeVisible();
  await expect(page.getByText("Standard output", { exact: true })).toBeVisible();
  await expect(page.getByText(/historical run. Use explicit Job Resume/)).toBeVisible();
  const run = (await (await request.get(`/api/jobs/${job.id}/recipe/current`)).json()).run;
  const steps = (await (await request.get(`/api/jobs/${job.id}/recipe/runs/${run.id}/steps`)).json()).items;
  const detail = await (await request.get(`/api/jobs/${job.id}/recipe/runs/${run.id}/steps/${steps[0].id}/attempts/${steps[0].currentAttemptId}`)).json();
  expect(detail.attempt.result.evidence.exitStatus).toBe(0);
  expect(detail.attempt.executions).toEqual([]);
  await page.getByRole("button", { name: "Review", exact: true }).click();
  const review = page.getByRole("complementary", { name: "Evidence for this decision" });
  await expect(review).toBeVisible();
  await review.getByRole("checkbox").first().check();
  await review.getByRole("button", { name: "Retain selected review evidence" }).click();
  await expect(review.getByText("Selected code and evidence retained. Rationale has not been recorded.")).toBeVisible();
  await review.getByLabel("Review rationale").fill("Inspected the exact Git result and immutable code. Delivery is separate.");
  await review.getByRole("button", { name: "Record review rationale", exact: true }).click();
  await expect(review.getByText(/Review rationale recorded as review-/)).toBeVisible();
  expect((await (await request.get(`/api/jobs/${job.id}`)).json()).state).toBe("review");
  expect((await (await request.get(`/api/jobs/${job.id}/reviews`)).json()).items).toHaveLength(1);
  await page.goto(`/projects/id/${project.id}/board`);
  await expect(page.getByLabel("Persisted Project totals")).toContainText("2 standalone Jobs");
  await expect(page.getByRole("region", { name: "Needs a decision" })).toContainText("Actual Git check");
});
