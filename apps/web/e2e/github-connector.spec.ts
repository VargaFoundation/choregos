import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * A GitHub connector is configured by picking a repository, never by typing an installation id
 * (S40-04c, ADR 0073). Rendered on the console's mock, project `dev`. `GITHUB_SHOTS=<dir>` writes
 * screenshots.
 */
const SHOTS = process.env.GITHUB_SHOTS;

test("configure the scm GitHub connector without ever seeing an installation id", async ({ page }) => {
  await page.goto("/p/dev/settings");
  const scm = page.getByTestId("connecteur-scm");
  await expect(scm.getByTestId("github-via")).toHaveText("via the Choregos App on DiametralGroup");
  await scm.getByRole("button", { name: "Edit" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByTestId("github-repo-field")).toBeVisible();
  expect(await dialog.textContent()).not.toMatch(/installation id/i);
  await dialog.getByPlaceholder("Search repositories").fill("billing");
  await expect(dialog.getByRole("button", { name: /acme\/checkout-web/ })).toHaveCount(0);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-github-picker.png` });
  await dialog.getByRole("button", { name: /acme\/billing-api/ }).click();
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(scm.getByTestId("github-via")).toHaveText("via the Choregos App on acme");
  await expect(scm).toContainText("acme/billing-api");
  expect(await page.content()).not.toMatch(/installation id/i);
});

test("a repository of an account without the App is refused, with the way to install it", async ({ page }) => {
  await page.route("https://github.com/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<title>GitHub</title>" }));
  await page.goto("/p/dev/settings");
  await page.getByTestId("connecteur-scm").getByRole("button", { name: "Edit" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Type it instead" }).click();
  await dialog.getByPlaceholder("owner/repo").fill("othercorp/app");
  await expect(dialog.getByText(/not installed on othercorp/)).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-github-not-installed.png` });
  await dialog.getByRole("button", { name: "Save" }).click();
  const section = page.getByTestId("section-connections");
  await expect(section.getByRole("alert")).toContainText("GitHub App is not installed on `othercorp`");
  await section.getByRole("button", { name: "Install on GitHub" }).click();
  await expect(page).toHaveURL(/github\.com\/apps\/choregos-bot\/installations\/new\?state=/);
});

test("the picker has no serious accessibility violation", async ({ page }) => {
  await page.goto("/p/dev/settings");
  await page.getByTestId("connecteur-scm").getByRole("button", { name: "Edit" }).click();
  await expect(page.getByTestId("github-repo-field")).toBeVisible();
  const result = await new AxeBuilder({ page }).analyze();
  expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
});
