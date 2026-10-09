import { expect, test } from "@playwright/test";

/**
 * Ce qu'une personne lit et touche (S23-11, seconde passe du 08/10) : des états en mots, pas en
 * codes ; un libellé visible au-dessus de chaque champ de saisie ; 44 px au doigt ; un tableau
 * qui défile le montre.
 */
test("un état se lit en mots, et une phrase du moteur sans backticks", async ({ page }) => {
  await page.goto("/p/billing-api/trains");
  await expect(page.locator("main")).toContainText("rolled back");
  await expect(page.locator("main")).not.toContainText("rolled_back");
  await expect(page.locator("main")).not.toContainText("awaiting_approval");

  await page.goto("/p/billing-api/workflows/default-simple");
  const etape = page.getByRole("list", { name: "steps of the process" });
  await expect(etape).toContainText("the agent refiner");
  await expect(etape).not.toContainText("`");
  await expect(etape.locator("code").first()).toHaveText("refiner");
});

const CHAMPS: [string, string][] = [
  ["/admin", "token name"],
  ["/admin/members", "member e-mail"],
  ["/admin/audit", "actor"],
  ["/p/billing-api/memory", "search the memory"],
  ["/p/billing-api/trains", "freeze reason"],
  ["/p/billing-api/runs/r3", "filter the journal"],
  ["/inbox", "reason for sending back"],
];

test("chaque champ de saisie porte un libellé visible, pas seulement un placeholder", async ({ page }) => {
  for (const [chemin, libelle] of CHAMPS) {
    await page.goto(chemin);
    await expect(page.getByText(libelle, { exact: true }).first(), `${chemin} : ${libelle}`).toBeVisible();
    await expect(page.getByLabel(libelle, { exact: true }).first()).toBeVisible();
  }
});

test.describe("au doigt", () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });

  test("un petit bouton fait 44 px de haut, et un tableau qui défile porte son ombre", async ({ page }) => {
    await page.goto("/admin/members");
    const bouton = page.getByRole("button", { name: /^remove / }).first();
    expect((await bouton.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    const conteneur = page.getByRole("table").first().locator("xpath=..");
    expect(await conteneur.evaluate((e) => e.scrollWidth > e.clientWidth)).toBe(true);
    expect(await conteneur.evaluate((e) => getComputedStyle(e).backgroundImage)).toContain("radial-gradient");
  });
});
