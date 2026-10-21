import { expect, test } from "@playwright/test";

/** S40-09c: the connector catalogue, by family, with icons and the caller's gestures (ADR 0074). */

test("sidebar to Connector types, a family with its icon, and Declare lands on the form with the type chosen", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  await page.getByRole("navigation").getByRole("link", { name: "Connector types" }).first().click();
  await expect(page).toHaveURL(/\/catalogue\/connectors$/);
  await expect(page.getByRole("heading", { name: "Connector types", level: 1 })).toBeVisible();

  const sourceCode = page.getByTestId("family-Source code");
  await expect(sourceCode.getByRole("heading", { name: "Source code", level: 2 })).toBeVisible();
  const github = page.getByTestId("entry-scm-github");
  await expect(github.locator('[data-icon="brand:github"]')).toBeVisible();
  await expect(github).toContainText("Configured in billing-api");

  await page.getByTestId("entry-identity-entra").getByRole("link", { name: "Declare" }).click();
  await expect(page).toHaveURL(/\/admin\/connectors\?type=identity\/entra$/);
});
