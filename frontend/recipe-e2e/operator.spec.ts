import { test, expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";
import type { Job, JobPendingDecisionPage, RecipeAttemptControl, RecipeAttemptInspection, RecipeCurrentRun, RecipeStepInspection, RecipeStepList } from "../src/api/types";

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
  await writeFile(path.join(directory, "git-events.jsonl"), "");
  await writeFile(path.join(home, "config.yaml"), "server:\n  host: 127.0.0.1\n  port: 18769\nruntime:\n  max_concurrent_jobs: 1\n");
  await startServer();
});

async function startServer() {
  server = spawn("uv", ["run", "--no-sync", "python", "-m", "backend.tests.recipe_browser_server"], {
    cwd: path.resolve(".."),
    env: {
      ...process.env, CODEPLANE_HOME: home, RECIPE_BROWSER_PORT: "18769", CODEPLANE_E2E_FAKE_AGENT: "1",
      GIT_TRACE2_EVENT: path.join(directory, "git-events.jsonl"),
    },
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
  const serverLog = test.info().outputPath("fixture-server.log");
  await writeFile(serverLog, output);
  await test.info().attach("fixture-server.log", { path: serverLog, contentType: "text/plain" });
  if (directory) {
    const gitLog = test.info().outputPath("git-events.jsonl");
    await writeFile(gitLog, await readFile(path.join(directory, "git-events.jsonl")));
    await test.info().attach("git-events.jsonl", { path: gitLog, contentType: "application/jsonl" });
  }
  if (directory) await rm(directory, { recursive: true, force: true });
});

async function finishFixtureCommands(
  page: Page,
  request: APIRequestContext,
  jobId: string,
  commands: Record<string, {
    argv: string[] | ((attempt: RecipeAttemptInspection["attempt"]) => string[]);
    workspaceKey?: string;
    maxAttempts?: number;
    hardGate?: boolean;
    permissionReason?: "hard_gate" | "incomplete_evidence";
  }>,
) {
  const { run }: RecipeCurrentRun = await (await request.get(`/api/jobs/${jobId}/recipe/current`)).json();
  expect(run?.jobId).toBe(jobId);
  const root = `/api/jobs/${jobId}/recipe/runs/${run!.id}`;
  const approved = new Set<string>();
  const hardGates = new Map<string, string[]>();
  const mergeDispatches = async () => (await readFile(path.join(directory, "git-events.jsonl"), "utf8"))
    .trim().split("\n").filter(Boolean).map((line) => JSON.parse(line))
    .filter((event) => event.event === "start" && event.argv?.[1] === "merge");
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    const pending: JobPendingDecisionPage = await (await request.get(`/api/jobs/${jobId}/recipe/decisions`)).json();
    const steps: RecipeStepList = await (await request.get(`${root}/steps`)).json();
    expect(pending.nextCursor).toBeNull();
    for (const item of pending.items) {
      if (item.humanGate) continue;
      const action = JSON.parse(item.approval.proposedAction!);
      expect(action.kind).toBe("policy_action");
      expect(action.execution.binding_id).toBe(`codeplane.command:${run!.id}`);
      const owners: RecipeAttemptControl[] = [];
      for (const candidate of steps.items) {
        if (!candidate.currentAttemptId || candidate.definition.runner.kind !== "command") continue;
        const control: RecipeAttemptControl = await (await request.get(
          `${root}/steps/${candidate.id}/attempts/${candidate.currentAttemptId}/control`,
        )).json();
        if (control.stop?.enabled && control.stop.expectedToken === action.execution.execution_token) owners.push(control);
      }
      expect(owners, `Permission must match one actual command token: ${JSON.stringify(item)}`).toHaveLength(1);
      const control = owners[0];
      const step = steps.items.find((candidate) => candidate.id === control.stepInstanceId)!;
      const expected = commands[step!.stepPath];
      expect(expected, `Unexpected permission for ${step!.stepPath}`).toBeDefined();
      expect(item.approval.jobId).toBe(jobId);
      // Policy events are intentionally Job-wide in the pending-decision projection.
      // Match their token against the independently read owned attempt, not argument claims.
      expect([item.recipeRunId, item.stepInstanceId, item.attemptId]).toEqual(
        item.attemptId ? [run!.id, step.id, step.currentAttemptId] : [null, null, null],
      );
      expect(control.attemptId).toBe(step.currentAttemptId);
      expect(approved.has(item.approval.id)).toBe(false);
      const target = `${root}/steps/${step.id}/attempts/${control.attemptId}`;
      const { attempt }: RecipeAttemptInspection = await (await request.get(target)).json();
      expect(attempt.id).toBe(control.attemptId);
      expect(attempt.stepInstanceId).toBe(step!.id);
      expect(attempt.number).toBeLessThanOrEqual(expected.maxAttempts ?? 1);
      expect(attempt.result).toBeNull();
      expect(attempt.outputs).toEqual([]);
      const argv = typeof expected.argv === "function" ? expected.argv(attempt) : expected.argv;
      expect(attempt.runner).toMatchObject({ kind: "command", argv });
      const job: Job = await (await request.get(`/api/jobs/${jobId}`)).json();
      expect(attempt.workspace.policy).toMatchObject({
        mode: expected.workspaceKey ? "isolated" : "mutable", key: expected.workspaceKey ?? "job",
      });
      if (expected.workspaceKey) {
        expect(path.dirname(attempt.workspace.path)).toBe(path.dirname(job.worktreePath!));
        const registered = execFileSync("git", ["-C", repository, "worktree", "list", "--porcelain"], { encoding: "utf8" });
        const entry = registered.split("\n\n").find((value) => value.startsWith(`worktree ${attempt.workspace.path}\n`));
        expect(entry).toContain(`branch refs/heads/codeplane/recipe/recipe-${step!.id}-v`);
        expect(attempt.workspace.path).not.toBe(job.worktreePath);
      } else {
        expect(attempt.workspace.path).toBe(job.worktreePath);
      }
      expect(control).toMatchObject({
        jobId, runId: run!.id, stepInstanceId: step!.id, attemptId: attempt.id,
        stop: { enabled: true },
      });
      expect(action.execution).toEqual({
        job_id: jobId, binding_id: `codeplane.command:${run!.id}`,
        epoch: control.executionEpoch, execution_token: control.stop?.expectedToken,
      });
      expect(action.execution.execution_token).toBeTruthy();
      expect(action.reason_code).toBe(expected.permissionReason ?? (expected.hardGate ? "hard_gate" : "incomplete_evidence"));
      expect(item.approval.description).toBe(`Policy approval required: ${action.reason_code}`);
      expect(JSON.parse(action.fields.arguments)).toEqual({ argv });
      expect(action.fields.command).toBe(argv[0] === "sh" ? argv[2] : "");
      const permissions = page.getByRole("region", { name: "Pending Job decisions" });
      await expect(permissions).toBeVisible();
      await permissions.getByRole("button", { name: "Refresh decisions" }).click();
      const card = permissions.locator("div.rounded-lg").filter({
        has: page.locator("pre", { hasText: item.approval.proposedAction! }),
      });
      await expect(card).toHaveCount(1);
      await expect(card.getByText(item.approval.description, { exact: true })).toBeVisible();
      if (expected.hardGate) expect(await mergeDispatches()).toEqual([]);
      // The exact owned command must still be unfinished when the operator decides.
      expect((await (await request.get(target)).json()).attempt.result).toBeNull();
      const [resolution] = await Promise.all([
        page.waitForResponse((response) => response.url().endsWith(`/api/approvals/${item.approval.id}/resolve`) &&
          response.request().method() === "POST"),
        card.getByRole("button", { name: "Approve", exact: true }).click(),
      ]);
      expect(resolution.ok(), await resolution.text()).toBeTruthy();
      approved.add(item.approval.id);
      if (expected.hardGate) hardGates.set(step!.stepPath, argv);
    }
    let finished = true;
    for (const [stepPath, expected] of Object.entries(commands)) {
      const step = steps.items.find((candidate) => candidate.stepPath === stepPath);
      if (!step || !["succeeded", "failed"].includes(step.state)) {
        finished = false;
        break;
      }
      if (step.state === "failed" && (expected.maxAttempts ?? 1) > 1) {
        const inspection: RecipeStepInspection = await (await request.get(`${root}/steps/${step.id}`)).json();
        const attempt = inspection.attempts.find((candidate) => candidate.id === step.currentAttemptId);
        if (!attempt || attempt.number !== expected.maxAttempts || attempt.outcome !== "failed") {
          finished = false;
          break;
        }
      }
    }
    if (finished) {
      for (const [stepPath, expected] of Object.entries(commands)) {
        if (expected.hardGate) {
          expect(hardGates.has(stepPath), `Missing mandatory gate: ${stepPath}`).toBe(true);
          expect((await mergeDispatches()).map((event) => event.argv)).toEqual([hardGates.get(stepPath)]);
        }
      }
      return;
    }
    await page.waitForTimeout(100);
  }
  throw new Error(`Fixture commands did not finish: ${JSON.stringify(commands)}\nServer:${output.slice(-12000)}`);
}

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
  await finishFixtureCommands(page, request, job.id, {
    check: { argv: ["sh", "-c", "printf '\\nFixture review change\\n' >> README.md; git diff --check"] },
  });
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
  await finishFixtureCommands(page, request, job.id, {
    "block@1/verify": { argv: ["git", "status", "--porcelain"] },
    left: { argv: ["git", "status", "--porcelain"], workspaceKey: "left" },
    right: { argv: ["git", "status", "--porcelain"], workspaceKey: "right" },
    join: {
      hardGate: true,
      argv: (attempt) => {
        const commits = ["left", "right"].map((name) => {
          const input = attempt.inputs[name];
          expect(input.producerOutcome).toBe("succeeded");
          expect(input.producingAttemptId).toBeTruthy();
          expect(input.value).toHaveProperty("commit");
          if (!input.value || typeof input.value !== "object" || Array.isArray(input.value) ||
            !("commit" in input.value) || typeof input.value.commit !== "string") throw new Error("Missing exact predecessor code commit");
          return input.value.commit;
        });
        return ["git", "merge", "--no-edit", ...commits];
      },
    },
  });
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

async function tabTo(page: Page, target: Locator, backwards = false) {
  for (let count = 0; count < 30; count++) {
    if (await target.evaluate((element) => element === document.activeElement)) return;
    await page.keyboard.press(backwards ? "Shift+Tab" : "Tab");
  }
  await expect(target).toBeFocused();
}

async function expectVisibleKeyboardFocus(target: Locator) {
  await expect(target).toBeFocused();
  expect(await target.evaluate((element) => {
    const style = getComputedStyle(element);
    return element.matches(":focus-visible") &&
      ((style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) || style.boxShadow !== "none");
  })).toBe(true);
}

for (const reducedMotion of ["no-preference", "reduce"] as const) {
  test(`repair exhaustion retains distinct attempt evidence and keyboard focus (${reducedMotion})`, async ({ page, request }) => {
    await page.emulateMedia({ reducedMotion });
    await page.setViewportSize({ width: 1280, height: 900 });
    const projects = await (await request.get("/api/settings/projects")).json();
    const project = projects.items[0] ?? await (await request.post("/api/settings/projects", {
      data: { name: "Repair exhaustion qualification", repoPaths: [repository] },
    })).json();
    const title = `Bounded repair ${reducedMotion}`;
    const command = [
      "attempt=0",
      "if test -f repair-count.txt; then read -r attempt < repair-count.txt; fi",
      "attempt=$((attempt + 1))",
      "printf '%s\\n' \"$attempt\" > repair-count.txt",
      "printf 'Repair attempt %s\\n' \"$attempt\"",
      "printf 'Check failed after repair %s\\n' \"$attempt\" >&2",
      "exit \"$((10 + attempt))\"",
    ].join("; ");
    const draftResponse = await request.post("/api/recipes", { data: {
      name: title, entrySteps: ["repair"], steps: [
        {
          id: "repair", title: "Bounded repair check",
          instructions: "Record one local repair attempt, then run its failing check. The published bound is total attempts.",
          runner: { kind: "command", argv: ["sh", "-c", command], timeoutSeconds: 30 },
          capabilities: ["read", "write", "shell", "external_access"], workspace: { mode: "mutable" },
          inputs: { request: { type: "string", binding: { source: "literal", value: "Inspect each failed repair independently." } } },
          outputs: { result: { type: "object" }, code: { type: "code_ref" } },
          retry: { maxAttempts: 3, retryOn: ["failed"], exhaustion: { nextSteps: ["decide"] } },
          routes: { succeeded: { nextSteps: ["passed"] }, failed: { terminal: "failed" } },
        },
        {
          id: "passed", title: "Passing-only successor",
          runner: { kind: "human", proposedAction: "This action requires a passing check." },
          capabilities: [], workspace: { mode: "none" }, dependsOn: [{ stepId: "repair", outcomes: ["succeeded"] }],
          routes: { approved: { terminal: "succeeded" }, rejected: { terminal: "failed" } },
        },
        {
          id: "decide", title: "Inspect exhausted repairs",
          runner: { kind: "human", proposedAction: "Inspect the final failed diagnostic. No passing result or new attempt is granted." },
          capabilities: [], workspace: { mode: "none" }, dependsOn: [{ stepId: "repair", outcomes: ["failed"] }],
          inputs: { diagnostic: {
            type: "object", optional: true, degradedReason: "Failed diagnostic only, never a passing check.",
            binding: { source: "output", stepId: "repair", output: "result" },
          } },
          routes: { approved: { terminal: "canceled" }, rejected: { terminal: "failed" } },
        },
      ],
    } });
    expect(draftResponse.ok(), await draftResponse.text()).toBeTruthy();
    const draft = await draftResponse.json();
    const published = await request.post(`/api/recipes/${draft.id}/publish`, { data: { expectedVersion: 1 } });
    expect(published.ok(), await published.text()).toBeTruthy();
    const revision = await published.json();
    const created = await request.post("/api/jobs", { data: {
      projectId: project.id, repo: repository, prompt: "Exhaust the published repair bound without hidden retry.",
      title, branch: `repair-${reducedMotion}`, worktreeName: `repair-${reducedMotion}`,
      preset: "autonomous", recipe: { revisionId: revision.id },
    } });
    expect(created.ok(), await created.text()).toBeTruthy();
    const job = await created.json();
    const { run }: RecipeCurrentRun = await (await request.get(`/api/jobs/${job.id}/recipe/current`)).json();
    expect(run).not.toBeNull();
    const root = `/api/jobs/${job.id}/recipe/runs/${run!.id}`;
    await page.goto(`/jobs/${job.id}`);
    await finishFixtureCommands(page, request, job.id, {
      repair: { argv: ["sh", "-c", command], maxAttempts: 3, permissionReason: "hard_gate" },
    });
    let steps: RecipeStepList;
    await expect.poll(async () => {
      steps = await (await request.get(`${root}/steps`)).json();
      return steps.items.find((step) => step.stepPath === "decide")?.state;
    }, { timeout: 20_000 }).toBe("needs_decision");
    const repair = steps!.items.find((step) => step.stepPath === "repair")!;
    const gate = steps!.items.find((step) => step.stepPath === "decide")!;
    const passed = steps!.items.find((step) => step.stepPath === "passed")!;
    expect(passed.state).toBe("skipped");
    expect(passed.currentAttemptId).toBeNull();
    const inspection: RecipeStepInspection = await (await request.get(`${root}/steps/${repair.id}`)).json();
    expect(inspection.attempts.map((attempt) => [attempt.number, attempt.outcome])).toEqual([[1, "failed"], [2, "failed"], [3, "failed"]]);
    expect(new Set(inspection.attempts.map((attempt) => attempt.id)).size).toBe(3);
    const details: RecipeAttemptInspection[] = [];
    for (const summary of inspection.attempts) {
      details.push(await (await request.get(`${root}/steps/${repair.id}/attempts/${summary.id}`)).json());
    }
    const receiptIds = details.flatMap((detail) => detail.attempt.outputs.map((output) => output.id));
    expect(new Set(receiptIds).size).toBe(6);
    expect(new Set(details.map((detail) => detail.attempt.outputs.find((output) => output.name === "code")!.checksum)).size).toBe(3);
    const consumed: RecipeAttemptInspection = await (await request.get(`${root}/steps/${gate.id}/attempts/${gate.currentAttemptId}`)).json();
    expect(consumed.attempt.inputs.diagnostic.producingAttemptId).toBe(repair.currentAttemptId);
    expect(consumed.attempt.inputs.diagnostic.producerOutcome).toBe("failed");
    expect(consumed.attempt.inputs.diagnostic.degradedReason).toBe("Failed diagnostic only, never a passing check.");

    await page.goto(`/jobs/${job.id}?run=${run!.id}&step=${repair.id}&attempt=${repair.currentAttemptId}`);
    const inspector = page.getByRole("region", { name: "Recipe step inspector" });
    const railButton = page.getByRole("complementary", { name: "Recipe steps" }).getByRole("button", { name: /^Bounded repair check/ });
    await expect(inspector.getByRole("heading", { name: "Bounded repair check" })).toBeVisible();
    await expect(inspector.getByText("3 total", { exact: true })).toBeVisible();
    await tabTo(page, railButton);
    await expectVisibleKeyboardFocus(railButton);
    await page.keyboard.press("Enter");
    await expect(railButton).toBeFocused();
    const selector = inspector.getByRole("combobox", { name: "Selected attempt" });
    await tabTo(page, selector);
    await expectVisibleKeyboardFocus(selector);
    await expect(selector.locator("option")).toHaveCount(3);
    const outputButton = inspector.getByRole("navigation", { name: "Step inspector views" }).getByRole("button", { name: "Output", exact: true });
    await tabTo(page, outputButton);
    await expectVisibleKeyboardFocus(outputButton);
    await page.keyboard.press("Enter");
    await expect(outputButton).toBeFocused();
    for (const index of [2, 1, 0, 2]) {
      await tabTo(page, selector, true);
      await page.keyboard.press("Home");
      for (let advance = 0; advance < index; advance++) await page.keyboard.press("ArrowDown");
      await expect(selector).toHaveValue(details[index].attempt.id);
      await expect(selector).toBeFocused();
      await expect(inspector.getByText(`Repair attempt ${index + 1}`, { exact: true })).toBeVisible();
      await expect(inspector.getByText(`Check failed after repair ${index + 1}`, { exact: true })).toBeVisible();
      await expect(inspector.getByText(String(11 + index), { exact: true })).toBeVisible();
      await expect(selector).toBeFocused();
      if (index < 2) await expect(inspector.getByText(/Historical attempt/)).toBeVisible();
    }
    const refresh = inspector.getByRole("button", { name: "Refresh read" });
    await page.keyboard.press("Shift+Tab");
    await expect(refresh).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(refresh).toBeEnabled();
    await expect(refresh).toBeFocused();
    await expect(inspector.getByText("Repair attempt 3", { exact: true })).toBeVisible();
    for (const detail of details) {
      const retained: RecipeAttemptInspection = await (await request.get(`${root}/steps/${repair.id}/attempts/${detail.attempt.id}`)).json();
      expect(retained.attempt).toEqual(detail.attempt);
    }
    const actualJob: Job = await (await request.get(`/api/jobs/${job.id}`)).json();
    expect(await readFile(path.join(actualJob.worktreePath!, "repair-count.txt"), "utf8")).toBe("3\n");
    expect((await (await request.get(`${root}/steps/${repair.id}`)).json()).attempts).toHaveLength(3);

    await page.setViewportSize({ width: 390, height: 844 });
    const surface = inspector.getByRole("heading", { name: "Bounded repair check" });
    await surface.dispatchEvent("touchstart", { touches: [{ identifier: 1, clientX: 320, clientY: 400 }] });
    await surface.dispatchEvent("touchend", { changedTouches: [{ identifier: 1, clientX: 80, clientY: 400 }] });
    const animation = await page.evaluate(async () => {
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      const panel = document.querySelector(".animate-slide-right");
      return panel ? getComputedStyle(panel).animationName : "none";
    });
    expect(animation).toBe(reducedMotion === "reduce" ? "none" : "slide-in-from-right");
    await expect(page.getByRole("complementary", { name: "Evidence for this decision" })).toBeVisible();
    await page.getByRole("navigation").getByRole("button", { name: "Run", exact: true }).click();
    await expect(inspector).toBeVisible();
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(reducedMotion === "reduce");
    await expect(selector).toHaveValue(repair.currentAttemptId!);
    await tabTo(page, selector);
    const decision = await request.post(`${root}/steps/${gate.id}/attempts/${gate.currentAttemptId}/decision`, { data: {
      expectedVersion: gate.version, approvalId: consumed.gate!.binding.approvalId,
      inputChecksum: consumed.gate!.binding.inputChecksum, decision: "rejected", outputs: {},
    } });
    expect(decision.ok(), await decision.text()).toBeTruthy();
    expect((await decision.json()).status).toBe("decided");
    await expect.poll(async () => (await (await request.get(`/api/jobs/${job.id}`)).json()).state).toBe("failed");
    await expect(page.getByRole("region", { name: "Explicit recipe continuation" })
      .getByRole("button", { name: "Resume Job with new run" })).toBeVisible();
    await expect(selector).toBeFocused();
    await expect(selector).toHaveValue(repair.currentAttemptId!);
    expect(await readFile(path.join(actualJob.worktreePath!, "repair-count.txt"), "utf8")).toBe("3\n");
    expect((await (await request.get(`${root}/steps/${repair.id}`)).json()).attempts).toEqual(inspection.attempts);
  });
}
