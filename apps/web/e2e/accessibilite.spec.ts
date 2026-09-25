import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Accessibilité mesurée, pas déclarée : axe passe sur les écrans principaux en mode démo.
 * Seules les violations « serious » et « critical » bloquent — les autres sont listées
 * dans le rapport, à corriger, pas à ignorer.
 */
const PAGES = ["/", "/p/billing-api", "/p/billing-api/board", "/p/billing-api/trains", "/login", "/admin"];

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
