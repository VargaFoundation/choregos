import { expect, test } from "@playwright/test";

/**
 * Aucune page ne défile en largeur (S23-01). Le 08/10, TOUTES les pages de la console débordaient
 * sous 1280 px — l'en-tête ne se repliait pas (1091 px de large dans une fenêtre de 390) — et la
 * page des findings débordait même à 1280. Trois causes, trois gardes : l'en-tête replié derrière
 * « menu », les tableaux qui défilent dans leur case, et les textes `sr-only` qui ne s'échappent
 * plus d'un conteneur qui défile (`globals.css`).
 */
const PAGES = [
  "/",
  "/p/billing-api",
  "/p/billing-api/board",
  "/p/billing-api/trains",
  "/p/billing-api/findings",
  "/p/billing-api/memory",
  "/p/billing-api/workflows",
  "/p/billing-api/workflows/default-simple",
  "/p/billing-api/workflows/default-simple/map",
  "/p/billing-api/workflows/default-simple/yaml",
  "/p/billing-api/workflows/default-simple/history",
  "/p/billing-api/workflows/release-full/map",
  "/p/billing-api/agents",
  "/p/billing-api/actions",
  "/p/billing-api/actions/act-poste",
  "/p/billing-api/integrations",
  "/p/billing-api/settings",
  "/p/billing-api/items/w1",
  "/p/billing-api/items/w2",
  "/p/billing-api/runs/r3",
  "/inbox",
  "/agents",
  "/agents/onboarding-coordinator",
  "/skills",
  "/skills/onboarding-procedure",
  "/integrations",
  "/integrations/claude-code",
  "/admin",
  "/admin/connectors",
  "/admin/members",
  "/admin/audit",
  "/admin/platform",
  "/admin/edition",
  "/projects/new",
  "/login",
];

for (const largeur of [360, 768, 1024, 1280]) {
  test(`aucune page ne défile en largeur à ${largeur} px`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: largeur, height: 900 });
    const debordent: string[] = [];
    for (const chemin of PAGES) {
      await page.goto(chemin);
      await page.waitForLoadState("networkidle");
      await page.evaluate(() => document.fonts.ready);
      const largeurDeLaPage = await page.evaluate(() => document.documentElement.scrollWidth);
      if (largeurDeLaPage > largeur) debordent.push(`${chemin} : ${largeurDeLaPage} px`);
    }
    expect(debordent).toEqual([]);
  });
}

test("sous 1280 px, la navigation se replie derrière « menu », et s'y ouvre", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/p/billing-api");
  const navigation = page.getByRole("navigation", { name: "main navigation" });
  await expect(navigation.getByRole("link", { name: "skills" })).toBeHidden();
  const menu = navigation.getByRole("button", { name: "menu" });
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  // Une cible tactile d'au moins 44 px de haut.
  expect((await menu.boundingBox())?.height).toBeGreaterThanOrEqual(44);

  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await expect(navigation.getByRole("link", { name: "projects" })).toHaveAttribute("aria-current", "page");
  await expect(navigation.getByRole("button", { name: "sign out" })).toBeVisible();
  // Échap referme, et rend le focus au bouton.
  await page.keyboard.press("Escape");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(menu).toBeFocused();

  // Suivre un lien referme le menu.
  await menu.click();
  await navigation.getByRole("link", { name: "skills" }).click();
  await expect(page).toHaveURL(/\/skills$/);
  await expect(menu).toHaveAttribute("aria-expanded", "false");
});
