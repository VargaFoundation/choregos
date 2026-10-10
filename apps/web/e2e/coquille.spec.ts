import { expect, test } from "@playwright/test";

/**
 * La coquille d'une console d'opérations (S24-03, ADR 0043) : à partir de 1280 px, la navigation est
 * dans une barre latérale repliable en rail ; la barre du haut fait 48 px à toutes les largeurs.
 */

test("à 1280 px, la navigation est dans la barre latérale, sans bouton « menu »", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/p/billing-api/board");
  const barre = page.getByRole("complementary", { name: "sidebar" });
  const navigation = barre.getByRole("navigation", { name: "main navigation" });
  await expect(navigation.getByRole("link", { name: "projects" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("button", { name: /^menu/ })).toBeHidden();
  // Une seule navigation principale à la fois : celle de l'en-tête est cachée.
  await expect(page.getByRole("navigation", { name: "main navigation" })).toHaveCount(1);
  expect((await barre.boundingBox())?.width).toBe(240);
});

for (const largeur of [360, 768, 1024, 1280]) {
  test(`la barre du haut fait 48 px à ${largeur} px`, async ({ page }) => {
    await page.setViewportSize({ width: largeur, height: 800 });
    await page.goto("/p/billing-api");
    expect((await page.getByRole("banner").boundingBox())?.height).toBe(48);
    // Sous 1280 px, la barre latérale n'est pas affichée.
    await expect(page.getByRole("complementary", { name: "sidebar" })).toBeVisible({ visible: largeur >= 1280 });
  });
}

test("la barre se replie en rail, le repli se garde, et s'applique sans le JavaScript de l'application", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const barre = page.getByRole("complementary", { name: "sidebar" });
  expect((await barre.boundingBox())?.width).toBe(240);

  await barre.getByRole("button", { name: "collapse sidebar" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-sidebar", "rail");
  expect((await barre.boundingBox())?.width).toBe(52);
  // Replié, chaque entrée garde son nom pour un lecteur d'écran, et son infobulle pour la souris.
  await expect(barre.getByRole("link", { name: "skills" })).toHaveAttribute("title", "skills");
  await expect(barre.getByRole("button", { name: "expand sidebar" })).toBeVisible();

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-sidebar", "rail");

  // Sans les scripts de l'application, seul le script d'en-tête tourne : c'est lui qui replie la barre.
  await page.route(/\/_next\/static\/.*\.js(\?.*)?$/, (route) => route.abort());
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-sidebar", "rail");
  expect((await barre.boundingBox())?.width).toBe(52);
});
