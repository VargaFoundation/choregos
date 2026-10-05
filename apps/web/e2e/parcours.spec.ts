import { expect, test } from "@playwright/test";

/** Les cinq parcours clés du plan (§3.4), joués sur les fixtures du mode démo. */

test("liste des projets et accès à un projet", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "projects" })).toBeVisible();
  await expect(page.getByText("Billing API")).toBeVisible();
  // La console dit sur quelle édition elle tourne (ADR 0024).
  await expect(page.getByTestId("edition-badge")).toHaveText("community edition");
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
  await expect(page.getByText(/demo mode: the session/)).toBeVisible();
});

test("administration : membres et jetons ont un écran", async ({ page }) => {
  await page.goto("/admin");
  await expect(page.getByText(/members of/)).toBeVisible();
  await expect(page.getByRole("button", { name: /mint a token/ })).toBeVisible();
});

test("integrations : un jeton pour Claude Code, glissé dans l'extrait", async ({ page }) => {
  await page.goto("/integrations");
  await expect(page.getByRole("heading", { name: "integrations" })).toBeVisible();
  await expect(page.getByTestId("mcp-url")).toHaveText("http://localhost:3000/mcp");
  await expect(page.getByTestId("snippet")).toContainText("claude mcp add --transport http choregos");
  await page.getByRole("button", { name: "create a token for Claude Code" }).click();
  await expect(page.getByTestId("snippet")).toContainText("chg_demo_jeton_affiche_une_fois");
  await expect(page.getByTestId("connection-status")).toBeVisible();
});

test("integrations d'un projet : la porte du projet, et claude.ai dit pourquoi il ne la joint pas", async ({ page }) => {
  await page.goto("/p/billing-api/integrations/cursor");
  await expect(page.getByTestId("mcp-url")).toHaveText("http://localhost:3000/mcp/projects/varga:billing-api");
  await page.getByRole("link", { name: "claude.ai" }).click();
  await expect(page.getByText("not reachable from here yet")).toBeVisible();
});

test("propositions : la décision se prend ici, un rejet exige un motif", async ({ page }) => {
  await page.goto("/p/billing-api/proposals");
  await expect(page.getByRole("link", { name: "open_infra_pr" })).toBeVisible();
  await page.getByRole("link", { name: "open_infra_pr" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/proposals\/pr1$/);
  await expect(page.getByText("you may be asked to sign in again")).toBeVisible();
  await expect(page.getByRole("button", { name: "reject" })).toBeDisabled();
  await page.getByLabel("reason").fill("la fenêtre de maintenance est gelée");
  await expect(page.getByRole("button", { name: "reject" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "approve" })).toBeEnabled();
});

test("workflows : un projet en porte plusieurs, chacun se lit en processus et en carte", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  await expect(page.getByTestId("workflow-card-default-simple")).toContainText("whatever no rule claims");
  await expect(page.getByTestId("workflow-card-hotfix")).toContainText("labelled incident");
  await page.getByRole("link", { name: "default-simple" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/workflows\/default-simple$/);
  await expect(page.getByTestId("process-step-t-implement")).toContainText("le diff reste dans le périmètre permis");
  await page.getByRole("link", { name: "map" }).click();
  // Les défauts partent de chaque état d'agent : la légende les dit une fois, la carte ne les dessine pas.
  await expect(page.getByTestId("workflow-defaults").getByRole("listitem")).toHaveCount(2);
  await expect(page.getByTestId("workflow-graph").locator(".react-flow__edge")).toHaveCount(2);
});

test("l'ancienne page du workflow mène à la liste", async ({ page }) => {
  await page.goto("/p/billing-api/workflow");
  await expect(page).toHaveURL(/\/p\/billing-api\/workflows$/);
});

test("historique d'un workflow : deux versions se comparent, une ancienne se republie", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/history");
  await expect(page.getByRole("table", { name: "versions of default-simple" }).getByRole("row")).toHaveCount(3);
  // Par défaut, la version d'avant contre l'active : la garantie ajoutée en v2.
  await expect(page.getByTestId("workflow-diff")).toContainText("gates: [scope_respected, ci_green]");
  await page.getByRole("button", { name: "restore v1" }).click();
  await expect(page.getByRole("button", { name: "republish v1" })).toBeVisible();
});
