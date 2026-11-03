import { expect, test } from "@playwright/test";

/**
 * The shell of an operations console (S24-03, ADR 0043): from 1280 px, the navigation is
 * in a sidebar that folds into a rail; the top bar is 48 px at every width.
 */

test("at 1280 px, the navigation is in the sidebar, with no \"menu\" button", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/p/billing-api/board");
  const barre = page.getByRole("complementary", { name: "Sidebar" });
  const navigation = barre.getByRole("navigation", { name: "Main navigation" });
  // Inside a project, the sidebar lists its sections (S30-03).
  await expect(navigation.getByRole("link", { name: "Board" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("button", { name: /^Menu/ })).toBeHidden();
  // A single main navigation at a time: the header's is hidden.
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toHaveCount(1);
  expect((await barre.boundingBox())?.width).toBe(240);
});

for (const largeur of [360, 768, 1024, 1280]) {
  test(`the top bar is 48 px at ${largeur} px`, async ({ page }) => {
    await page.setViewportSize({ width: largeur, height: 800 });
    await page.goto("/p/billing-api");
    expect((await page.getByRole("banner").boundingBox())?.height).toBe(48);
    // Below 1280 px, the sidebar is not displayed.
    await expect(page.getByRole("complementary", { name: "Sidebar" })).toBeVisible({ visible: largeur >= 1280 });
  });
}

test("the bar folds into a rail, the fold is kept, and applies without the application's JavaScript", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const barre = page.getByRole("complementary", { name: "Sidebar" });
  expect((await barre.boundingBox())?.width).toBe(240);

  await barre.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-sidebar", "rail");
  expect((await barre.boundingBox())?.width).toBe(52);
  // Folded, each entry keeps its name for a screen reader, and its tooltip for the mouse.
  await expect(barre.getByRole("link", { name: "Skills" })).toHaveAttribute("title", "Skills");
  await expect(barre.getByRole("button", { name: "Expand sidebar" })).toBeVisible();

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-sidebar", "rail");

  // Without the application's scripts, only the header script runs: it is what folds the bar.
  await page.route(/\/_next\/static\/.*\.js(\?.*)?$/, (route) => route.abort());
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-sidebar", "rail");
  expect((await barre.boundingBox())?.width).toBe(52);
});

test("Integrations is in the Organisation group, not in the Catalogue (S40-08)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  const navigation = page.getByRole("complementary", { name: "Sidebar" }).getByRole("navigation", { name: "Main navigation" });
  const lien = navigation.getByRole("list", { name: "Organisation" }).getByRole("link", { name: "Integrations" });
  await expect(lien).toHaveAttribute("href", "/integrations");
  await expect(navigation.getByRole("list", { name: "Catalogue" }).getByRole("link", { name: "Integrations" })).toHaveCount(0);
});

test("About opens a panel with the deployed version, from the sidebar and from the rail (S42-12)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const barre = page.getByRole("complementary", { name: "Sidebar" });
  await barre.getByRole("button", { name: "About" }).click();
  const panel = page.getByRole("dialog", { name: "About" });
  await expect(panel.getByText("Community edition")).toBeVisible();
  await expect(panel.getByTestId("about-core-licence")).toHaveText("Apache-2.0");
  await expect(panel.getByText("0123abc4567def890123abc4567def890123abc4")).toBeVisible();
  await expect(panel.getByRole("link", { name: "Source repository" })).toBeVisible();
  await panel.getByRole("button", { name: "Close" }).click();
  await expect(panel).toBeHidden();

  // Folded: the icon stays, with its name.
  await barre.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(barre.getByRole("button", { name: "About" })).toBeVisible();
  await barre.getByRole("button", { name: "About" }).click();
  await expect(page.getByRole("dialog", { name: "About" }).getByText("Community edition")).toBeVisible();
});

test("About is in the mobile menu (S42-12)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/");
  await page.getByRole("button", { name: /^Menu/ }).click();
  await page.getByRole("button", { name: "About" }).click();
  await expect(page.getByRole("dialog", { name: "About" }).getByText("Community edition")).toBeVisible();
});
