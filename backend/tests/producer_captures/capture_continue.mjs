import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import * as fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const [packageDir, harnessFile, harnessSource, archive, destination] = process.argv.slice(2).map((value) => path.resolve(value));
const packageJson = JSON.parse(await fs.readFile(path.join(packageDir, "package.json"), "utf8"));
assert.equal(packageJson.name, "@continuedev/cli");
assert.equal(packageJson.version, "1.5.47");
const cli = path.join(packageDir, packageJson.bin.cn);
const guard = fileURLToPath(new URL("./loopback_only.cjs", import.meta.url));
const { setupMockLLMTest, cleanupMockLLMServer } = await import(pathToFileURL(harnessFile).href);
const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "codeplane-continue-capture-"));
const globalDir = path.join(workspace, ".continue");
const blockedLog = path.join(workspace, "blocked-network.jsonl");
const context = { testDir: workspace, cliPath: cli };
const prompts = ["Describe addition in this synthetic CLI fixture.", "Use the synthetic example 2 plus 3."];
const replies = ["Addition combines two numbers in this inert fixture.", "The synthetic example gives 5."];
let response = 0;
const server = await setupMockLLMTest(context, {
  response: () => replies[response++],
});
const runs = [];
let completed = false;

async function run(args) {
  const child = spawn(process.execPath, ["--require", guard, cli, ...args], {
    cwd: workspace,
    env: {
      PATH: process.env.PATH,
      HOME: workspace,
      USERPROFILE: workspace,
      LANG: "C.UTF-8",
      TERM: "dumb",
      CONTINUE_CLI_TEST: "true",
      FORCE_NO_TTY: "true",
      CONTINUE_CLI_TEST_SESSION_ID: "codeplane-native-capture",
      CONTINUE_GLOBAL_DIR: globalDir,
      NATIVE_CAPTURE_NETWORK_LOG: blockedLog,
      NODE_OPTIONS: "--max-old-space-size=768",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const timer = setTimeout(() => child.kill("SIGKILL"), 45000);
  try {
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", resolve);
    });
    runs.push({ args, exit_code: code, stdout, stderr });
    assert.equal(code, 0, `Actual Continue CLI failed:\n${stdout}\n${stderr}`);
    return stdout;
  } finally {
    clearTimeout(timer);
  }
}

async function sha256(file) {
  return createHash("sha256").update(await fs.readFile(file)).digest("hex");
}

try {
  await fs.mkdir(destination, { recursive: true });
  const sample = path.join(destination, "continue-cli-1.5.47.session.json");
  await assert.rejects(fs.access(sample), { code: "ENOENT" });
  const first = await run(["-p", "--config", context.configPath, prompts[0]]);
  assert.ok(first.includes(replies[0]));
  const second = await run(["--resume", "-p", "--config", context.configPath, prompts[1]]);
  assert.ok(second.includes(replies[1]));
  assert.equal(server.requests.length, 2);
  assert.ok(server.requests.every((request) => request.method === "POST" && request.url === "/chat/completions"));
  const sessions = (await fs.readdir(path.join(globalDir, "sessions")))
    .filter((name) => name.endsWith(".json") && name !== "sessions.json");
  assert.equal(sessions.length, 1);
  const nativePath = path.join(globalDir, "sessions", sessions[0]);
  const raw = await fs.readFile(nativePath);
  const saved = JSON.parse(raw);
  assert.deepEqual(saved.history.filter((item) => item.message?.role === "user").map((item) => item.message.content), prompts);
  assert.deepEqual(saved.history.filter((item) => item.message?.role === "assistant").map((item) => item.message.content), replies);
  await fs.copyFile(nativePath, sample);
  const blocked = await fs.readFile(blockedLog, "utf8").catch((error) => {
    if (error.code === "ENOENT") return "";
    throw error;
  });
  const provenance = {
    capture_kind: "actual-cli/local-inert-sse",
    format_id: "continue_dev",
    producer: {
      distribution: packageJson.name,
      version: packageJson.version,
      artifact: path.basename(archive),
      artifact_sha256: await sha256(archive),
      artifact_sha512: createHash("sha512").update(await fs.readFile(archive)).digest("base64"),
      files_sha256: {
        "package.json": await sha256(path.join(packageDir, "package.json")),
        "dist/cn.js": await sha256(cli),
        "dist/index.js": await sha256(path.join(packageDir, "dist/index.js")),
      },
      git_head: null,
      node: process.version,
    },
    harness: {
      repository: "https://github.com/continuedev/continue",
      source_release: "v1.5.45",
      source_commit: "abb1fc0439bd335efafd2dc4f39907e25348c572",
      source_path: "extensions/cli/src/test-helpers/mock-llm-server.ts",
      source_sha256: await sha256(harnessSource),
      compiled_sha256: await sha256(harnessFile),
      functions: ["createMockLLMServer", "setupMockLLMTest"],
    },
    sample: path.basename(sample),
    sample_sha256: await sha256(sample),
    native_session_file: sessions[0],
    cli_calls: runs.map(({ args, exit_code }) => ({ args, exit_code })),
    prompts,
    replies,
    local_http_requests: server.requests.length,
    blocked_non_loopback_attempts: blocked.split("\n").filter(Boolean).map(JSON.parse),
    model_inference: false,
    limitations: [
      "The installed npm artifact is not attributed to the separate harness source commit",
      "The local SSE fixture supplied no usage counters; native CLI zero initializers are not billing evidence",
    ],
  };
  await fs.writeFile(path.join(destination, "continue-cli-1.5.47.provenance.json"), JSON.stringify(provenance, null, 2) + "\n");
  await fs.writeFile(path.join(destination, "continue-cli-runs.json"), JSON.stringify(runs, null, 2) + "\n");
  completed = true;
  console.log(JSON.stringify({ sample, sha256: provenance.sample_sha256, requests: server.requests.length, version: packageJson.version }));
} finally {
  await cleanupMockLLMServer(server);
  if (completed) {
    await fs.rm(workspace, { recursive: true });
  } else {
    await fs.mkdir(destination, { recursive: true });
    await fs.writeFile(path.join(destination, "continue-cli-runs.json"), JSON.stringify(runs, null, 2) + "\n");
    console.error(`Capture failed; native workspace retained at ${workspace}`);
  }
}
