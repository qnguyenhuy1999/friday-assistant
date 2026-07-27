import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  // An answer becomes visible on the conversation's 5s answer poll, so the 5s
  // default would race the very thing these specs exist to observe.
  expect: { timeout: 20_000 },
  retries: process.env.CI ? 1 : 0,
  use: {
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  reporter: process.env.CI
    ? [["blob", { outputDir: "test-results" }], ["list"]]
    : "list",
});
