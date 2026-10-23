import { expect, test } from "@playwright/test";

/**
 * The Organisation card on /admin (S40-24), on the mock API: the display name is changed, saved with
 * exactly the name and currency, and the console shows it back after leaving the page and returning.
 */
test("an organisation is renamed from the administration page", async ({ page }) => {
  await page.goto("/admin");
  const name = page.getByLabel("Display name");
  await expect(name).toHaveValue("Varga Foundation");
  await name.fill("Diametral");
  await page.getByLabel("Currency").selectOption("USD");
  await page.getByRole("button", { name: "Save the organisation" }).click();
  await expect(page.getByText("Organisation saved")).toBeVisible();

  const writes = await page.evaluate(
    () => (window as unknown as { __mockWrites?: { method: string; path: string; body: unknown }[] }).__mockWrites ?? [],
  );
  const patch = writes.filter((w) => w.method === "PATCH" && /\/orgs\/[^/]+$/.test(w.path)).at(-1)!;
  expect(patch.body).toEqual({ name: "Diametral", currency: "USD" });

  await page.getByRole("link", { name: "All of acme" }).click();
  await page.getByRole("link", { name: "Admin", exact: true }).first().click();
  await expect(page.getByLabel("Display name")).toHaveValue("Diametral");
});
