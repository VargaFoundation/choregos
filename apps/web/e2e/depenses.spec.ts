import { expect, test } from "@playwright/test";

/**
 * L'écran des dépenses (S29-03), sur le mode démo : les chiffres affichés sont ceux que le registre
 * de démonstration additionne, rangement par rangement — le même registre sert chaque regroupement.
 */

test("la dépense du mois, sa prévision et son budget se lisent", async ({ page }) => {
  await page.goto("/costs");
  await expect(page.getByRole("heading", { name: "Spend", level: 1 })).toBeVisible();
  const mois = page.getByTestId("mois");
  await expect(mois).toContainText("Forecast at month end (linear)");
  await expect(mois).toContainText("Budget: daily budget × days in the month");
  await expect(mois).toContainText("€"); // la monnaie de l'organisation : l'euro par défaut
  await expect(page.getByTestId("budget-etat")).toBeVisible();
});

test("les plus coûteux : agents, workflows, tickets ; les modèles et les outils à part ; un CSV par rangement", async ({
  page,
}) => {
  await page.goto("/costs");
  await expect(page.getByRole("table", { name: "Top agents" }).getByRole("row").first()).toContainText("coordinator");
  await expect(page.getByRole("table", { name: "Top workflows" })).toContainText("delivery");
  await expect(page.getByRole("table", { name: "Top work items" })).toContainText("BILL-12");
  await expect(page.getByRole("table", { name: "By tool" })).toContainText("crm_export");
  await expect(page.getByRole("table", { name: "By tool" })).not.toContainText("claude-sonnet");
  await expect(page.getByRole("table", { name: "By model" })).toContainText("claude-sonnet");
  const csv = page.getByRole("link", { name: "CSV", exact: true });
  expect(await csv.count()).toBeGreaterThanOrEqual(7);
  const hrefs = await csv.evaluateAll((liens) => liens.map((l) => l.getAttribute("href") ?? ""));
  expect(hrefs.some((h) => h.includes("group_by=agent"))).toBe(true);
  expect(hrefs.some((h) => h.includes("group_by=model") && h.includes("kind=tool"))).toBe(true);
});

test("un projet : la même page, resserrée, et le lien depuis sa vue d'ensemble", async ({ page }) => {
  await page.goto("/p/billing-api");
  await page.getByRole("link", { name: "Spend by agent, workflow, work item" }).click();
  await expect(page).toHaveURL(/\/costs\?project=billing-api/);
  await expect(page.getByRole("combobox", { name: "Scope" })).toHaveValue("billing-api");
  await expect(page.getByRole("table", { name: "Top workflows" })).not.toContainText("onboarding");
  await expect(page.getByRole("table", { name: "Projects" })).toHaveCount(0);
});

test("changer de période change le total", async ({ page }) => {
  await page.goto("/costs");
  const total = page.getByTestId("total-periode");
  await expect(total).toContainText("Total €");
  const avant = await total.textContent();
  await page.getByRole("combobox", { name: "Period" }).selectOption("90");
  await expect(total).not.toHaveText(avant ?? "");
});

test("l'entrée « spend » est dans la navigation", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.getByRole("complementary", { name: "Sidebar" }).getByRole("link", { name: "Spend" }).click();
  await expect(page).toHaveURL(/\/costs$/);
});
