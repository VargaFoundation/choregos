import { expect, test } from "@playwright/test";

/**
 * The Models page of the administration (S39-07c, ADR 0060), on the mock API: a model disabled there
 * leaves the agent builder's picker. Navigation stays inside the page (no reload), as the mock's state
 * lives for the length of the page.
 */
test("a model disabled in /admin/models is not offered when creating an agent", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/admin/models");
  await expect(page.getByRole("heading", { name: "Models", exact: true })).toBeVisible();
  const row = page.getByTestId("model-platform/cheap");
  await expect(row).toBeVisible();
  await row.getByLabel("Enable platform/cheap").click();
  await expect(page.getByText("platform/cheap disabled")).toBeVisible();

  await page.getByRole("link", { name: "All of acme" }).click();
  await page.getByRole("link", { name: "Agents", exact: true }).first().click();
  await page.getByRole("link", { name: "New agent" }).click();
  await page.getByLabel("Name").fill("Triage helper");
  await page.getByLabel("What should it do?").fill("Sorts the incoming tickets by urgency, with a short reason.");
  await page.getByRole("button", { name: "Start from a blank form" }).click();
  const model = page.getByTestId("agent-builder").getByLabel("Model");
  await expect(model.locator("option", { hasText: "platform/standard" })).toHaveCount(1);
  await expect(model.locator("option", { hasText: "platform/cheap" })).toHaveCount(0);
});
