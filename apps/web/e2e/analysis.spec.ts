import { expect, test } from "@playwright/test";
import path from "path";

/** Live E2E over the real stack: web (3000) + API (8000) + Postgres + Claude agents.
 *  Start both servers before running: see README. Costs a few cents per run. */

const fixture = (name: string) => path.join(__dirname, "fixtures", name);
const unique = `E2E ${Date.now()}`;

/** Register a fresh studio through the real login UI; lands on /projects. */
async function signUp(page: import("@playwright/test").Page, tag: string) {
  await page.goto("/projects");
  await page.waitForURL("**/login"); // unauthenticated -> redirected
  await page.getByRole("button", { name: /create your studio/i }).click();
  await page.getByPlaceholder(/studio \/ organization name/i).fill(`Studio ${tag}`);
  await page.getByPlaceholder("Email").fill(`e2e-${tag.toLowerCase().replace(/\W+/g, "-")}-${Date.now()}@test.dev`);
  await page.getByPlaceholder(/password/i).fill("e2e-password-1");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL("**/projects");
}

test("invalid floorplan is rejected with a human message", async ({ page }) => {
  await signUp(page, "reject");
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

test("valid image that is not a floorplan: Intake Agent rejects with its reason", async ({ page }) => {
  await signUp(page, "intake");
  await page.getByPlaceholder("Project name").fill(`${unique} intake-reject`);
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByText(`Analysis · ${unique} intake-reject`)).toBeVisible();

  // A structurally valid PNG (passes magic-byte check) that is a bar chart
  await page
    .locator('input[type="file"][accept=".png,.jpg,.jpeg,.pdf"]')
    .setInputFiles(fixture("not_a_floorplan.png"));
  await expect(page.getByText(/NOT_A_FLOORPLAN\.PNG ✓/i)).toBeVisible();

  await page.getByRole("button", { name: "Generate insights" }).click();

  // The real Intake Agent (Haiku vision) must reject it with a human sentence
  await expect(page.locator("span.dim-label", { hasText: "rejected" }).first()).toBeVisible({
    timeout: 120_000,
  });
  const reason = page.getByTestId("analysis-error");
  await expect(reason).toBeVisible();
  expect(((await reason.textContent()) ?? "").length).toBeGreaterThan(10);
  // Nothing past intake ran — no paid zones/layout calls
  await expect(page.getByText("zones: started")).toHaveCount(0);
});

test("full analysis: upload → agents → heatmap, scenarios, moodboard, score", async ({ page }) => {
  await signUp(page, "full");
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
    timeout: 420_000,
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

  // Interior renders are an enhancement: either present, or their absence is
  // explicitly surfaced in the pipeline register — never silent.
  const renders = page.getByTestId("moodboard-render");
  const renderCount = await renders.count();
  if (renderCount === 0) {
    await expect(page.getByText(/renders generated .* — continuing/)).toBeVisible();
  } else {
    // Every rendered tile must be a real decoded image, not a broken placeholder.
    for (let i = 0; i < renderCount; i++) {
      const ok = await renders.nth(i).evaluate(
        (img) => (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth > 0
      );
      expect(ok).toBe(true);
    }
    // The register names which provider served them (gemini or the free fallback).
    await expect(page.getByText(/renders generated via \w+/)).toBeVisible();
  }

  // All 9 steps done
  await expect(page.getByText("qa · done")).toBeVisible();

  // Branded PDF report is downloadable and a real PDF
  const download = page.getByTestId("report-download");
  await expect(download).toBeVisible();
  const pdfUrl = await download.getAttribute("href");
  const pdf = await page.request.get(pdfUrl!);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  const body = await pdf.body();
  expect(body.subarray(0, 5).toString()).toBe("%PDF-");
  expect(body.length).toBeGreaterThan(50_000); // embedded heatmap => substantial file

  // Every scenario carries a real constraint-solver verdict (OR-Tools CP-SAT)
  const verdicts = page.getByTestId("solver-verdict");
  expect(await verdicts.count()).toBeGreaterThanOrEqual(2);
  await expect(verdicts.first()).toHaveText(/constraint solver/);

  await page.screenshot({ path: "e2e/results-full-analysis.png", fullPage: true });

  // A refresh must not lose the analysis (QA hunt bug: there was no history at all)
  await page.reload();
  await page.getByRole("button", { name: new RegExp(unique) }).first().click();
  const history = page.getByTestId("analysis-history");
  await expect(history).toBeVisible();
  await history.getByRole("button").first().click();
  await expect(page.getByTestId("flow-score")).toBeVisible();
  await expect(page.getByAltText(/guest-flow heatmap/i)).toBeVisible();
  await expect(page.getByTestId("report-download")).toBeVisible();
});

test("PDF floorplan: full analysis with a rendered heatmap", async ({ page }) => {
  test.skip(!process.env.E2E_PDF, "PDF full run is opt-in (E2E_PDF=1) — extra model spend");
  await signUp(page, "pdf");
  await page.getByPlaceholder("Project name").fill(`${unique} PDF`);
  await page.locator("select").selectOption("cafe");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByText(`Analysis · ${unique} PDF`)).toBeVisible();

  await page
    .locator('input[type="file"][accept=".png,.jpg,.jpeg,.pdf"]')
    .setInputFiles(fixture("plan.pdf"));
  await expect(page.getByText(/PLAN\.PDF ✓/i)).toBeVisible();

  await page.getByRole("button", { name: "Generate insights" }).click();
  await expect(page.locator("span.dim-label", { hasText: "done" }).first()).toBeVisible({
    timeout: 420_000,
  });

  // The heatmap is the point: PDFs used to produce none (pypdfium2 now rasterizes)
  const heatmap = page.getByAltText(/guest-flow heatmap/i);
  await expect(heatmap).toBeVisible();
  const decoded = await heatmap.evaluate(
    (img) => (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth > 0
  );
  expect(decoded).toBe(true);
  await expect(page.getByTestId("report-download")).toBeVisible();
  await page.screenshot({ path: "e2e/results-pdf-analysis.png", fullPage: true });
});

test("Arabic report: full analysis with report_language=ar", async ({ page }) => {
  test.skip(!process.env.E2E_AR, "Arabic full run is opt-in (E2E_AR=1) — extra model spend");
  await signUp(page, "ar");
  await page.getByPlaceholder("Project name").fill(`${unique} AR`);
  await page.getByPlaceholder("Client (optional)").fill("فندق كليو");
  await page.locator("select").selectOption("hotel");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByText(`Analysis · ${unique} AR`)).toBeVisible();

  await page
    .locator('input[type="file"][accept=".png,.jpg,.jpeg,.pdf"]')
    .setInputFiles(fixture("cleo_plan.png"));
  await expect(page.getByText(/CLEO_PLAN\.PNG ✓/i)).toBeVisible();

  // Language toggle: Arabic selected, styling reflects it
  const arChip = page.getByTestId("report-lang-ar");
  await arChip.click();
  await expect(arChip).toHaveClass(/border-viridian/);

  await page.getByRole("button", { name: "Generate insights" }).click();
  await expect(page.locator("span.dim-label", { hasText: "done" }).first()).toBeVisible({
    timeout: 420_000,
  });

  const download = page.getByTestId("report-download");
  await expect(download).toBeVisible();
  const pdf = await page.request.get((await download.getAttribute("href"))!);
  expect(pdf.status()).toBe(200);
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
});
