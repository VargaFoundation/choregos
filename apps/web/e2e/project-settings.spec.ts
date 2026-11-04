import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * The project settings read as groups of sections, each a page (S55-07), with an icon per connector
 * (S40-03b). The mock keeps what is saved for the time of the page: a test moves between groups by
 * clicking, never by reloading. Rendered on the
 * console's mock, project `dev` as the sandbox scenario leaves it: a tracker, a repository, a CD that is the
 * demo stand-in, the runtime provided by the deployment. `SETTINGS_SHOTS=<dir>` also writes screenshots.
 */
const SHOTS = process.env.SETTINGS_SHOTS;

test("the connections group lists each connector with its icon", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/p/dev/settings/connections");
  const headings = await page.getByRole("heading", { level: 2 }).allTextContents();
  expect(headings.filter((h) => ["Connections", "Notifications"].includes(h))).toEqual(["Connections", "Notifications"]);
  await expect(page.getByTestId("connector-kind-scm").locator('[data-icon="brand:github"]')).toBeVisible();
  await expect(page.getByTestId("connector-kind-tracker").locator('[data-icon="brand:github"]')).toBeVisible();
  await expect(page.getByTestId("connector-kind-cd").locator('[data-icon="lucide:cd/demo"]')).toBeVisible();
  await expect(page.getByTestId("connector-kind-cd")).toContainText("Demo stand-in");
  await expect(page.getByTestId("connector-kind-cd")).toContainText("Not tested yet");
  await expect(page.getByTestId("connector-kind-runtime")).toContainText("Provided by the platform");
  await expect(page.getByTestId("connector-kind-runtime")).toContainText("agent-sandbox");
  expect(await page.content()).not.toContain("Unknown");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-settings-dev.png`, fullPage: true });
});

test("the groups' nav opens a group, and an old anchor lands on its group", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/p/dev/settings");
  await page.getByRole("navigation", { name: "Settings groups" }).getByRole("link", { name: "Agents" }).click();
  await expect(page).toHaveURL(/\/p\/dev\/settings\/agents$/);
  await expect(page.locator("#tools")).toBeVisible();
  await page.goto("/p/dev/settings#tools");
  await expect(page).toHaveURL(/\/p\/dev\/settings\/agents#tools$/);
});

test("Configure opens a drawer and Save closes it, the card then reads Connected", async ({ page }) => {
  await page.goto("/p/dev/settings/connections");
  const cd = page.getByTestId("connector-kind-cd");
  await cd.getByRole("button", { name: "Edit" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-settings-drawer.png` });
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(cd.getByTestId("state-cd")).toHaveText("Connected");
});

test("the runtime lists only what the deployment runs", async ({ page }) => {
  await page.goto("/p/dev/settings/connections");
  const runtime = page.getByTestId("connector-kind-runtime");
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

  const groups = page.getByRole("navigation", { name: "Settings groups" });
  await groups.getByRole("link", { name: "General" }).click();
  const general = page.getByTestId("section-general");
  await general.getByLabel("Test command").fill("pytest -q");
  await general.getByRole("button", { name: "Save general settings" }).click();
  await expect(general.getByRole("status")).toHaveText("General settings saved");

  await groups.getByRole("link", { name: "Connections" }).click();
  const notifications = page.getByTestId("section-notifications");
  await notifications.getByLabel("Slack channel").fill("#choregos-dev");
  await notifications.getByRole("button", { name: "Save notification settings" }).click();
  await expect(notifications.getByRole("status")).toHaveText("Notification settings saved");

  await groups.getByRole("link", { name: "Overview" }).click();
  await expect(panel.getByTestId("readiness-item-repository-checks")).toHaveAttribute("data-state", "done");
  await expect(panel.getByTestId("readiness-item-notifications")).toHaveAttribute("data-state", "done");
  // What the first save wrote is still there after the second one: no section drops another's keys.
  await groups.getByRole("link", { name: "General" }).click();
  await expect(page.getByTestId("section-general").getByLabel("Test command")).toHaveValue("pytest -q");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-readiness-after-fix.png`, fullPage: true });
});

test("a readiness fix link opens the group that holds its section", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/p/dev/settings");
  await page.getByTestId("fix-notifications").click();
  await expect(page).toHaveURL(/\/settings\/connections#notifications$/);
  await expect(page.locator("#notifications")).toBeVisible();
});

test("the tools, backend and danger zone sections work on the mock", async ({ page }) => {
  await page.goto("/p/dev/settings/agents");
  await expect(page.getByTestId("section-agents-models").getByLabel("Default backend")).toHaveValue("claude-code");
  await page.getByTestId("section-agents-models").getByRole("button", { name: "Save backend" }).click();
  await expect(page.getByTestId("section-agents-models").getByRole("status").first()).toHaveText("Backend saved");
  await page.goto("/p/dev/settings/danger");
  const danger = page.getByTestId("section-danger-zone");
  await danger.getByRole("button", { name: "Suspend" }).click();
  await expect(danger.getByTestId("confirmation")).toContainText("Suspend dev?");
  await danger.getByRole("button", { name: "Cancel" }).click();
  await expect(danger.getByTestId("confirmation")).toHaveCount(0);
});

test("no serious accessibility violation and no horizontal scroll at 390 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/p/dev/settings/connections");
  await expect(page.getByTestId("connector-kind-cd")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/after-settings-390.png`, fullPage: true });
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});
