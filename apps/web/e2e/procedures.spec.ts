import { expect, test } from "@playwright/test";

/**
 * The setup procedure beside every "add" form (S39-23b, ADR 0067), on the console's mock API,
 * which serves the real procedure files rendered with an example address. Not the live API.
 */
test("declaring a SharePoint connector opens its procedure over the page, at the first section", async ({ page }) => {
  await page.goto("/admin/connectors");
  const type = page.locator("label:has(span:text-is('Type')) select");
  const before = await type.boundingBox();
  await type.selectOption("documents/sharepoint");
  const panel = page.getByTestId("procedure-panel");
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText("Connect SharePoint and OneDrive");
  await expect(panel.getByRole("heading", { level: 3 }).first()).toHaveText("Before you start");
  await expect(panel.getByTestId("procedure-section-permissions")).toContainText("Sites.Selected");
  // The panel lies over the page: opening it never reflows the form being filled in.
  const after = await type.boundingBox();
  expect(before && after && after.x === before.x && after.width === before.width).toBe(true);
  await expect(page.getByRole("button", { name: "Declare" })).toBeVisible();
  // Close, and reopen from the button.
  await panel.getByTestId("procedure-close").click();
  await expect(panel).toHaveCount(0);
  // The button sits in the declare form; each connector already declared has its own, further down.
  await page.locator("form", { has: page.getByPlaceholder("entra-acme") }).getByRole("button", { name: "How to set this up" }).click();
  await expect(page.getByTestId("procedure-panel")).toBeVisible();
});

test("choosing Entra ID shows what to grant, and what stays unproven", async ({ page }) => {
  await page.goto("/admin/connectors");
  await page.locator("label:has(span:text-is('Type')) select").selectOption("identity/entra");
  const panel = page.getByTestId("procedure-panel");
  await expect(panel.getByRole("heading", { level: 2 })).toContainText("Entra");
  await expect(panel.getByTestId("procedure-section-permissions")).toContainText("administrative unit");
  await expect(panel.getByTestId("procedure-section-permissions")).toContainText("Not proven");
});

test("adding a Confluence source opens its procedure, and the connector's is one click away", async ({ page }) => {
  await page.goto("/knowledge/architecture-framework");
  await page.getByLabel("source kind").selectOption("confluence");
  const panel = page.getByTestId("procedure-panel");
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText("Add a Confluence space or page tree to a knowledge base");
  await page.getByRole("button", { name: "Set up the connector" }).click();
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText("Connect Confluence Cloud");
  await expect(panel.getByTestId("procedure-section-in_choregos")).toContainText("cloud_id");
});

test("at 390 px the panel is a sheet under the form and nothing scrolls sideways", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/admin/connectors");
  await page.locator("label:has(span:text-is('Type')) select").selectOption("documents/sharepoint");
  const panel = page.getByTestId("procedure-panel");
  await expect(panel).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const box = await panel.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 390 + 1).toBe(true);
});

test("a new remote agent opens the A2A procedure and switches to Rovo's on Atlassian's card (batch 4)", async ({ page }) => {
  await page.goto("/agents/new/remote");
  const panel = page.getByTestId("procedure-panel");
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText("Add a remote agent (A2A)");
  await expect(page.getByTestId("procedure-value-oauth_callback:remote")).toContainText("/api/v1/remote-credentials/oauth/callback");
  await page.getByLabel(/Card URL/).fill("https://a2a.atlassian.com/.well-known/agent.json");
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText("Add Atlassian Rovo as a remote agent");
  await expect(panel).toContainText("work-in-confluence");
});
