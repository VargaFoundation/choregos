import { expect, test } from "@playwright/test";

/**
 * A connector says what its state means, and saving tests it (S40-05). On the console's mock, whose
 * `PUT` answers as the real API now does: tested by the save itself.
 */
test("the settings never print the raw word Unknown, and a save reads Connected", async ({ page }) => {
  await page.goto("/p/billing-api/settings");
  const scm = page.getByTestId("connecteur-scm");
  // Required, nothing behind it, nothing provided by the platform.
  await expect(scm.getByTestId("etat-scm")).toHaveText("Not connected");
  // The runtime is what the deployment runs.
  await expect(page.getByTestId("connecteur-runtime").getByTestId("etat-runtime")).toHaveText("Provided by the platform");

  await scm.getByRole("button", { name: "Configure" }).click();
  // The repository is picked from what the App sees (S40-04c), not typed beside an installation id.
  await scm.getByRole("button", { name: /acme\/billing-api/ }).click();
  await scm.getByRole("button", { name: "Save" }).click();
  await expect(scm.getByTestId("etat-scm")).toHaveText("Connected");
  await expect(page.getByRole("status")).toContainText("scm connector saved: connected");
});

test("a demo stand-in is named as one", async ({ page }) => {
  await page.goto("/p/billing-api/settings");
  await page.getByText("Add a connector").locator("..").getByRole("combobox").selectOption("cd");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const cd = page.getByTestId("connecteur-cd");
  await cd.getByRole("button", { name: "Configure" }).click();
  await cd.getByLabel("Implementation").selectOption({ label: "Demo stand-in (simulated CD)" });
  await cd.getByRole("button", { name: "Save" }).click();
  await expect(cd.getByTestId("etat-cd")).toHaveText("Connected");
  await expect(cd).toContainText("Demo stand-in");
  await expect(cd).not.toContainText("Unknown");
});

test("a failing connector says why", async ({ page }) => {
  await page.goto("/p/checkout-web/settings");
  await expect(page.getByTestId("etat-cd")).toHaveText("Failing");
  await expect(page.getByTestId("connecteur-cd")).toContainText("401 from Argo CD");
  expect(await page.content()).not.toContain("Unknown");
});
