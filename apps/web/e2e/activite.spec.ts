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

test("l'activité d'un run se lit au-dessus du journal : chaque appel une fois, le refus sur l'appel bloqué (S25-02)", async ({
  page,
}) => {
  await page.goto("/p/billing-api/runs/r3");
  await expect(page.getByTestId("activite-resume")).toHaveText("5 tool calls · 5 finished · 1 refused · 1 sub-agent");
  const appels = page.getByRole("table", { name: "tool calls" });
  await expect(appels.locator("tbody tr")).toHaveCount(5);
  const bloque = appels.locator('tr[data-appel="tc-4"]');
  await expect(bloque).toContainText("Edit src/billing/rates.py");
  await expect(bloque).toContainText("denied");
  await expect(bloque).toContainText("failed");
  // L'activité est AU-DESSUS du journal brut.
  const activite = await page.getByTestId("activite-du-run").boundingBox();
  const journal = await page.getByTestId("live-log").boundingBox();
  expect(activite!.y).toBeLessThan(journal!.y);
});
