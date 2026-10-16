import { expect, test } from "@playwright/test";

/**
 * The setup procedure beside every "add" form (S39-23b, ADR 0067), on the console's mock API,
 * which serves the real procedure files rendered with an example address. Not the live API.
 */
test("declaring a SharePoint connector opens its procedure beside the form, at the first section", async ({ page }) => {
  await page.goto("/admin/connectors");
  await page.locator("label:has(span:text-is('Type')) select").selectOption("documents/sharepoint");
  const panel = page.getByTestId("procedure-panel");
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText("Connect SharePoint and OneDrive");
  await expect(panel.getByRole("heading", { level: 3 }).first()).toHaveText("Before you start");
  await expect(panel.getByTestId("procedure-section-permissions")).toContainText("Sites.Selected");
  // The form is still usable while the panel is open: the page made room for it.
  await expect(page.getByRole("button", { name: "Declare" })).toBeVisible();
  const box = await page.getByRole("button", { name: "Declare" }).boundingBox();
  const side = await panel.boundingBox();
  expect(box && side && box.x + box.width <= side.x + 1).toBe(true);
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
