import { expect, test } from "@playwright/test";

/** S39-10d: browse the MCP catalogue, install an entry, land on the connector it created (ADR 0065). */

test("browse, filter, install and land on the connector", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/admin/connectors");
  await page.getByRole("link", { name: "MCP catalogue" }).click();
  await expect(page).toHaveURL(/\/admin\/connectors\/catalogue$/);
  await expect(page.getByTestId("catalogue-version")).toContainText("Catalogue 2026.10.0");

  // The facets narrow the grid; an OAuth server stays listed, with its reason, and cannot be installed.
  await page.getByLabel("Search the catalogue").fill("notion");
  const notion = page.getByTestId("entry-notion");
  await expect(notion).toContainText("Needs OAuth");
  await expect(notion.getByRole("button", { name: "Install" })).toBeDisabled();
  await page.getByLabel("Installable only").check();
  await expect(page.getByText("Nothing matches")).toBeVisible();
  await page.getByLabel("Search the catalogue").fill("github");
  await expect(page.getByTestId("entry-github")).toBeVisible();

  // The drawer: a name and a secret by reference, never the key.
  await page.getByTestId("entry-github").getByRole("button", { name: "Install" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toContainText("Install GitHub");
  await drawer.getByLabel("Secret, by reference").fill("env:CONNECTOR_GITHUB");
  await drawer.getByRole("button", { name: "Install" }).click();

  // It lands on the connector, which exists and offers its discovery.
  await expect(page).toHaveURL(/\/admin\/connectors\?name=github/);
  await expect(page.getByTestId("installed-from-catalogue")).toContainText("Installed github from the catalogue");
  const card = page.getByTestId("connector-github");
  await expect(card).toBeVisible();
  await expect(card.getByRole("button", { name: "Discover" })).toBeVisible();

  // And the catalogue now says so.
  await page.getByRole("link", { name: "MCP catalogue" }).click();
  await page.getByLabel("Search the catalogue").fill("github");
  await expect(page.getByTestId("entry-github")).toContainText("Installed");
});
