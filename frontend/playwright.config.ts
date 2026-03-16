import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:8080",
    headless: true,
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
  webServer: {
    command: "cd .. && source .venv/bin/activate && cpl up",
    url: "http://127.0.0.1:8080/api/health",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
