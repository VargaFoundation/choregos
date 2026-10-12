import { expect, test } from "@playwright/test";

/**
 * Ce que fait l'agent, lisible (S25) : son plan, tel qu'il le publie en ACP, révision après révision.
 */
test("le plan de l'agent se lit sur la page d'un run : progression, tâches, retraits (S25-01)", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r3");
  const plan = page.getByRole("list", { name: "Agent plan" });
  // Trois tâches dans le plan courant, et deux retirées en chemin : une reformulée, une abandonnée.
  await expect(plan.getByRole("listitem")).toHaveCount(5);
  await expect(page.getByTestId("plan-progression")).toHaveText("2 of 3 done");
  await expect(plan.locator('[data-statut="removed"]')).toHaveCount(2);
  await expect(plan.locator('[data-statut="removed"]').first()).toContainText("removed in revision 3");
  // Le journal brut, lui, dit ce que contient chaque révision, sans JSON.
  await expect(page.getByTestId("live-log")).toContainText("Plan · 2 of 3 done");
});

test("l'activité d'un run se lit au-dessus du journal : chaque appel une fois, le refus sur l'appel bloqué (S25-02)", async ({
  page,
}) => {
  await page.goto("/p/billing-api/runs/r3");
  await expect(page.getByTestId("activite-resume")).toHaveText("5 tool calls · 5 finished · 1 refused · 1 sub-agent");
  const appels = page.getByRole("table", { name: "Tool calls" });
  await expect(appels.locator("tbody tr")).toHaveCount(5);
  const bloque = appels.locator('tr[data-appel="tc-4"]');
  await expect(bloque).toContainText("Edit src/billing/rates.py");
  await expect(bloque).toContainText("Denied");
  await expect(bloque).toContainText("Failed");
  // L'activité est AU-DESSUS du journal brut.
  const activite = await page.getByTestId("activite-du-run").boundingBox();
  const journal = await page.getByTestId("live-log").boundingBox();
  expect(activite!.y).toBeLessThan(journal!.y);
});

test("un run que l'agent dit fini mais dont une garantie échoue s'affiche « declared done, not observed » (S25-03)", async ({
  page,
}) => {
  await page.goto("/p/billing-api/runs/r3");
  await expect(page.getByTestId("fin-declaree")).toHaveText("declared done, not observed");
  // Chaque élément du résumé dit d'où il vient : le coût est constaté, le plan déclaré, la couverture
  // déclarée (le runner ne l'a pas mesurée), les tests constatés.
  await expect(page.locator("section", { has: page.getByRole("heading", { name: "Cost", exact: true }) }).locator("[data-provenance]")).toHaveText(/Observed/);
  await expect(page.locator("section", { has: page.getByRole("heading", { name: "Agent plan", exact: true }) }).locator("[data-provenance]")).toHaveText(/Declared/);
  const preuves = page.locator("section", { has: page.getByRole("heading", { name: "Evidence", exact: true }) });
  await expect(preuves.getByRole("listitem").filter({ hasText: "Coverage" }).locator("[data-provenance]")).toHaveText(/Declared/);
  await expect(preuves.getByRole("listitem").filter({ hasText: "Tests" }).locator("[data-provenance]")).toHaveText(/Observed/);
});

test("les reçus de passage d'un ticket : chaque sortie, qui l'a produite, qui l'a lue (S25-04)", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  const passages = page.getByRole("list", { name: "Hand-offs" });
  await expect(passages.locator(":scope > li")).toHaveCount(2);
  await expect(passages.locator('[data-sortie="spec_markdown"]')).toContainText("Produced by refine · attempt 1");
  await expect(passages.locator('[data-sortie="spec_markdown"]')).toContainText("Read by implement · attempt 1");
  // L'étape suivante a lu la révision exacte produite, pour la spec comme pour le plan.
  await expect(passages.locator('[data-lecture="same"]')).toHaveCount(2);
  await expect(page.getByTestId("modifiee-apres-coup")).toHaveCount(0);
});
