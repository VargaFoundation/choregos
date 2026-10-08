import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Accessibilité mesurée, pas déclarée : axe passe sur les écrans principaux en mode démo.
 * Seules les violations « serious » et « critical » bloquent — les autres sont listées
 * dans le rapport, à corriger, pas à ignorer.
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
  "/approvals",
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
];

for (const path of PAGES) {
  test(`${path} sans violation sérieuse`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    const bloquantes = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(
      bloquantes.map((v) => `${v.id} (${v.impact}) : ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
    ).toEqual([]);
  });
}

/** La carte en cours d'édition (S16-12) : le panneau d'un état et le brouillon passent axe aussi. */
test("carte en cours d'édition sans violation sérieuse", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/map");
  await page.getByTestId("workflow-graph").locator('.react-flow__node[data-id="inbox"]').click();
  const panneau = page.getByTestId("panneau-etat");
  await panneau.getByLabel("label").fill("Nouvelles demandes");
  await panneau.getByRole("button", { name: "set label" }).click();
  await expect(page.getByTestId("brouillon")).toContainText("1 change not published yet");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bloquantes = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
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
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const bloquantes = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(
    bloquantes.map((v) => `${v.id} (${v.impact}) : ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
  ).toEqual([]);
});

/**
 * Le graphe de workflow se parcourt au clavier : Tab atteint les états, les flèches suivent
 * les transitions, et l'état sous le curseur est décrit (aria-live) sous la carte.
 */
test("graphe de workflow : parcours au clavier et description de l'état", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/map");
  const graph = page.getByTestId("workflow-graph");
  await expect(graph.locator(".react-flow__node")).toHaveCount(4);
  const focus = page.getByTestId("workflow-graph-focus");
  await expect(focus).toContainText("Tab reaches the states");

  await graph.locator(".react-flow__node").first().focus();
  await expect(focus).toHaveText("To triage (agent lane): → Ready (by refiner).");

  await page.keyboard.press("ArrowRight");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("data-id", "ready");
  await expect(focus).toHaveText(
    "Ready (agent lane): → Done (by dev, gates scope_respected); → Needs a human on retries exhausted.",
  );
  // Au clavier comme au survol : les escalades de l'état parcouru se montrent, puis se replient.
  await expect(graph.locator(".react-flow__edge")).toHaveCount(3);

  await page.keyboard.press("ArrowRight");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("data-id", "done");
  await expect(focus).toHaveText("Done (terminal lane, terminal): no outgoing transition.");
  await expect(graph.locator(".react-flow__edge")).toHaveCount(2);

  // Au bout du workflow, → reste sur place ; Début revient au premier état.
  await page.keyboard.press("ArrowRight");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("data-id", "done");
  await page.keyboard.press("Home");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("data-id", "inbox");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("aria-label", /To triage, agent lane, 1 outgoing transition/);

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

/** L'en-tête tient sur une ligne à 1280 px (S21-03) : six entrées, l'organisation, la session. */
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
      // L'en-tête a une hauteur fixe : un libellé qui passe à la ligne déborde DEDANS sans la changer.
      // Ce qui se mesure, c'est chaque élément de la navigation : une ligne de texte, pas trois (la
      // marque, elle, est dessinée sur deux lignes).
      hautes: [...document.querySelectorAll('nav[aria-label="main navigation"] :is(a, button, span, select)')]
        .filter((el) => el.getBoundingClientRect().height > 30)
        .map((el) => (el.textContent ?? "").trim()),
    }));
    expect(mesure.largeur, path).toBeLessThanOrEqual(mesure.fenetre);
    expect(mesure.hautes, path).toEqual([]);
  }
});
