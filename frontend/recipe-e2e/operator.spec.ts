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
      runner: { kind: "command", argv: ["sh", "-c", "printf '\\nFixture review change\\n' >> README.md; git diff --check"], timeoutSeconds: 30 },
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
  await page.goto(`/jobs/new?projectId=${project.id}`);
  await page.getByLabel("Launch recipe definition").selectOption(draft.id);
  await page.getByLabel("Launch recipe revision").selectOption(revisions.items[0].id);
  await page.locator("#prompt-input").fill("Run the actual Git check");
  await page.getByRole("button", { name: "Autonomous", exact: true }).click();
  const [create] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/api/jobs") && response.request().method() === "POST"),
    page.getByRole("button", { name: "Create Job", exact: true }).click(),
  ]);
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
  await expect(page.getByText("Recipe questions must target an exact live attempt. Use the Run inspector, or explicit Resume for a new run.")).toBeVisible();
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
  await expect(page.getByRole("region", { name: "Needs a decision" })).toContainText("Actual command browser journey");
});

test("pins a reusable block, joins isolated command branches and rejects only the exact human proposal", async ({ page, request }) => {
  const project = (await (await request.get("/api/settings/projects")).json()).items[0];
  const capabilities = ["read", "shell", "write", "external_access"];
  const child = await (await request.post("/api/recipes", { data: {
    name: "Pinned verification block", kind: "block", entrySteps: ["verify"], steps: [{
      id: "verify", title: "Verify pinned code", runner: { kind: "command", argv: ["git", "status", "--porcelain"] },
      capabilities, workspace: { mode: "mutable" },
      routes: { succeeded: { terminal: "succeeded" }, failed: { terminal: "failed" } },
    }],
  } })).json();
  const childRevision = await (await request.post(`/api/recipes/${child.id}/publish`, { data: { expectedVersion: 1 } })).json();
  const draftResponse = await request.post("/api/recipes", { data: {
    name: "Block and fanout join", entrySteps: ["block"], steps: [
      {
        id: "block", title: "Pinned block", runner: { kind: "block", revisionId: childRevision.id },
        capabilities, workspace: { mode: "mutable" },
        routes: { succeeded: { nextSteps: ["left", "right"] }, failed: { terminal: "failed" } },
      },
      ...["left", "right"].map((id) => ({
        id, title: `${id} isolated check`, runner: { kind: "command", argv: ["git", "status", "--porcelain"] },
        capabilities, workspace: { mode: "isolated", key: id }, dependsOn: [{ stepId: "block" }],
        outputs: { code: { type: "code_ref" } },
        routes: { succeeded: { nextSteps: ["join"] }, failed: { terminal: "failed" } },
      })),
      {
        id: "join", title: "Join exact code evidence",
        runner: { kind: "command", argv: ["git", "merge", "--no-edit", { input: "left", field: "commit" }, { input: "right", field: "commit" }], reconciles: ["left", "right"] },
        capabilities, workspace: { mode: "mutable" },
        dependsOn: [{ stepId: "left" }, { stepId: "right" }],
        inputs: Object.fromEntries(["left", "right"].map((id) => [id, { type: "code_ref", binding: { source: "output", stepId: id, output: "code" } }])),
        outputs: { result: { type: "object" } },
        routes: { succeeded: { nextSteps: ["gate"] }, failed: { terminal: "failed" } },
      },
      {
        id: "gate", title: "Review joined evidence", runner: { kind: "human", proposedAction: "Decide only this joined evidence." },
        capabilities: [], workspace: { mode: "none" }, dependsOn: [{ stepId: "join" }],
        inputs: { result: { type: "object", binding: { source: "output", stepId: "join", output: "result" } } },
        routes: { approved: { terminal: "succeeded" }, rejected: { terminal: "failed" } },
      },
    ],
  } });
  expect(draftResponse.ok(), await draftResponse.text()).toBeTruthy();
  const draft = await draftResponse.json();
  const published = await request.post(`/api/recipes/${draft.id}/publish`, { data: { expectedVersion: 1 } });
  expect(published.ok(), await published.text()).toBeTruthy();
  const revision = await published.json();
  const created = await request.post("/api/jobs", { data: {
    projectId: project.id, repo: repository, prompt: "Run block and joined checks",
    title: "Block fanout join", branch: "block-fanout", worktreeName: "block-fanout",
    preset: "autonomous", recipe: { revisionId: revision.id },
  } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const job = await created.json();
  const run = (await (await request.get(`/api/jobs/${job.id}/recipe/current`)).json()).run;
  expect(run.snapshot.blocks[childRevision.id].revisionId).toBe(childRevision.id);
  await page.goto(`/jobs/${job.id}`);
  const permissions = page.getByRole("region", { name: "Pending Job decisions" });
  await expect(permissions.getByText("Approve this exact recipe command invocation")).toBeVisible({ timeout: 20_000 });
  await expect(permissions.getByText(/git merge --no-edit/)).toBeVisible();
  await permissions.getByRole("button", { name: "Approve", exact: true }).click();
  let gate: { id: string; currentAttemptId: string; state: string } | undefined;
  try {
    await expect.poll(async () => {
      const steps = (await (await request.get(`/api/jobs/${job.id}/recipe/runs/${run.id}/steps`)).json()).items;
      gate = steps.find((step: { stepPath: string }) => step.stepPath === "gate");
      return gate?.state;
    }, { timeout: 20_000 }).toBe("needs_decision");
  } catch (error) {
    const steps = await (await request.get(`/api/jobs/${job.id}/recipe/runs/${run.id}/steps`)).json();
    const approvals = await (await request.get(`/api/jobs/${job.id}/approvals`)).json();
    throw new Error(`${String(error)}\nSteps:${JSON.stringify(steps)}\nApprovals:${JSON.stringify(approvals)}\nServer:${output.slice(-12000)}`);
  }
  await page.goto(`/jobs/${job.id}?run=${run.id}&step=${gate!.id}&attempt=${gate!.currentAttemptId}`);
  const decision = page.getByRole("region", { name: "Exact human gate decision" });
  await expect(decision).toBeVisible();
  await page.getByRole("button", { name: "Inputs", exact: true }).click();
  await expect(page.getByText("Producer outcome", { exact: true })).toBeVisible();
  await decision.getByRole("checkbox").check();
  await decision.getByRole("button", { name: "Reject exact proposal" }).click();
  await expect.poll(async () => (await (await request.get(`/api/jobs/${job.id}/recipe/current`)).json()).run.state).toBe("failed");
  const detail = await (await request.get(`/api/jobs/${job.id}/recipe/runs/${run.id}/steps/${gate!.id}/attempts/${gate!.currentAttemptId}`)).json();
  expect(detail.attempt.result.evidence.decision).toBe("rejected");
  expect(detail.attempt.executions).toEqual([]);
});
