import { expect, test } from "@playwright/test";
import path from "node:path";

/**
 * Browser verification of the DEPLOYED stack.
 *
 *   E2E_BASE_URL=https://app.totalkapp.com \
 *   E2E_DEPLOYED_EMAIL=qa.audit@meyraki.test \
 *   E2E_DEPLOYED_PASSWORD=... \
 *     npx playwright test deployed.spec.ts
 *
 * Separate from analysis.spec.ts for one reason: that suite SIGNS UP a throwaway account
 * per test, and the deployment is invitation-only, so every registration there is refused
 * with a 403 — correctly. Loosening the allowlist to let the tests in would mean allowing
 * a whole email domain on a public site, and since registration requires no email
 * verification, anyone could then claim an address in it. A reserved TLD is no safer.
 *
 * So this logs in as an already-allowlisted account instead. It gives the one thing the
 * API smoke cannot: what the browser actually renders. A heatmap can be stored, returned
 * with a 200, and still fail to decode; a PDF link can exist and download zero bytes.
 */

const EMAIL = process.env.E2E_DEPLOYED_EMAIL;
const PASSWORD = process.env.E2E_DEPLOYED_PASSWORD;
const unique = `Deployed ${Date.now().toString(36)}`;

test.describe("deployed stack", () => {
  test.skip(
    !EMAIL || !PASSWORD,
    "set E2E_DEPLOYED_EMAIL and E2E_DEPLOYED_PASSWORD to run against a deployment",
  );

  test("sign in, analyse a real plan, see the heatmap and download the report", async ({
    page,
  }) => {
    test.setTimeout(900_000); // a real analysis over the public network

    await page.goto("/projects");
    await page.waitForURL("**/login");
    await page.getByPlaceholder("Email").fill(EMAIL!);
    await page.getByPlaceholder(/password/i).fill(PASSWORD!);
    await page.getByRole("button", { name: /sign in/i }).click();
    await page.waitForURL("**/projects");

    await page.getByPlaceholder("Project name").fill(unique);
    await page.locator("select").selectOption("hotel");
    await page.getByRole("button", { name: "Create project" }).click();
    await expect(page.getByText(`Analysis · ${unique}`)).toBeVisible();

    await page
      .locator('input[type="file"][accept=".png,.jpg,.jpeg,.pdf"]')
      .setInputFiles(path.join(__dirname, "..", "..", "api", "tests", "golden", "cleo_hotel.png"));
    await expect(page.getByText(/CLEO_HOTEL\.PNG ✓/i)).toBeVisible();

    await page.getByRole("button", { name: "Generate insights" }).click();
    await expect(page.locator("span.dim-label", { hasText: "done" }).first()).toBeVisible({
      timeout: 780_000,
    });

    // Decoded, not merely present. An <img> whose request 404s or whose bytes are corrupt
    // still exists in the DOM and still passes a visibility check.
    const heatmap = page.getByAltText(/guest-flow heatmap/i);
    await expect(heatmap).toBeVisible();
    const decoded = await heatmap.evaluate(
      (img) => (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth > 0,
    );
    expect(decoded, "the heatmap element rendered but the image never decoded").toBe(true);

    // The report the client is actually sent.
    const link = page.getByTestId("report-download");
    await expect(link).toBeVisible();
    const href = await link.getAttribute("href");
    expect(href, "the download link must point at the extensionless report route").toContain(
      "/report",
    );
    const pdf = await page.request.get(href!);
    expect(pdf.status()).toBe(200);
    expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
    // The CDN must not be allowed to keep a copy of a client's report.
    expect(pdf.headers()["cache-control"] ?? "").toContain("no-store");

    await page.screenshot({ path: "e2e/results-deployed.png", fullPage: true });
  });
});
