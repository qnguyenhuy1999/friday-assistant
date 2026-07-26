import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  reporter: process.env.CI
    ? [["blob", { outputDir: "test-results" }], ["list"]]
    : "list",
});
