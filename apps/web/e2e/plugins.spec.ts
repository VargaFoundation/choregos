import { expect, test } from "@playwright/test";

/** S43-08: Catalogue → Plugins in the demo — install (202), the refused self-approval, another's approval, configure a server. */

test("install waits for a second administrator, who approves; a server links to its configuration", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  await page.getByRole("navigation").getByRole("link", { name: "Plugins" }).click();
  await expect(page).toHaveURL(/\/catalogue\/plugins$/);
  await expect(page.getByTestId("trust-varga")).toContainText("keyless");
  await expect(page.getByTestId("source-partner")).toContainText("did not answer");

  // lea asked for hello: the demo's administrator approves it, and it is installed.
  await page.getByTestId("approval-pa-1").getByRole("button", { name: "Approve and install" }).click();
  await expect(page.getByText("hello 1.0.0 is installed.")).toBeVisible();
  await expect(page.getByTestId("installed-hello")).toBeVisible();

  // An upgrade of the default servicenow brings a server: it waits, and its asker cannot approve it.
  await page.getByTestId("installed-servicenow").getByRole("button", { name: "Upgrade to 0.2.0" }).click();
  await expect(page.getByTestId("outcome-pending")).toContainText("it brings the server `servicenow`");
  const mine = page.locator("[data-testid^=approval-]").filter({ hasText: "You asked for it" });
  await mine.getByRole("button", { name: "Approve and install" }).click();
  await expect(mine.getByRole("alert")).toContainText("the administrator who asked cannot approve");

  // Uninstalling triage keeps what the organisation changed.
  const triage = page.getByTestId("installed-triage");
  await triage.getByRole("button", { name: "Uninstall" }).click();
  await triage.getByTestId("confirmation").getByRole("button", { name: "Uninstall" }).click();
  await expect(page.getByTestId("outcome-uninstalled")).toContainText("agents: triage-lead; skills: sizing.");

  // The server is unconfigured; Configure opens its connector with its own settings.
  const server = page.getByTestId("installed-servicenow").getByTestId("server-servicenow");
  await expect(server).toContainText("unconfigured");
  await server.getByRole("link", { name: "Configure" }).click();
  await expect(page).toHaveURL(/\/admin\/connectors\?connector=servicenow$/);
  const card = page.getByTestId("connector-servicenow");
  await expect(card.getByTestId("plugin-server-servicenow")).toBeVisible();
  await expect(card.getByLabel(/instance url/)).toBeVisible();
});
