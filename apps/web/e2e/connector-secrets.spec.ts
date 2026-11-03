import { expect, test } from "@playwright/test";

/**
 * A connector's secret is typed once and never shown again (S39-06d, ADR 0058). On the console's
 * mock API, which keeps what the real one would tell: set, version, when, by whom — never a value.
 * (A reload would reset the mock: the checks run in the same page.)
 */
const VALUE = "client-secret-AAAA-1111";

test("declare a SharePoint connector with a typed secret, then replace it", async ({ page }) => {
  await page.goto("/admin/connectors");
  await page.getByPlaceholder("entra-acme").fill("diametral-sharepoint");
  await page.locator("label:has(span:text-is('Type')) select").selectOption({ label: "SharePoint / OneDrive — documents" });
  await page.getByLabel("tenant id").fill("acme");
  await page.getByLabel("client id").fill("choregos-kb");
  const secret = page.getByLabel("client secret value");
  await expect(secret).toHaveAttribute("type", "password");
  await secret.fill(VALUE);
  await page.getByRole("button", { name: "Declare" }).click();

  // It is declared, its secret is "set", and the value is nowhere in the page.
  const panel = page.getByTestId("secrets-diametral-sharepoint");
  await expect(panel.getByTestId("secret-state-client_secret")).toContainText("set · rotated");
  expect(await page.content()).not.toContain(VALUE);

  // Replace: an empty input (the console does not know the old value), then the version moves.
  await panel.getByRole("button", { name: "Replace" }).click();
  await expect(panel.getByLabel("client secret value")).toHaveValue("");
  await panel.getByLabel("client secret value").fill("another-secret-BBBB-2222");
  await panel.getByRole("button", { name: "Save" }).click();
  await expect(panel.getByLabel("client secret value")).toHaveCount(0);
  await expect(panel.getByTestId("secret-state-client_secret")).toContainText("set · rotated");
  expect(await page.content()).not.toContain("another-secret-BBBB-2222");

  // Clear leaves the field to be set again.
  await panel.getByRole("button", { name: "Clear" }).click();
  await expect(panel.getByTestId("secret-state-client_secret")).toHaveText("not set");
});

test("a reference written by an operator shows as a reference, a typed value as set", async ({ page }) => {
  await page.goto("/admin/connectors");
  await expect(page.getByTestId("secret-state-client_secret").first()).toContainText("reference env:CONNECTOR_ENTRA_CLIENT_SECRET");
  await expect(page.getByTestId("secret-state-token")).toContainText("set · rotated 3 days ago by");
});
