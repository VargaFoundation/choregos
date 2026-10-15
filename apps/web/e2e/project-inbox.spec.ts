import { expect, test } from "@playwright/test";

/** S39-04: a project's Inbox entry stays in the project and lists only what waits there. */

test("the project's sidebar Inbox opens the project's inbox, without another project's budget alert", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/p/checkout-web/board");
  const bar = page.getByRole("complementary", { name: "Sidebar" });
  await bar.getByRole("link", { name: "Inbox" }).click();
  await expect(page).toHaveURL(/\/p\/checkout-web\/inbox$/);
  await expect(page.getByText("No budget crossed")).toBeVisible();
  await expect(page.getByText(/Project billing-api has spent/)).toHaveCount(0);
  await expect(bar.getByTestId("project-switcher")).toBeVisible();
});

test("the organisation's inbox can be narrowed to a project", async ({ page }) => {
  await page.goto("/inbox?project=billing-api");
  await expect(page.getByTestId("budgets-franchis").getByText(/Project billing-api has spent/)).toBeVisible();
  await expect(page.getByTestId("budgets-franchis").getByText(/^Agent /)).toHaveCount(0);
});
