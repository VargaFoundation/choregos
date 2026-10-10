import { expect, test } from "@playwright/test";

/**
 * Ce que fait l'agent, lisible (S25) : son plan, tel qu'il le publie en ACP, révision après révision.
 */
test("le plan de l'agent se lit sur la page d'un run : progression, tâches, retraits (S25-01)", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r3");
  const plan = page.getByRole("list", { name: "agent plan" });
  // Trois tâches dans le plan courant, et deux retirées en chemin : une reformulée, une abandonnée.
  await expect(plan.getByRole("listitem")).toHaveCount(5);
  await expect(page.getByTestId("plan-progression")).toHaveText("2 of 3 done");
  await expect(plan.locator('[data-statut="removed"]')).toHaveCount(2);
  await expect(plan.locator('[data-statut="removed"]').first()).toContainText("removed in revision 3");
  // Le journal brut, lui, dit ce que contient chaque révision, sans JSON.
  await expect(page.getByTestId("live-log")).toContainText("plan · 2 of 3 done");
});
