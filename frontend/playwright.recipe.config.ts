import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./recipe-e2e",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 120_000,
  use: { headless: true, baseURL: "http://127.0.0.1:18769" },
  projects: [{ name: "recipe-chromium", use: { browserName: "chromium", channel: "chrome" } }],
});
