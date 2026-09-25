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
  "/p/billing-api/workflow",
  "/login",
  "/admin",
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

/**
 * Le graphe de workflow se parcourt au clavier : Tab atteint les états, les flèches suivent
 * les transitions, et l'état sous le curseur est décrit (aria-live) sous la carte.
 */
test("graphe de workflow : parcours au clavier et description de l'état", async ({ page }) => {
  await page.goto("/p/billing-api/workflow");
  const graph = page.getByTestId("workflow-graph");
  await expect(graph.locator(".react-flow__node")).toHaveCount(3);
  const focus = page.getByTestId("workflow-graph-focus");
  await expect(focus).toContainText("Tab reaches the states");

  await graph.locator(".react-flow__node").first().focus();
  await expect(focus).toHaveText("À trier (agent lane): → Prêt (by refiner).");

  await page.keyboard.press("ArrowRight");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("data-id", "ready");
  await expect(focus).toHaveText("Prêt (agent lane): → Fini (by dev, gates scope_respected).");

  await page.keyboard.press("ArrowRight");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("data-id", "done");
  await expect(focus).toHaveText("Fini (terminal lane, terminal): no outgoing transition.");

  // Au bout du workflow, → reste sur place ; Début revient au premier état.
  await page.keyboard.press("ArrowRight");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("data-id", "done");
  await page.keyboard.press("Home");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("data-id", "inbox");
  await expect(graph.locator(".react-flow__node:focus")).toHaveAttribute("aria-label", /À trier, agent lane, 1 outgoing transition/);
});
