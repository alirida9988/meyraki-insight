import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 300_000, // the full-analysis test runs the real AI pipeline
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1, // scenarios share the dev database — run serially
  use: {
    baseURL: "http://localhost:3000",
    channel: "chrome", // reuse the system Chrome, no browser download
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  reporter: [["list"]],
});
