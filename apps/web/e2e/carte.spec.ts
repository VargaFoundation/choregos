import { expect, test } from "@playwright/test";

/**
 * La carte à plat se lit sans zoom (S21-07) : dix-huit états, là où l'ancienne carte en couloirs
 * débordait et s'ouvrait au zoom 0,8 avec une vue d'ensemble (revue du 07/10 : « pas très lisible »).
 */
test("une carte de dix-huit états se lit sans zoom : une colonne, aucun débordement, aucun chevauchement, des titres lisibles", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/workflows/release-full/map?vue=list");
  const carte = page.getByTestId("workflow-graph");
  await expect(carte.locator("[data-etat]")).toHaveCount(18);
  await page.evaluate(() => document.fonts.ready);
  const mesure = await page.evaluate(() => {
    const cases = [...document.querySelectorAll<HTMLElement>('[aria-label="nominal path"] > li [data-etat]')].map((el) =>
      el.closest("div")!.getBoundingClientRect(),
    );
    const titres = [...document.querySelectorAll<HTMLElement>("[data-etat] span.font-semibold")].map((el) =>
      parseFloat(getComputedStyle(el).fontSize),
    );
    return {
      largeur: document.documentElement.scrollWidth,
      fenetre: document.documentElement.clientWidth,
      gauches: [...new Set(cases.map((r) => Math.round(r.left)))],
      chevauchements: cases.slice(1).filter((r, i) => r.top < cases[i]!.bottom).length,
      plusPetitTitre: Math.min(...titres),
    };
  });
  expect(mesure.largeur).toBeLessThanOrEqual(mesure.fenetre);
  expect(mesure.gauches).toHaveLength(1);
  expect(mesure.chevauchements).toBe(0);
  expect(mesure.plusPetitTitre).toBeGreaterThanOrEqual(13);
  await expect(carte.getByRole("list", { name: "off the main path" }).locator(":scope > li")).toHaveCount(5);
});

test("le panneau reste à portée quand on descend la carte", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/p/billing-api/workflows/release-full/map?vue=list");
  await page.getByTestId("etat-verified_prod").click();
  await expect(page.getByTestId("panneau-etat")).toBeInViewport();
  await page.mouse.wheel(0, 3000);
  await expect(page.getByTestId("panneau-etat")).toBeInViewport();
});

test("Tab atteint une flèche, Entrée ouvre le panneau de sa transition", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/release-full/map?vue=list");
  await page.getByTestId("etat-ready").focus();
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("transition-t-implement")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("panneau-transition")).toBeVisible();
  await expect(page.getByText("transition t-implement", { exact: true })).toBeVisible();
});

test("la carte s'ouvre en serpentin, sans déborder, et une étape cliquée ouvre sa transition (S22-05)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/workflows/release-full/map");
  const carte = page.getByTestId("carte-en-serpentin");
  await expect(carte).toBeVisible();
  const debord = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(debord).toBeLessThanOrEqual(0);
  await carte.getByTestId("etape-t-implement").click();
  await expect(page.getByTestId("panneau-transition")).toBeVisible();
  await page.getByRole("button", { name: "list" }).click();
  await expect(page).toHaveURL(/vue=list/);
  await expect(page.getByTestId("workflow-graph")).toBeVisible();
});
