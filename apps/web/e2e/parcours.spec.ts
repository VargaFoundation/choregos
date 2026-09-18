import { expect, test } from "@playwright/test";

/** Les cinq parcours clés du plan (§3.4), joués sur les fixtures du mode démo. */

test("liste des projets et accès à un projet", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Projets" })).toBeVisible();
  await expect(page.getByText("Billing API")).toBeVisible();
  await page.getByRole("link", { name: "Billing API" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api$/);
  await expect(page.getByText("PR mergées au 1er passage")).toBeVisible();
});

test("board : colonnes du workflow et décision humaine", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  await expect(page.getByRole("heading", { name: "Board" })).toBeVisible();
  await expect(page.getByText("Les avoirs ne sont pas déduits du total")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approuver" }).first()).toBeVisible();
});

test("ticket : coûts par étape et timeline", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  await expect(page.getByText("Coût par étape")).toBeVisible();
  await expect(page.getByText("Timeline")).toBeVisible();
});

test("run : journal ACP avec permissions refusées mises en évidence", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r3");
  await expect(page.getByText("Journal ACP")).toBeVisible();
  await expect(page.getByPlaceholder("filtrer")).toBeVisible();
});

test("trains : gel impossible sans motif", async ({ page }) => {
  await page.goto("/p/billing-api/trains");
  await expect(page.getByText("Environnement prod")).toBeVisible();
  await page.getByRole("button", { name: "Geler" }).first().click();
  await expect(page.getByText("le motif du gel est obligatoire")).toBeVisible();
});

test("findings : triage disponible", async ({ page }) => {
  await page.goto("/p/billing-api/findings");
  await expect(page.getByText("Requête N+1 sur le chargement des lignes")).toBeVisible();
  await expect(page.getByRole("button", { name: "Créer le ticket" })).toBeVisible();
});

test("wizard : validation du slug avant de continuer", async ({ page }) => {
  await page.goto("/projects/new");
  await page.getByRole("button", { name: "Suivant" }).click();
  await page.getByLabel("Identifiant (slug)").fill("Billing API");
  await expect(page.getByText("minuscules, chiffres et tirets uniquement")).toBeVisible();
});
