import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * The project settings page reads as sections with an icon per connector (S40-03b). Rendered on the
 * console's mock, project `dev` as the sandbox scenario leaves it: a tracker, a repository, a CD that is the
 * demo stand-in, the runtime provided by the deployment. `SETTINGS_SHOTS=<dir>` also writes screenshots.
 */
const SHOTS = process.env.SETTINGS_SHOTS;

test("the sections read in order, each connector has its icon", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/p/dev/settings");
  const headings = await page.getByRole("heading", { level: 2 }).allTextContents();
  expect(headings.filter((h) => ["Connections", "Agents & models", "Guardrails", "Tools"].includes(h))).toEqual([
    "Connections",
    "Agents & models",
    "Guardrails",
    "Tools",
  ]);
  await expect(page.getByTestId("connecteur-scm").locator('[data-icon="brand:github"]')).toBeVisible();
  await expect(page.getByTestId("connecteur-tracker").locator('[data-icon="brand:github"]')).toBeVisible();
  await expect(page.getByTestId("connecteur-cd").locator('[data-icon="lucide:cd/demo"]')).toBeVisible();
  await expect(page.getByTestId("connecteur-cd")).toContainText("Demo stand-in");
  await expect(page.getByTestId("connecteur-cd")).toContainText("Not tested yet");
  await expect(page.getByTestId("connecteur-runtime")).toContainText("Provided by the platform");
  await expect(page.getByTestId("connecteur-runtime")).toContainText("agent-sandbox");
  expect(await page.content()).not.toContain("Unknown");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-settings-dev.png`, fullPage: true });
});

test("the section nav jumps to a section", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/p/dev/settings");
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Tools" }).click();
  await expect(page).toHaveURL(/#tools$/);
  await expect(page.locator("#tools")).toBeInViewport();
});

test("Configure opens a drawer and Save closes it, the card then reads Connected", async ({ page }) => {
  await page.goto("/p/dev/settings");
  const cd = page.getByTestId("connecteur-cd");
  await cd.getByRole("button", { name: "Edit" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-settings-drawer.png` });
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(cd.getByTestId("etat-cd")).toHaveText("Connected");
});

test("the runtime lists only what the deployment runs", async ({ page }) => {
  await page.goto("/p/dev/settings");
  const runtime = page.getByTestId("connecteur-runtime");
  await runtime.getByRole("button", { name: "Configure" }).click();
  const options = await page.getByRole("dialog").getByLabel("Implementation").locator("option").allTextContents();
  expect(options).toEqual(["Kubernetes · agent-sandbox (warm pool)", "Kubernetes Job"]);
});

test("no serious accessibility violation and no horizontal scroll at 390 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/p/dev/settings");
  await expect(page.getByTestId("connecteur-cd")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-settings-390.png`, fullPage: true });
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});
