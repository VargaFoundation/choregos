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

test("the readiness panel lists what is left, and setting a Slack channel and a test command turns two items done (S40-06c)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/p/dev/settings");
  const panel = page.getByTestId("readiness-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("readiness-summary")).toContainText("ready");
  await expect(panel.getByTestId("readiness-item-notifications")).toHaveAttribute("data-state", "todo");
  await expect(panel.getByTestId("readiness-item-repository-checks")).toHaveAttribute("data-state", "check");
  await expect(panel.getByTestId("readiness-item-connector-cd")).toHaveAttribute("data-state", "check");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-readiness-before-fix.png`, fullPage: true });

  const general = page.getByTestId("section-general");
  await general.getByLabel("Test command").fill("pytest -q");
  await general.getByRole("button", { name: "Save general settings" }).click();
  await expect(general.getByRole("status")).toHaveText("General settings saved");
  await expect(panel.getByTestId("readiness-item-repository-checks")).toHaveAttribute("data-state", "done");

  const notifications = page.getByTestId("section-notifications");
  await notifications.getByLabel("Slack channel").fill("#choregos-dev");
  await notifications.getByRole("button", { name: "Save notification settings" }).click();
  await expect(notifications.getByRole("status")).toHaveText("Notification settings saved");
  await expect(panel.getByTestId("readiness-item-notifications")).toHaveAttribute("data-state", "done");
  // What the first save wrote is still there after the second one: no section drops another's keys.
  await expect(general.getByLabel("Test command")).toHaveValue("pytest -q");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-readiness-after-fix.png`, fullPage: true });
});

test("a readiness fix link jumps to its section", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/p/dev/settings");
  await page.getByTestId("fix-notifications").click();
  await expect(page).toHaveURL(/#notifications$/);
  await expect(page.locator("#notifications")).toBeInViewport();
});

test("the tools, backend and danger zone sections work on the mock", async ({ page }) => {
  await page.goto("/p/dev/settings");
  await expect(page.getByTestId("section-agents-models").getByLabel("Default backend")).toHaveValue("claude-code");
  await page.getByTestId("section-agents-models").getByRole("button", { name: "Save backend" }).click();
  await expect(page.getByTestId("section-agents-models").getByRole("status").first()).toHaveText("Backend saved");
  const danger = page.getByTestId("section-danger-zone");
  await danger.getByRole("button", { name: "Suspend" }).click();
  await expect(danger.getByTestId("confirmation")).toContainText("Suspend dev?");
  await danger.getByRole("button", { name: "Cancel" }).click();
  await expect(danger.getByTestId("confirmation")).toHaveCount(0);
});

test("no serious accessibility violation and no horizontal scroll at 390 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/p/dev/settings");
  await expect(page.getByTestId("connecteur-cd")).toBeVisible();
  await expect(page.getByTestId("readiness-panel")).toBeVisible();
  await expect(page.getByTestId("section-general")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-settings-390.png`, fullPage: true });
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});
