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
  await expect(page.getByRole("button", { name: /mint a token/ })).toBeVisible();
  await page.getByRole("link", { name: "members" }).click();
  await expect(page.getByText(/members of/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^remove / }).first()).toBeVisible();
});

test("administration : une section déclarée par un greffon se rend, et son secret ne se montre qu'une fois", async ({
  page,
}) => {
  await page.goto("/admin");
  await page.getByRole("link", { name: "SCIM provisioning" }).click();
  await expect(page).toHaveURL(/\/admin\/x\/scim$/);
  const section = page.getByTestId("admin-section-scim");
  await expect(section.getByLabel(/accept SCIM requests/)).toBeChecked();
  await expect(section.getByRole("table", { name: "tokens" })).toContainText("okta");
  await section.getByRole("button", { name: "revoke" }).click();
  await expect(section.getByText("The identity provider stops provisioning with this token.")).toBeVisible();
  await section.getByRole("button", { name: "cancel" }).click();
  await section.getByLabel(/^name/).fill("okta");
  await section.getByRole("button", { name: "create a token" }).click();
  await expect(page.getByTestId("secret-once").getByRole("textbox")).toHaveValue("scim_demo_shown_once");
  await page.getByRole("button", { name: "I have copied it" }).click();
  await expect(page.getByTestId("secret-once")).toHaveCount(0);
});

test("administration : en communautaire, ce que l'édition entreprise ajoute, sans faux écran", async ({ page }) => {
  await page.goto("/admin/edition");
  await expect(page.getByText(/runs the community core/)).toBeVisible();
  await expect(page.getByRole("list", { name: "what the enterprise edition adds" })).toContainText(
    "SCIM provisioning of users and groups",
  );
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

test("une proposition de l'ontologie est une action : un ancien lien y mène, qui dit ce qu'elle touche", async ({
  page,
}) => {
  // Un lien de décision émis avant S20-10 (porte MCP, courriel) : même identifiant, page des actions.
  await page.goto("/p/billing-api/proposals/pr1");
  await expect(page).toHaveURL(/\/p\/billing-api\/actions\/pr1$/);
  const ontologie = page.getByTestId("ontologie");
  await expect(ontologie).toContainText("open_infra_pr");
  await expect(ontologie).toContainText("os-reboot-required");
  await expect(page.getByTestId("regles-de-decision")).toContainText("within 10 min");
  await expect(page.getByRole("button", { name: "reject" })).toBeDisabled();
  await page.getByLabel("reason to reject").fill("la fenêtre de maintenance est gelée");
  await expect(page.getByRole("button", { name: "reject" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "approve" })).toBeEnabled();
  await page.goto("/p/billing-api/proposals");
  await expect(page).toHaveURL(/\/p\/billing-api\/actions$/);
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
  // Le chemin nominal se lit seul : une escalade se montre autour de son état, ou toutes sur demande.
  const carte = page.getByTestId("workflow-graph");
  await expect(carte.locator(".react-flow__edge")).toHaveCount(2);
  await carte.locator('.react-flow__node[data-id="ready"]').hover();
  await expect(carte.locator(".react-flow__edge")).toHaveCount(3);
  await page.mouse.move(0, 0);
  await expect(carte.locator(".react-flow__edge")).toHaveCount(2);
  await page.getByLabel(/show every retry, rejection and escalation/).check();
  await expect(carte.locator(".react-flow__edge")).toHaveCount(3);
});

test("workflows : un libellé changé sur la carte se lit dans la vue processus, puis se publie", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/map");
  const carte = page.getByTestId("workflow-graph");
  await carte.locator('.react-flow__node[data-id="inbox"]').click();
  const panneau = page.getByTestId("panneau-etat");
  await panneau.getByLabel("label").fill("Nouvelles demandes");
  await panneau.getByRole("button", { name: "set label" }).click();

  // Rien n'est publié : le brouillon dit le geste, son diff, et la carte se redessine depuis lui.
  const brouillon = page.getByTestId("brouillon");
  await expect(brouillon).toContainText("1 change not published yet");
  await brouillon.getByText("last change").click();
  await expect(page.getByTestId("dernier-diff")).toContainText("+  inbox: { display: Nouvelles demandes, kind: wait }");
  await expect(carte.locator('.react-flow__node[data-id="inbox"]')).toContainText("Nouvelles demandes");

  // La vue processus lit le même brouillon ; publier crée la version suivante de celle qui a été lue.
  await page.getByRole("link", { name: "process" }).click();
  await expect(page.getByTestId("process-step-t-refine")).toContainText("Nouvelles demandes");
  await brouillon.getByRole("button", { name: "publish v2" }).click();
  await expect(brouillon.getByRole("status")).toHaveText("default-simple v2 published");
});

test("workflows : la vue processus ouvre le panneau d'une étape, son délai prérempli", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple");
  await page.getByRole("button", { name: "edit t-refine" }).click();
  const panneau = page.getByTestId("panneau-transition");
  await expect(panneau).toBeVisible();
  // Le délai lu est prérempli : t-implement en a un, t-refine non.
  await expect(panneau.getByLabel("at most (hours, empty: no limit)")).toHaveValue("");
  await page.getByRole("button", { name: "close" }).click();
  await page.getByRole("button", { name: "edit t-implement" }).click();
  await expect(page.getByTestId("panneau-transition").getByLabel("at most (hours, empty: no limit)")).toHaveValue("72");
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

test("board par workflow : la demande choisit son workflow, et ses champs en viennent", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  await expect(page.getByText("Les avoirs ne sont pas déduits du total")).toBeVisible();
  await page.getByRole("button", { name: "new request" }).click();
  await page.getByLabel("workflow of the request").selectOption("hotfix");
  const champs = page.getByTestId("request-fields");
  await expect(champs.getByLabel("incident id (required)")).toBeVisible();
  await expect(champs.getByLabel("severity (required)")).toBeVisible();
  await expect(champs.getByLabel("detected on")).toHaveAttribute("type", "date");
  // Le board du flux d'incident : ses colonnes, pas celles du défaut.
  await page.getByLabel("board workflow").selectOption("hotfix");
  await expect(page).toHaveURL(/workflow=hotfix/);
});

test("agents : le registre, et un Claude Code connecté qui agit comme agent externe", async ({ page }) => {
  await page.goto("/agents");
  await expect(page.getByRole("heading", { name: "agents" })).toBeVisible();
  await expect(page.getByTestId("agent-coordinateur-onboarding")).toContainText("Coordinateur onboarding");
  await expect(page.getByTestId("agent-coordinateur-onboarding")).toContainText("1 skill");
  // Le jeton du Claude Code de Léa a appelé la porte : connecté, et rattaché à son agent externe.
  const claude = page.getByTestId("client-tok-claude");
  await expect(claude).toContainText("connected");
  await expect(claude).toContainText("claude-code/2.1.0");
  await expect(claude.getByRole("link", { name: "acts as Le Claude Code de Léa" })).toBeVisible();
  // Un client jamais appelé ne se dit pas connecté, et s'enregistre.
  await expect(page.getByTestId("client-tok-cursor")).toContainText("never called");
  await expect(page.getByTestId("client-tok-cursor").getByRole("button", { name: "register as an external agent" })).toBeVisible();

  await page.getByRole("link", { name: "Coordinateur onboarding" }).click();
  await expect(page.getByTestId("mesures")).toContainText("14 (12 succeeded, 2 failed)");
  await expect(page.getByTestId("version")).toContainText("procedure-onboarding@1");
  await page.getByRole("link", { name: "procedure-onboarding@1" }).click();
  await expect(page.getByTestId("fichiers")).toContainText("Comptes Entra à J-10");
});

test("agents : un agent externe montre son client, et un projet ses agents implicites", async ({ page }) => {
  await page.goto("/agents/claude-de-lea");
  await expect(page.getByTestId("clients-de-l-agent")).toContainText("claude-code/2.1.0");
  await page.goto("/p/billing-api/agents");
  await expect(page.getByTestId("epingles")).toContainText("coordinateur-onboarding");
  await expect(page.getByTestId("epingles")).toContainText("tightened");
  await expect(page.getByTestId("implicites")).toContainText("refiner");
  await expect(page.getByTestId("implicites")).toContainText("dev");
  await page.getByTestId("implicites").getByRole("button", { name: "register" }).first().click();
  await expect(page.getByTestId("implicites").getByRole("status")).toContainText("actors.refiner.agent: refiner");
});

test("réglages : les connecteurs que les workflows exigent, et pourquoi ; un secret en référence", async ({ page }) => {
  await page.goto("/p/billing-api/settings");
  const connecteurs = page.getByTestId("connecteurs");
  // Billing API livre du logiciel : un dépôt et une CI, chacun avec sa raison.
  await expect(page.getByTestId("connecteur-scm")).toContainText("platform default: github");
  await expect(page.getByTestId("connecteur-scm")).toContainText("the guarantee scope_respected on t-implement");
  // Rien n'exige de notification : la ligne n'existe pas tant qu'on ne l'ajoute pas.
  await expect(page.getByTestId("connecteur-notify")).toHaveCount(0);
  await page.getByLabel("add a connector").selectOption("notify");
  await page.getByRole("button", { name: "add", exact: true }).click();
  await expect(page.getByTestId("connecteur-notify")).toBeVisible();
  // Jira : le formulaire vient de son schéma, ses secrets se nomment par référence.
  await page.getByTestId("connecteur-tracker").getByRole("button", { name: "edit" }).click();
  await page.getByTestId("connecteur-tracker").getByRole("combobox").first().selectOption("jira");
  await page.getByLabel("api token").fill("s3cr3t");
  await expect(page.getByTestId("connecteur-tracker").getByRole("alert")).toContainText("a reference is expected");
  await page.getByLabel("api token").fill("env:JIRA_TOKEN");
  await expect(page.getByTestId("connecteur-tracker").getByRole("alert")).toHaveCount(0);
  await expect(connecteurs).toBeVisible();
});

test("connecteurs de l'organisation : chaque opération porte sa politique ; un projet ne fait que resserrer", async ({ page }) => {
  await page.goto("/admin/connectors");
  const operations = page.getByTestId("operations-entra-acme");
  await expect(operations).toContainText("creer_compte");
  await expect(page.getByLabel("policy of creer_compte")).toHaveValue("approval");
  await expect(page.getByLabel("project groups of creer_compte")).toHaveValue("rh");

  await page.goto("/p/billing-api/settings");
  const projet = page.getByTestId("operations-du-projet");
  await expect(projet).toContainText("lire_utilisateur");
  // `creer_compte` est `approval` dans l'organisation : le choix ne propose pas `allowed`.
  const choix = page.getByLabel("this project's policy for creer_compte");
  await expect(choix.locator("option")).toHaveText(["as the organisation", "approval", "forbidden"]);
});

test("un serveur MCP de l'organisation : découvrir dit ce qui naît fermé", async ({ page }) => {
  await page.goto("/admin/connectors");
  await expect(page.getByLabel("policy of commander_poste")).toHaveValue("forbidden");
  await page.getByRole("button", { name: "discover" }).click();
  await expect(page.getByRole("status")).toHaveText(
    "1 new (closed until you open them), 1 changed their schema (closed again)",
  );
});

test("la boîte des décisions : une action proposée par un agent attend une personne", async ({ page }) => {
  await page.goto("/approvals");
  const boite = page.getByTestId("boite");
  await expect(boite).toContainText("Commander le portable de Léa");
  await expect(boite).toContainText("proposed by the agent coordinateur-onboarding");
  await boite.getByRole("link", { name: /Commander le portable/ }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/actions\/act-poste$/);
  await expect(page.getByRole("button", { name: "reject" })).toBeDisabled();
  await page.getByRole("button", { name: "approve" }).click();
  // Une action déjà décidée montre ses décisions et le journal de ses effets, clé par clé.
  await page.goto("/p/billing-api/actions/act-comptes");
  await expect(page.getByTestId("decisions")).toContainText("approve by lea@varga.dev");
  await expect(page.getByTestId("effet-1")).toContainText("key act-comptes:1 · 2 attempt(s)");
});
