import { expect, test } from "@playwright/test";
import path from "path";

/** Live E2E over the real stack: web (3000) + API (8000) + Postgres + Claude agents.
 *  Start both servers before running: see README. Costs a few cents per run. */

const fixture = (name: string) => path.join(__dirname, "fixtures", name);
const unique = `E2E ${Date.now()}`;

test("invalid floorplan is rejected with a human message", async ({ page }) => {
  await page.goto("/projects");
  await page.getByPlaceholder("Project name").fill(`${unique} reject`);
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByText(`Analysis · ${unique} reject`)).toBeVisible();

  await page
    .locator('input[type="file"][accept=".png,.jpg,.jpeg,.pdf"]')
    .setInputFiles(fixture("fake_plan.png"));
  await expect(page.getByText(/not a valid PNG, JPG, or PDF/i)).toBeVisible();
  // Generate stays disabled — no analysis can start from a rejected upload
  await expect(page.getByRole("button", { name: "Generate insights" })).toBeDisabled();
});

test("full analysis: upload → agents → heatmap, scenarios, moodboard, score", async ({ page }) => {
  await page.goto("/projects");
  await page.getByPlaceholder("Project name").fill(unique);
  await page.getByPlaceholder("Client (optional)").fill("Cleo Urban Stay");
  await page.locator("select").selectOption("hotel");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByText(`Analysis · ${unique}`)).toBeVisible();

  // Uploads (tags confirm server acceptance, not just the picker)
  await page
    .locator('input[type="file"][accept=".png,.jpg,.jpeg,.pdf"]')
    .setInputFiles(fixture("cleo_plan.png"));
  await expect(page.getByText(/CLEO_PLAN\.PNG ✓/i)).toBeVisible();
  await page.locator('input[type="file"][accept=".csv"]').setInputFiles(fixture("footfall.csv"));
  await expect(page.getByText(/FOOTFALL\.CSV ✓/i)).toBeVisible();

  // Objectives: guest flow is pre-selected; add revenue
  await page.getByRole("button", { name: "Revenue per sqm" }).click();

  const generate = page.getByRole("button", { name: "Generate insights" });
  await expect(generate).toBeEnabled();
  await generate.click();
  await expect(page.getByRole("button", { name: "Analyzing…" })).toBeDisabled();

  // Live register streams step events
  await expect(page.getByText("Pipeline register")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("zones: started")).toBeVisible({ timeout: 60_000 });

  // Real pipeline (Haiku + Sonnet + Opus) — allow up to 4 minutes
  await expect(page.getByText("Analysis · " + unique)).toBeVisible();
  await expect(page.locator("span.dim-label", { hasText: "done" }).first()).toBeVisible({
    timeout: 240_000,
  });

  // Every user-visible deliverable is present
  await expect(page.getByAltText(/guest-flow heatmap/i)).toBeVisible();
  await expect(page.getByTestId("flow-score")).toBeVisible();
  const score = await page.getByTestId("flow-score").locator("p").first().textContent();
  expect(Number(score)).toBeGreaterThan(0);
  expect(Number(score)).toBeLessThanOrEqual(100);

  const scenarios = page.getByTestId("scenarios");
  await expect(scenarios).toBeVisible();
  expect(await scenarios.locator("h3").count()).toBeGreaterThanOrEqual(2);
  await expect(scenarios.getByText(/confidence \d+%/i).first()).toBeVisible();

  const moodboard = page.getByTestId("moodboard");
  await expect(moodboard).toBeVisible();
  expect(await moodboard.locator("p", { hasText: /^#/ }).count()).toBeGreaterThanOrEqual(3);

  // All 9 steps done
  await expect(page.getByText("qa · done")).toBeVisible();
  await page.screenshot({ path: "e2e/results-full-analysis.png", fullPage: true });
});
