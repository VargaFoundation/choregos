import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Accessibilité mesurée, pas déclarée : axe passe sur les écrans principaux en mode démo.
 * Toute violation bloque, bonnes pratiques comprises (S23-05) : quand seules « serious » et
 * « critical » bloquaient, 23 pages sur 36 sautaient du `h1` au `h3` sans que rien ne rougisse.
 */
const PAGES = [
  "/",
  "/p/billing-api",
  "/p/billing-api/board",
  "/p/billing-api/trains",
  "/p/billing-api/workflows",
  "/p/billing-api/workflows/new",
  "/p/billing-api/workflows/default-simple",
  "/p/billing-api/workflows/default-simple/map",
  "/p/billing-api/workflows/default-simple/yaml",
  "/p/billing-api/workflows/default-simple/history",
  "/login",
  "/admin",
  "/admin/connectors",
  "/admin/members",
  "/admin/audit",
  "/admin/platform",
  "/admin/edition",
  "/admin/x/scim",
  "/integrations",
  "/inbox",
  "/p/billing-api/actions",
  "/p/billing-api/actions/act-poste",
  "/p/billing-api/actions/act-comptes",
  "/agents",
  "/agents/onboarding-coordinator",
  "/agents/leas-claude-code",
  "/skills",
  "/skills/onboarding-procedure",
  "/p/billing-api/agents",
  "/p/billing-api/integrations/claude-desktop",
  "/p/billing-api/actions/pr1",
  "/p/billing-api/workflows/release-full/map",
  "/p/billing-api/workflows/release-full/map?vue=list",
  // Le parcours animé d'un ticket (S22-02) : en cours, et fini.
  "/p/billing-api/items/w1",
  "/p/billing-api/items/w4",
];

// Les deux thèmes (ADR 0043) : un contraste qui tient en sombre peut tomber en clair, et l'inverse.
for (const theme of ["dark", "light"] as const) {
  test.describe(`thème ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((choix) => window.localStorage.setItem("choregos.theme", choix), theme);
    });

    for (const path of PAGES) {
      test(`${path} sans violation sérieuse`, async ({ page }) => {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        const results = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
          .analyze();
        const bloquantes = results.violations;
        expect(
          bloquantes.map((v) => `${v.id} (${v.impact}) : ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
        ).toEqual([]);
      });
    }
  });
}

/** La carte en cours d'édition (S16-12) : le panneau d'un état et le brouillon passent axe aussi. */
test("carte en cours d'édition sans violation sérieuse", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/map?vue=list");
  await page.getByTestId("workflow-graph").getByTestId("etat-inbox").click();
  const panneau = page.getByTestId("panneau-etat");
  await panneau.getByLabel("label").fill("Nouvelles demandes");
  await panneau.getByRole("button", { name: "set label" }).click();
  await expect(page.getByTestId("brouillon")).toContainText("1 change not published yet");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]).analyze();
  const bloquantes = results.violations;
  expect(
    bloquantes.map((v) => `${v.id} (${v.impact}) : ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
  ).toEqual([]);
});

/** Les connecteurs d'un projet (S19-01) : la section seule — le reste des réglages a ses propres pages. */
test("réglages : la section des connecteurs sans violation sérieuse", async ({ page }) => {
  await page.goto("/p/billing-api/settings");
  await page.getByTestId("connecteur-tracker").getByRole("button", { name: "edit" }).click();
  const results = await new AxeBuilder({ page })
    .include('[data-testid="connecteurs"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
    .analyze();
  const bloquantes = results.violations;
  expect(
    bloquantes.map((v) => `${v.id} (${v.impact}) : ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
  ).toEqual([]);
});

/**
 * Le graphe de workflow se parcourt au clavier : Tab atteint les états, les flèches suivent
 * les transitions, et l'état sous le curseur est décrit (aria-live) sous la carte.
 */
test("graphe de workflow : parcours au clavier et description de l'état", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/map?vue=list");
  const carte = page.getByTestId("workflow-graph");
  await expect(carte.locator("[data-etat]")).toHaveCount(4);
  const focus = page.getByTestId("workflow-graph-focus");
  await expect(focus).toContainText("Tab reaches the states");

  await carte.getByTestId("etat-inbox").focus();
  await expect(focus).toHaveText("To triage (agent): → Ready (by refiner).");

  await page.keyboard.press("ArrowRight");
  await expect(carte.getByTestId("etat-ready")).toBeFocused();
  await expect(focus).toHaveText("Ready (agent): → Done (by dev, gates scope_respected); → Needs a human when retries run out.");
  // Au clavier comme au survol : les escalades de l'état parcouru se montrent, puis se replient.
  await expect(carte.getByTestId("secondaires-ready")).toBeVisible();

  await page.keyboard.press("ArrowRight");
  await expect(carte.getByTestId("etat-done")).toBeFocused();
  await expect(focus).toHaveText("Done (end, terminal): no outgoing transition.");
  await expect(carte.getByTestId("secondaires-ready")).toHaveCount(0);

  // Au bout du chemin, → reste sur place ; Début revient au premier état.
  await page.keyboard.press("ArrowRight");
  await expect(carte.getByTestId("etat-done")).toBeFocused();
  await page.keyboard.press("Home");
  await expect(carte.getByTestId("etat-inbox")).toBeFocused();
  await expect(carte.getByTestId("etat-inbox")).toHaveAttribute("aria-label", "To triage, agent");

  // Entrée sur un état ouvre son panneau : la carte se modifie au clavier aussi (S16-12).
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("panneau-etat")).toBeVisible();
});

test("les barres d'onglets ne défilent pas en hauteur : Windows y dessinait ses flèches", async ({ page }) => {
  // L'onglet actif descend d'un pixel sur le filet ; sans la marge qui le reçoit, chaque barre
  // débordait d'un pixel en hauteur, et Windows dessinait ▲ ● ▼ au bout de la ligne (relevé le 06/10).
  const barres: Array<[string, string]> = [
    ["/p/billing-api/workflows/default-simple/map", "project sections"],
    ["/p/billing-api/workflows/default-simple/map", "views of default-simple"],
    ["/admin", "administration sections"],
    ["/integrations/claude-code", "clients"],
  ];
  for (const [chemin, nom] of barres) {
    await page.goto(chemin);
    const barre = page.getByRole("navigation", { name: nom });
    await expect(barre).toBeVisible();
    const deborde = await barre.evaluate((element) => element.scrollHeight - element.clientHeight);
    expect(deborde, `${chemin} · ${nom}`).toBe(0);
  }
});

/**
 * À 1280 px, rien ne passe à la ligne (S21-03) : la navigation est dans la barre latérale (S24-03),
 * l'organisation et la session dans la barre du haut.
 */
test("l'en-tête tient sur une ligne à 1280 px, sans défilement horizontal", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  for (const path of ["/", "/skills", "/integrations", "/admin"]) {
    await page.goto(path);
    await expect(page.getByRole("link", { name: "skills" })).toBeVisible();
    // La session arrive après la page : l'en-tête ne porte son poids réel (organisation, personne,
    // déconnexion) qu'une fois qu'elle est là.
    await expect(page.getByRole("button", { name: "sign out" })).toBeVisible();
    // Mesurer avant les polices, c'est mesurer la police de repli : plus étroite, elle ne casse rien.
    await page.evaluate(() => document.fonts.ready);
    const mesure = await page.evaluate(() => ({
      largeur: document.documentElement.scrollWidth,
      fenetre: document.documentElement.clientWidth,
      // L'en-tête et la barre ont une hauteur fixe : un libellé qui passe à la ligne déborde DEDANS
      // sans la changer. Ce qui se mesure, c'est chaque élément : une ligne de texte, pas trois.
      // La barre du haut seule : l'en-tête d'une page (`<header>` aussi) porte des boutons de 32 px.
      hautes: [
        ...document.querySelectorAll(
          'nav[aria-label="main navigation"] :is(a, button, span, select), header[data-barre-du-haut] :is(a, button, span, select)',
        ),
      ]
        .filter((el) => el.getBoundingClientRect().height > 30)
        .map((el) => (el.textContent ?? "").trim()),
    }));
    expect(mesure.largeur, path).toBeLessThanOrEqual(mesure.fenetre);
    expect(mesure.hautes, path).toEqual([]);
  }
});

/** Chaque page a son titre, et le premier arrêt de tabulation mène au contenu (S23-05). */
test("titres de page distincts, et un lien d'évitement vers le contenu", async ({ page }) => {
  const titres = new Map<string, string>();
  for (const path of ["/", "/inbox", "/p/billing-api", "/p/billing-api/board", "/p/billing-api/items/w1", "/admin"]) {
    await page.goto(path);
    await expect(page).not.toHaveTitle("");
    titres.set(await page.title(), path);
  }
  expect(titres.size).toBe(6);
  await page.goto("/p/billing-api/board");
  await expect(page).toHaveTitle("board · billing-api · choregos");
  await page.keyboard.press("Tab");
  const evitement = page.getByRole("link", { name: "skip to content" });
  await expect(evitement).toBeFocused();
  await expect(evitement).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("main#contenu")).toBeFocused();
});

