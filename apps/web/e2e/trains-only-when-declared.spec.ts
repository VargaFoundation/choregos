import { expect, test } from "@playwright/test";

/**
 * Trains exist only when a workflow of the project has a `via: release_train` transition (S30-07,
 * plan item 17). The HR project of the demo (`joiners-leavers` template) has none: no trains tab, no
 * "trains in flight". Billing API ships software through the train: both show.
 */
test("the joiners-leavers project shows no trains tab; the software delivery one does", async ({ page }) => {
  await page.goto("/p/people-ops");
  const tabs = page.getByRole("navigation", { name: "project sections" });
  await expect(tabs.getByRole("link", { name: "board" })).toBeVisible();
  await expect(tabs.getByRole("link", { name: "trains" })).toHaveCount(0);
  await expect(page.getByText("active tickets")).toBeVisible();
  await expect(page.getByText("trains in flight")).toHaveCount(0);

  await page.goto("/p/billing-api");
  await expect(tabs.getByRole("link", { name: "trains" })).toBeVisible();
  await expect(page.getByText("trains in flight")).toBeVisible();
});

test("the project list counts trains only for the projects that have some", async ({ page }) => {
  await page.goto("/");
  const card = (name: string) => page.getByRole("link").filter({ has: page.getByRole("heading", { name }) });
  await expect(card("People Ops")).toContainText("active tickets");
  await expect(card("People Ops")).not.toContainText("trains in flight");
  await expect(card("Billing API")).toContainText("trains in flight");
});
