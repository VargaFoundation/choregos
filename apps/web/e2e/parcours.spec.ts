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
  // Le titre est le nom du projet, le slug au-dessus (S23-04).
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Billing API");
  await expect(page.getByText("project · billing-api")).toBeVisible();
});

test("board : une colonne d'un état que le workflow ne connaît pas le dit (S23-04)", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  // Les tickets de démonstration sont dans des états qu'ignore la carte de default-simple.
  await expect(page.getByText("not a state of this workflow").first()).toBeVisible();
});

test("board : colonnes du workflow et décision humaine", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  await expect(page.getByRole("heading", { name: "Board" })).toBeVisible();
  await expect(page.getByText("Credit notes are not deducted from the total")).toBeVisible();
  await expect(page.getByRole("button", { name: "approve" }).first()).toBeVisible();
});

test("ticket : coûts par étape et timeline", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  await expect(page.getByText("cost per stage")).toBeVisible();
  await expect(page.getByText("Timeline")).toBeVisible();
});

test("ticket : le parcours se suit en direct, se rejoue, et chaque agent montre ce qu'il a fait (S22-02)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/items/w1");
  const parcours = page.getByTestId("parcours");
  await expect(parcours.getByTestId("parcours-maintenant")).toContainText("now in Reviewed by an agent");
  await expect(parcours.getByTestId("etape-t-security-review")).toHaveAttribute("data-statut", "en_cours");
  await expect(parcours.getByTestId("tour-t-test")).toHaveText("passed in round 3");
  // La carte tient dans la page : elle s'allonge, elle ne déborde pas.
  const debord = await page.evaluate(() => {
    const carte = document.querySelector('[data-testid="parcours"]')!.getBoundingClientRect();
    return { droite: carte.right, fenetre: document.documentElement.clientWidth };
  });
  expect(debord.droite).toBeLessThanOrEqual(debord.fenetre);

  await parcours.getByRole("button", { name: "2×" }).click();
  await parcours.getByRole("button", { name: "Replay the journey" }).click();
  await expect(parcours.getByTestId("parcours-moment")).toContainText("/ 30");
  await expect(parcours.getByTestId("etape-t-triage")).toHaveAttribute("data-statut", "fait", { timeout: 5000 });
  await parcours.getByRole("button", { name: "Pause the replay" }).click();
  await parcours.getByRole("navigation", { name: "stages of the journey" }).getByRole("button", { name: /In progress/ }).click();
  await expect(parcours.getByTestId("etape-t-implement")).toHaveAttribute("data-statut", "en_cours");
  await parcours.getByRole("button", { name: /^Live/ }).click();

  await parcours.getByTestId("etape-t-test").click();
  const panneau = page.getByTestId("panneau-parcours");
  await expect(panneau).toBeVisible();
  await panneau.getByTestId("tentative-r-test-1").click();
  await expect(panneau).toContainText("2 tests fail: rounding of partial credit notes.");
  await expect(panneau.getByTestId("ce-qu-il-a-fait")).toContainText("src/billing/rates.py");
  await panneau.getByRole("tab", { name: "Log" }).click();
  await expect(panneau.getByTestId("live-log")).toContainText("session/request_permission");
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
  await expect(page.getByText("N+1 query when loading order lines")).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "connect a client" })).toBeVisible();
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
  await page.getByLabel("reason to reject").fill("the maintenance window is frozen");
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
  await expect(page.getByTestId("process-step-t-implement")).toContainText("the agent stayed within its allowed paths");
  await page.getByRole("link", { name: "map" }).click();
  // La carte s'ouvre en serpentin (S22-05) ; la liste à plat est à un onglet.
  await expect(page.getByTestId("carte-en-serpentin")).toBeVisible();
  await page.getByRole("button", { name: "list" }).click();
  // Les défauts partent de chaque état d'agent : la légende les dit une fois, la carte ne les dessine pas.
  await expect(page.getByTestId("workflow-defaults").getByRole("listitem")).toHaveCount(2);
  // Le chemin nominal se lit seul : une escalade se montre autour de son état, ou toutes sur demande.
  const carte = page.getByTestId("workflow-graph");
  await expect(carte.getByRole("list", { name: "nominal path" }).locator(":scope > li")).toHaveCount(3);
  await expect(carte.getByTestId("secondaires-ready")).toHaveCount(0);
  await carte.getByTestId("etat-ready").hover();
  await expect(carte.getByTestId("secondaires-ready")).toContainText("when retries run out → Needs a human");
  await page.mouse.move(0, 0);
  await expect(carte.getByTestId("secondaires-ready")).toHaveCount(0);
  await page.getByLabel(/show every retry, rejection and escalation/).check();
  await expect(carte.getByTestId("secondaires-ready")).toBeVisible();
});

test("workflows : un libellé changé sur la carte se lit dans la vue processus, puis se publie", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/map?vue=list");
  const carte = page.getByTestId("workflow-graph");
  await carte.getByTestId("etat-inbox").click();
  const panneau = page.getByTestId("panneau-etat");
  await panneau.getByLabel("label").fill("Nouvelles demandes");
  await panneau.getByRole("button", { name: "set label" }).click();

  // Rien n'est publié : le brouillon dit le geste, son diff, et la carte se redessine depuis lui.
  const brouillon = page.getByTestId("brouillon");
  await expect(brouillon).toContainText("1 change not published yet");
  await brouillon.getByText("last change").click();
  await expect(page.getByTestId("dernier-diff")).toContainText("+  inbox: { display: Nouvelles demandes, kind: wait }");
  await expect(carte.getByTestId("etat-inbox")).toContainText("Nouvelles demandes");

  // La vue processus lit le même brouillon ; publier crée la version suivante de celle qui a été lue.
  await page.getByRole("link", { name: "process" }).click();
  await expect(page.getByTestId("process-step-t-refine")).toContainText("Nouvelles demandes");
  await brouillon.getByRole("button", { name: "publish v2" }).click();
  await expect(brouillon.getByRole("status")).toHaveText("default-simple v2 published — running items finish on their version");
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
  await expect(page.getByText("Credit notes are not deducted from the total")).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "agents", exact: true })).toBeVisible();
  await expect(page.getByTestId("agent-onboarding-coordinator")).toContainText("Onboarding coordinator");
  await expect(page.getByTestId("agent-onboarding-coordinator")).toContainText("1 skill");
  // Le jeton du Claude Code de Léa a appelé la porte : connecté, et rattaché à son agent externe.
  const claude = page.getByTestId("client-tok-claude");
  await expect(claude).toContainText("connected");
  await expect(claude).toContainText("claude-code/2.1.0");
  await expect(claude.getByRole("link", { name: "acts as Léa's Claude Code" })).toBeVisible();
  // Un client jamais appelé ne se dit pas connecté, et s'enregistre.
  await expect(page.getByTestId("client-tok-cursor")).toContainText("never called");
  await expect(page.getByTestId("client-tok-cursor").getByRole("button", { name: "register as an external agent" })).toBeVisible();

  await page.getByRole("link", { name: "Onboarding coordinator" }).click();
  await expect(page.getByTestId("mesures")).toContainText("14 (12 succeeded, 2 failed)");
  await expect(page.getByTestId("version")).toContainText("onboarding-procedure@1");
  await page.getByRole("link", { name: "onboarding-procedure@1" }).click();
  await expect(page.getByTestId("fichiers")).toContainText("Entra accounts ten days before the start date");
});

test("agents : un agent externe montre son client, et un projet ses agents implicites", async ({ page }) => {
  await page.goto("/agents/leas-claude-code");
  await expect(page.getByTestId("clients-de-l-agent")).toContainText("claude-code/2.1.0");
  await page.goto("/p/billing-api/agents");
  await expect(page.getByTestId("epingles")).toContainText("onboarding-coordinator");
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
  await expect(operations).toContainText("create_account");
  await expect(page.getByLabel("policy of create_account")).toHaveValue("approval");
  await expect(page.getByLabel("project groups of create_account")).toHaveValue("hr");

  await page.goto("/p/billing-api/settings");
  const projet = page.getByTestId("operations-du-projet");
  await expect(projet).toContainText("read_user");
  // `create_account` est `approval` dans l'organisation : le choix ne propose pas `allowed`.
  const choix = page.getByLabel("this project's policy for create_account");
  await expect(choix.locator("option")).toHaveText(["as the organisation", "approval", "forbidden"]);
});

test("connecteurs : systèmes métier, serveurs MCP et outils de livraison des projets, chacun dans sa section", async ({
  page,
}) => {
  await page.goto("/admin/connectors");
  await expect(page.getByTestId("glossaire")).toHaveAttribute("open", "");
  await expect(page.getByTestId("section-metier")).toContainText("entra-acme");
  await expect(page.getByTestId("section-metier")).toContainText("directory (identity)");
  await expect(page.getByTestId("section-mcp")).toContainText("supplier-agent");
  await expect(page.getByTestId("section-metier")).not.toContainText("supplier-agent");
  await expect(page.getByTestId("livraison-checkout-web")).toContainText("work tracker: jira");
  await expect(page.getByTestId("livraison-checkout-web")).toContainText("deployment: argocd");
  await page.getByTestId("livraison-checkout-web").getByRole("link", { name: "settings" }).click();
  await expect(page).toHaveURL(/\/p\/checkout-web\/settings$/);
});

test("un serveur MCP de l'organisation : découvrir dit ce qui naît fermé", async ({ page }) => {
  await page.goto("/admin/connectors");
  await expect(page.getByLabel("policy of order_laptop")).toHaveValue("forbidden");
  await page.getByRole("button", { name: "discover" }).click();
  await expect(page.getByRole("status")).toHaveText(
    "1 new (closed until you open them), 1 changed their schema (closed again)",
  );
});

test("la boîte des décisions : une action proposée par un agent attend une personne", async ({ page }) => {
  // L'ancienne adresse mène à l'inbox (S23-02).
  await page.goto("/approvals");
  await expect(page).toHaveURL(/\/inbox$/);
  const boite = page.getByTestId("boite");
  await expect(boite).toContainText("Order Léa's laptop");
  await expect(boite).toContainText("proposed by the agent onboarding-coordinator");
  await boite.getByRole("link", { name: /Order Léa's laptop/ }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/actions\/act-poste$/);
  await expect(page.getByRole("button", { name: "reject" })).toBeDisabled();
  await page.getByRole("button", { name: "approve" }).click();
  // Une action déjà décidée montre ses décisions et le journal de ses effets, clé par clé.
  await page.goto("/p/billing-api/actions/act-comptes");
  await expect(page.getByTestId("decisions")).toContainText("approve by lea@varga.dev");
  await expect(page.getByTestId("effet-1")).toContainText("key act-comptes:1 · 2 attempt(s)");
});

test("agents : le catalogue s'installe, et Claude Code se connecte en un clic", async ({ page }) => {
  await page.goto("/agents");
  const catalogue = page.getByTestId("catalogue");
  await expect(catalogue.getByTestId("catalogue-developer")).toContainText("not installed");
  await expect(catalogue.getByTestId("catalogue-reviewer")).toContainText("an update is available");
  await catalogue.getByTestId("catalogue-developer").getByRole("button", { name: "install" }).click();
  const claude = page.getByTestId("client-catalogue-claude-code");
  await claude.getByRole("button", { name: "connect" }).click();
  await expect(page.getByTestId("extrait-claude-code")).toContainText("chg_demo_shown_once");
  await expect(page.getByTestId("client-catalogue-chatgpt")).toContainText("not available here");
});

test("inbox : un ticket arrêté sur une demande humaine s'y décide, et l'en-tête le compte (S23-02)", async ({ page }) => {
  await page.goto("/");
  // Un ticket (Spec to approve) et deux actions attendent quelqu'un.
  await expect(page.getByRole("link", { name: /^inbox/ })).toContainText("3");
  await page.getByRole("link", { name: /^inbox/ }).click();
  await expect(page).toHaveURL(/\/inbox$/);
  const ticket = page.getByTestId("attente-w2");
  await expect(ticket).toContainText("Export invoices as PDF");
  await expect(ticket).toContainText("Approval of the specification requested");
  await expect(ticket).toContainText("billing-api · varga/billing-api#124 · Spec to approve · asked");
  await expect(ticket).toContainText("due in 21 hours");
  await ticket.getByRole("button", { name: "approve" }).click();
  await expect(ticket.getByRole("alert")).toHaveCount(0);
  await ticket.getByRole("link", { name: "Export invoices as PDF" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/items\/w2$/);
});

test("mise en route : un projet neuf dit ce qui lui manque ; un projet qui tourne n'en dit rien (S23-03)", async ({ page }) => {
  await page.goto("/p/checkout-web");
  const liste = page.getByTestId("mise-en-route");
  await expect(liste).toBeVisible();
  await expect(page.getByTestId("etape-provisioning")).toContainText("provisioning is running");
  await expect(page.getByTestId("etape-connecteur-cd")).toContainText("argocd: its last test failed");
  await expect(page.getByTestId("etape-premier-ticket")).toContainText("(to do)");
  await page.getByTestId("etape-connecteur-cd").getByRole("link", { name: "settings" }).click();
  await expect(page).toHaveURL(/\/p\/checkout-web\/settings$/);

  await page.goto("/p/billing-api");
  await expect(page.getByText("prs merged on first pass")).toBeVisible();
  await expect(page.getByTestId("mise-en-route")).toHaveCount(0);
});


test("board : ce qui attend une personne se dit en tête, et y mène même hors champ (S23-09)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/p/billing-api/board");
  const attentes = page.getByTestId("attentes-du-board");
  await expect(attentes).toContainText("1 ticket waits for a person");
  const carte = page.locator("#carte-w2");
  // Sans le bandeau, la carte était dans une colonne hors champ, sans indice.
  await expect(carte).not.toBeInViewport();
  await attentes.getByRole("link").first().click();
  await expect(carte).toBeInViewport();
  await expect(carte.getByRole("button", { name: "approve" })).toBeVisible();
  // Le sommaire nomme chaque colonne, même hors champ, et dit où l'on attend.
  const sommaire = page.getByRole("navigation", { name: "columns of the board" });
  await expect(sommaire.getByRole("link")).toHaveCount(await page.locator("section[id^=colonne-]").count());
  await expect(sommaire).toContainText("1 waiting");
});

test("coûts : un écran, une devise, et un graphe qui se lit (S23-10)", async ({ page }) => {
  for (const chemin of ["/p/billing-api", "/p/billing-api/items/w1", "/p/billing-api/board"]) {
    await page.goto(chemin);
    await page.waitForLoadState("networkidle");
    // L'écran d'un ticket mêlait le total en € et ses runs en US$ ; la vue d'ensemble convertissait à 0,92.
    await expect(page.locator("main")).not.toContainText("€");
  }
  await page.goto("/p/billing-api");
  const graphe = page.getByTestId("graphe-des-couts");
  await expect(graphe.getByRole("img")).toHaveAttribute("aria-label", /^cost per day, 14 days: highest US\$/);
  await expect(page.getByText("daily budget")).toBeVisible();
  await graphe.getByText("as a table").click();
  await expect(graphe.getByRole("table", { name: "cost per day" }).getByRole("row")).toHaveCount(15);
});

test("repérage : un ticket et un run ont leur fil d'Ariane, et l'onglet board reste actif (S23-12)", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  const onglets = page.getByRole("navigation", { name: "project sections" });
  await expect(onglets.getByRole("link", { name: "board" })).toHaveAttribute("aria-current", "page");
  const fil = page.getByRole("navigation", { name: "breadcrumb" });
  await expect(fil.getByRole("link", { name: "board" })).toHaveAttribute("href", "/p/billing-api/board");

  await page.goto("/p/billing-api/runs/r3");
  await expect(onglets.getByRole("link", { name: "board" })).toHaveAttribute("aria-current", "page");
  await expect(fil.getByRole("link")).toHaveCount(2);
  await expect(fil.locator("[aria-current=page]")).toContainText("attempt");

  await page.goto("/p/billing-api/actions/act-poste");
  await expect(onglets.getByRole("link", { name: "actions" })).toHaveAttribute("aria-current", "page");
});

test("repérage : la légende du parcours distingue « in progress » et « done » autrement que par la couleur (S23-12)", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  const legende = page.getByRole("list", { name: "legend" });
  const pastille = (libelle: string) =>
    legende.getByRole("listitem").filter({ hasText: libelle }).locator("span[aria-hidden]").first();
  const enCours = await pastille("in progress").evaluate((e) => getComputedStyle(e).borderTopColor);
  const fait = await pastille("done").evaluate((e) => getComputedStyle(e).borderTopColor);
  // Ce qui vit porte un anneau ; ce qui est fait n'en porte pas.
  expect(enCours).not.toBe("rgba(0, 0, 0, 0)");
  expect(fait).toBe("rgba(0, 0, 0, 0)");
});
