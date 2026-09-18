import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  timeout: 120000,
  expect: { timeout: 20000 },
  workers: 1,
  use: {
    baseURL: process.env.RELAY_E2E_URL || "http://127.0.0.1:5174",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHANNEL
      ? { channel: process.env.PLAYWRIGHT_CHANNEL }
      : undefined,
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1000 },
      },
    },
  ],
  webServer: process.env.RELAY_E2E_URL ? undefined : [
    {
      command: "node ../scripts/e2e-server.mjs api",
      url: "http://127.0.0.1:8001/health",
      timeout: 120000,
      reuseExistingServer: false,
    },
    {
      command: "node ../scripts/e2e-server.mjs web",
      url: "http://127.0.0.1:5174",
      timeout: 120000,
      reuseExistingServer: false,
    },
  ],
});
