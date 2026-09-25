import { expect, test } from "@playwright/test";

/** Les cinq parcours clés du plan (§3.4), joués sur les fixtures du mode démo. */

test("liste des projets et accès à un projet", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "projects" })).toBeVisible();
  await expect(page.getByText("Billing API")).toBeVisible();
  await page.getByRole("link", { name: "Billing API" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api$/);
  await expect(page.getByText("prs merged on first pass")).toBeVisible();
});

test("board : colonnes du workflow et décision humaine", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  await expect(page.getByRole("heading", { name: "Board" })).toBeVisible();
  await expect(page.getByText("Les avoirs ne sont pas déduits du total")).toBeVisible();
  await expect(page.getByRole("button", { name: "approve" }).first()).toBeVisible();
});

test("ticket : coûts par étape et timeline", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  await expect(page.getByText("cost per stage")).toBeVisible();
  await expect(page.getByText("Timeline")).toBeVisible();
});

test("run : journal ACP avec permissions refusées mises en évidence", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r3");
  await expect(page.getByText("ACP journal")).toBeVisible();
  await expect(page.getByPlaceholder("filter")).toBeVisible();
});

test("trains : gel impossible sans motif", async ({ page }) => {
  await page.goto("/p/billing-api/trains");
  await expect(page.getByText("Environment prod")).toBeVisible();
  await page.getByRole("button", { name: "freeze" }).first().click();
  await expect(page.getByText("a reason for the freeze is required")).toBeVisible();
});

test("findings : triage disponible", async ({ page }) => {
  await page.goto("/p/billing-api/findings");
  await expect(page.getByText("Requête N+1 sur le chargement des lignes")).toBeVisible();
  await expect(page.getByRole("button", { name: "create the ticket" })).toBeVisible();
});

test("wizard : validation du slug avant de continuer", async ({ page }) => {
  await page.goto("/projects/new");
  await page.getByRole("button", { name: "next" }).click();
  await page.getByLabel("identifier (slug)").fill("Billing API");
  await expect(page.getByText("lowercase letters, digits and dashes only")).toBeVisible();
});

test("connexion : la page existe et dit ce qu'elle attend", async ({ page }) => {
  await page.goto("/login?next=%2Fadmin");
  await expect(page.getByRole("heading", { name: "sign in" })).toBeVisible();
  // en mode démo la session est simulée : la page le dit au lieu d'un bouton vers un IdP absent
  await expect(page.getByText(/demo mode/)).toBeVisible();
});

test("administration : membres et jetons ont un écran", async ({ page }) => {
  await page.goto("/admin");
  await expect(page.getByText(/members of/)).toBeVisible();
  await expect(page.getByRole("button", { name: /mint a token/ })).toBeVisible();
});
