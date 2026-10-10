import { expect, test } from "@playwright/test";

/**
 * Le thème de la console (ADR 0043) : sombre par défaut, clair ou celui du système au choix, et le
 * choix s'applique AVANT la première peinture — le script d'en-tête le pose, pas React.
 */

const fond = (page: import("@playwright/test").Page) =>
  page.evaluate(() => getComputedStyle(document.body).backgroundColor);

test("sombre par défaut, en Geist, le monospace gardé pour le code", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  // slateDark 1 : un quasi-noir froid, plus le #0A0A0A du design system.
  expect(await fond(page)).toBe("rgb(17, 17, 19)");
  expect(await page.evaluate(() => getComputedStyle(document.body).fontFamily)).toMatch(/Geist/);
  expect(await page.evaluate(() => getComputedStyle(document.body).fontFamily)).not.toMatch(/JetBrains/);
});

test("le clair choisi survit au rechargement, et s'applique sans le JavaScript de l'application", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("radio", { name: "light" }).check({ force: true });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await fond(page)).toBe("rgb(252, 252, 253)");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  // Sans les scripts de l'application, seul le script d'en-tête tourne : c'est lui qui pose le thème.
  await page.route(/\/_next\/static\/.*\.js(\?.*)?$/, (route) => route.abort());
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await fond(page)).toBe("rgb(252, 252, 253)");
});

test("« system » suit le mode du système", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await page.getByRole("radio", { name: "system" }).check({ force: true });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
