import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // The full-analysis test runs the real AI pipeline. The moodboard step is
  // deliberately paced: the free render provider refuses back-to-back requests, so the
  // three renders are spaced ~12s apart to deliver 3/3 instead of 1/3. Must stay above
  // the in-test waits or this cap fires first and they never apply.
  timeout: 600_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1, // scenarios share the dev database — run serially
  use: {
    // Overridable so the same suite can run against a real deployment. The compose
    // stack publishes no host port — everything reaches it through the tunnel — so
    // testing the deployed thing means pointing at its public URL:
    //   E2E_BASE_URL=https://app.totalkapp.com npx playwright test
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel: "chrome", // reuse the system Chrome, no browser download
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  reporter: [["list"]],
});
