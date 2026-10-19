import { expect, test } from "@playwright/test";

/** S39-10d: browse the MCP catalogue, install an entry, land on the connector it created (ADR 0065). */

test("browse, filter, install and land on the connector", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  await page.getByRole("navigation").getByRole("link", { name: "MCP servers" }).click();
  await expect(page).toHaveURL(/\/catalogue\/mcp-servers$/);
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
  // Both aids at this one gesture (S39-23 and S39-22): the setup procedure beside the form, and, because the
  // organisation has not opened the server's host, the network access modal before the install goes through.
  await expect(drawer.getByTestId("install-procedure")).toBeVisible();
  await drawer.getByLabel("Secret, by reference").fill("env:CONNECTOR_GITHUB");
  await drawer.getByRole("button", { name: "Install" }).click();
  const modal = page.getByTestId("egress-modal");
  await expect(modal).toBeVisible();
  await expect(modal.getByTestId("org-api.githubcopilot.com")).toHaveText("not allowed");
  await modal.getByRole("button", { name: "Allow and continue" }).click();
  await expect(modal).toBeHidden();

  // It lands on the connector, which exists and offers its discovery.
  await expect(page).toHaveURL(/\/admin\/connectors\?name=github/);
  await expect(page.getByTestId("installed-from-catalogue")).toContainText("Installed github from the catalogue");
  const card = page.getByTestId("connector-github");
  await expect(card).toBeVisible();
  await expect(card.getByRole("button", { name: "Discover" })).toBeVisible();

  // And the catalogue now says so.
  await page.getByRole("link", { name: "MCP servers" }).first().click();
  await page.getByLabel("Search the catalogue").fill("github");
  const github = page.getByTestId("entry-github");
  await expect(github).toContainText("Installed");

  // Installed for selected projects (the default): on in no project yet, then on for billing-api.
  await expect(github).toContainText("on in no project yet");
  await github.getByRole("button", { name: "Projects…" }).click();
  await page.getByRole("dialog").getByLabel("billing-api").check();
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  await expect(github).toContainText("on in billing-api");
});

test("the old administration path redirects to Catalogue, MCP servers", async ({ page }) => {
  await page.goto("/admin/connectors/catalogue");
  await expect(page).toHaveURL(/\/catalogue\/mcp-servers$/);
  await expect(page.getByRole("heading", { name: "MCP servers", level: 1 })).toBeVisible();
});
