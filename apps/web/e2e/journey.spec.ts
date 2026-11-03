import { expect, test, type Locator, type Page } from "@playwright/test";

/** The plan's five key journeys (§3.4), played on the demo-mode fixtures. */

test("projects list and access to a project", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page.getByText("Billing API")).toBeVisible();
  // The console says which edition it runs on (ADR 0024).
  await expect(page.getByTestId("edition-badge")).toHaveText("Community edition");
  await page.getByRole("link", { name: "Billing API" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api$/);
  await expect(page.getByText("PRs merged on first pass")).toBeVisible();
  // The title is the project's name (S23-04); the trail starts at the project list (S30-03).
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Billing API");
  const fil = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(fil.getByRole("link", { name: "Projects" })).toHaveAttribute("href", "/");
  await expect(fil.locator("[aria-current=page]")).toHaveText("Billing API");
});

test("inside a project the sidebar lists its sections, and no tab row is left (S30-03)", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  const barre = page.getByRole("complementary", { name: "Sidebar" });
  for (const group of ["Work", "Define", "Configure"]) await expect(barre.getByRole("list", { name: group })).toBeVisible();
  await expect(barre.getByRole("link", { name: "Board" })).toHaveAttribute("aria-current", "page");
  await expect(barre.getByRole("link", { name: "Skills" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Project sections" })).toHaveCount(0);
  // The switcher goes to another project; the way back leads to the organisation's pages.
  await barre.getByTestId("project-switcher").locator("summary").click();
  await barre.getByRole("link", { name: "Checkout Web" }).click();
  await expect(page).toHaveURL(/\/p\/checkout-web$/);
  await barre.getByRole("link", { name: /All of/ }).click();
  await expect(barre.getByRole("link", { name: "Skills" })).toBeVisible();
  // The administration: its sections in the sidebar, no tab row.
  await page.goto("/admin/members");
  await expect(barre.getByRole("list", { name: "Administration" }).getByRole("link", { name: "Members" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("navigation", { name: "Administration sections" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Administration");
});

test("board: a column for a state the workflow does not know says so (S23-04)", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  // The demo work items are in states that default-simple's map ignores.
  await expect(page.getByText("Not a state of this workflow").first()).toBeVisible();
});

test("board: workflow columns and human decision", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  await expect(page.getByRole("heading", { name: "Board" })).toBeVisible();
  await expect(page.getByText("Credit notes are not deducted from the total")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" }).first()).toBeVisible();
});

test("work item: costs per step and timeline", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  await expect(page.getByText("Cost per stage")).toBeVisible();
  await expect(page.getByText("Timeline")).toBeVisible();
});

test("work item: the journey is followed live, replayed, and every agent shows what it did (S22-02)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/items/w1");
  const journey = page.getByTestId("parcours");
  await expect(journey.getByTestId("parcours-maintenant")).toContainText("Now in Reviewed by an agent");
  await expect(journey.getByTestId("etape-t-security-review")).toHaveAttribute("data-statut", "en_cours");
  await expect(journey.getByTestId("tour-t-test")).toHaveText("Passed in round 3");
  // The map fits in the page: it grows longer, it does not overflow.
  const debord = await page.evaluate(() => {
    const mapBox = document.querySelector('[data-testid="parcours"]')!.getBoundingClientRect();
    return { droite: mapBox.right, fenetre: document.documentElement.clientWidth };
  });
  expect(debord.droite).toBeLessThanOrEqual(debord.fenetre);

  await journey.getByRole("button", { name: "2×" }).click();
  await journey.getByRole("button", { name: "Replay the journey" }).click();
  await expect(journey.getByTestId("parcours-moment")).toContainText("/ 34");
  await expect(journey.getByTestId("etape-t-triage")).toHaveAttribute("data-statut", "fait", { timeout: 5000 });
  await journey.getByRole("button", { name: "Pause the replay" }).click();
  await journey.getByRole("navigation", { name: "Stages of the journey" }).getByRole("button", { name: /In progress/ }).click();
  await expect(journey.getByTestId("etape-t-implement")).toHaveAttribute("data-statut", "en_cours");
  await journey.getByRole("button", { name: /^Live/ }).click();

  await journey.getByTestId("etape-t-test").click();
  const panel = page.getByTestId("panneau-parcours");
  await expect(panel).toBeVisible();
  await panel.getByTestId("tentative-r-test-1").click();
  await expect(panel).toContainText("2 tests fail: rounding of partial credit notes.");
  await expect(panel.getByTestId("ce-qu-il-a-fait")).toContainText("src/billing/rates.py");
  await panel.getByRole("tab", { name: "Log" }).click();
  await expect(panel.getByTestId("live-log")).toContainText("session/request_permission");
});

test("run: ACP log with refused permissions highlighted", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r3");
  await expect(page.getByText("ACP journal")).toBeVisible();
  await expect(page.getByPlaceholder("Filter")).toBeVisible();
});

test("trains : gel impossible sans motif", async ({ page }) => {
  await page.goto("/p/billing-api/trains");
  await expect(page.getByText("Environment prod")).toBeVisible();
  await page.getByRole("button", { name: "Freeze" }).first().click();
  await expect(page.getByText("A reason for the freeze is required")).toBeVisible();
});

test("findings : triage disponible", async ({ page }) => {
  await page.goto("/p/billing-api/findings");
  await expect(page.getByText("N+1 query when loading order lines")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create the ticket" })).toBeVisible();
});

test("wizard: slug validation before continuing", async ({ page }) => {
  await page.goto("/projects/new");
  await page.getByLabel(/^Software, from a code repository/).check();
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByLabel("Identifier (slug)").fill("Billing API");
  await expect(page.getByText("Lowercase letters, digits and dashes only")).toBeVisible();
});

test("sign-in: the page exists and says what it expects", async ({ page }) => {
  await page.goto("/login?next=%2Fadmin");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  // in demo mode the session is simulated: the page says so instead of a button towards an absent IdP
  await expect(page.getByText(/Demo mode: the session/)).toBeVisible();
});

test("administration: members and tokens have a screen", async ({ page }) => {
  await page.goto("/admin");
  await expect(page.getByRole("button", { name: /Create an API token/ })).toBeVisible();
  await page.getByRole("link", { name: "Members" }).click();
  await expect(page.getByText(/Members of/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Remove / }).first()).toBeVisible();
});

test("the administration lists my roles by scope (S40-07)", async ({ page }) => {
  await page.goto("/admin");
  const roles = page.getByRole("list", { name: "Your roles" });
  await expect(roles).toContainText("platform : org_admin");
  await expect(roles).not.toContainText("acme :");
});

test("members: a person appears once; a project has its members; org_admin is not a project role (S30-06)", async ({ page }) => {
  await page.goto("/admin/members");
  const membres = page.getByTestId("membres");
  await expect(membres.getByTestId("membre-marc@acme.dev")).toHaveCount(1);
  await expect(membres.getByTestId("membre-marc@acme.dev")).toContainText("checkout-web · Release captain");
  await expect(page.getByTestId("roles-additifs")).toContainText("cannot take anything away");
  // The scope of an invitation is picked among the projects; a project leaves org_admin out.
  await page.getByLabel("Scope").selectOption("billing-api");
  await expect(page.getByLabel("Role", { exact: true }).locator("option[value=org_admin]")).toHaveCount(0);

  await page.goto("/p/billing-api/members");
  const barre = page.getByRole("complementary", { name: "Sidebar" });
  await expect(barre.getByRole("link", { name: "Members" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("membres").getByRole("row")).toHaveCount(3);
  const role = page.getByRole("combobox", { name: "Role of lea@acme.dev on billing-api" });
  await expect(role).toHaveValue("project_owner");
  await expect(role.locator("option[value=org_admin]")).toHaveCount(0);
});

test("administration: a section declared by an extension renders, and its secret shows only once", async ({
  page,
}) => {
  await page.goto("/admin");
  await page.getByRole("link", { name: "SCIM provisioning" }).click();
  await expect(page).toHaveURL(/\/admin\/x\/scim$/);
  const section = page.getByTestId("admin-section-scim");
  await expect(section.getByLabel(/accept SCIM requests/)).toBeChecked();
  await expect(section.getByRole("table", { name: "tokens" })).toContainText("okta");
  await section.getByRole("button", { name: "Revoke" }).click();
  await expect(section.getByText("The identity provider stops provisioning with this token.")).toBeVisible();
  await section.getByRole("button", { name: "Cancel" }).click();
  await section.getByLabel(/^name/).fill("okta");
  await section.getByRole("button", { name: "create a token" }).click();
  await expect(page.getByTestId("secret-once").getByRole("textbox")).toHaveValue("scim_demo_shown_once");
  await page.getByRole("button", { name: "I have copied it" }).click();
  await expect(page.getByTestId("secret-once")).toHaveCount(0);
});

test("administration: in community, what the enterprise edition adds, without a fake screen", async ({ page }) => {
  await page.goto("/admin/edition");
  await expect(page.getByText(/runs the community core/)).toBeVisible();
  await expect(page.getByRole("list", { name: "What the enterprise edition adds" })).toContainText(
    "SCIM provisioning of users and groups",
  );
});

test("integrations: a token for Claude Code, slipped into the snippet", async ({ page }) => {
  await page.goto("/integrations");
  await expect(page.getByRole("heading", { name: "Integrations", level: 1 })).toBeVisible();
  await expect(page.getByTestId("mcp-url")).toHaveText("http://localhost:3000/mcp");
  await expect(page.getByTestId("snippet")).toContainText("claude mcp add --transport http choregos");
  await page.getByRole("button", { name: "Create an access token" }).click();
  await expect(page.getByTestId("snippet")).toContainText("chg_demo_jeton_affiche_une_fois");
  await expect(page.getByTestId("connection-status")).toBeVisible();
});

test("a project's integrations: the project's door, and claude.ai says why it does not reach it", async ({ page }) => {
  await page.goto("/p/billing-api/integrations/cursor");
  await expect(page.getByTestId("mcp-url")).toHaveText("http://localhost:3000/mcp/projects/acme:billing-api");
  await page.getByRole("link", { name: "claude.ai" }).click();
  await expect(page.getByText("Not reachable from here yet")).toBeVisible();
});

test("an ontology proposal is an action: a former link leads there, and says what it touches", async ({
  page,
}) => {
  // A decision link issued before S20-10 (MCP door, email): same identifier, actions page.
  await page.goto("/p/billing-api/proposals/pr1");
  await expect(page).toHaveURL(/\/p\/billing-api\/actions\/pr1$/);
  const ontology = page.getByTestId("ontologie");
  await expect(ontology).toContainText("open_infra_pr");
  await expect(ontology).toContainText("os-reboot-required");
  await expect(page.getByTestId("regles-de-decision")).toContainText("within 10 min");
  await expect(page.getByRole("button", { name: "Reject" })).toBeDisabled();
  await page.getByLabel("Reason to reject").fill("the maintenance window is frozen");
  await expect(page.getByRole("button", { name: "Reject" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Approve" })).toBeEnabled();
  await page.goto("/p/billing-api/proposals");
  await expect(page).toHaveURL(/\/p\/billing-api\/actions$/);
});

test("workflows: a project carries several, each reads as a process and as a map", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  await expect(page.getByTestId("workflow-card-default-simple")).toContainText("Whatever no rule claims");
  await expect(page.getByTestId("workflow-card-hotfix")).toContainText("labelled incident");
  await page.getByRole("link", { name: "default-simple" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/workflows\/default-simple$/);
  // A workflow opens on its graph (S55-06); its steps in words are one tab away.
  await expect(page.getByTestId("carte-en-serpentin")).toBeVisible();
  await page.getByRole("link", { name: "Steps" }).click();
  await expect(page.getByTestId("process-step-t-implement")).toContainText("the agent stayed within its allowed paths");
  await page.getByRole("link", { name: "Graph" }).click();
  // The map opens as a snake (S22-05); the flat list is one tab away.
  await expect(page.getByTestId("carte-en-serpentin")).toBeVisible();
  await page.getByRole("button", { name: "List" }).click();
  // The defaults leave every agent state: the legend says them once, the map does not draw them.
  await expect(page.getByTestId("workflow-defaults").getByRole("listitem")).toHaveCount(2);
  // The nominal path reads alone: an escalation shows around its state, or all of them on request.
  const mapBox = page.getByTestId("workflow-graph");
  await expect(mapBox.getByRole("list", { name: "Nominal path" }).locator(":scope > li")).toHaveCount(3);
  await expect(mapBox.getByTestId("secondaires-ready")).toHaveCount(0);
  await mapBox.getByTestId("etat-ready").hover();
  await expect(mapBox.getByTestId("secondaires-ready")).toContainText("When retries run out → Needs a human");
  await page.mouse.move(0, 0);
  await expect(mapBox.getByTestId("secondaires-ready")).toHaveCount(0);
  await page.getByLabel(/Show every retry, rejection and escalation/).check();
  await expect(mapBox.getByTestId("secondaires-ready")).toBeVisible();
});

test("workflows: a label changed on the map reads in the process view, then is published", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple?view=list");
  const mapBox = page.getByTestId("workflow-graph");
  await mapBox.getByTestId("etat-inbox").click();
  const panel = page.getByTestId("panneau-etat");
  await panel.getByLabel("Label").fill("Nouvelles demandes");
  await panel.getByRole("button", { name: "Set label" }).click();

  // Nothing is published: the draft says the gesture, its diff, and the map is redrawn from it.
  const draft = page.getByTestId("draft");
  await expect(draft).toContainText("1 change not published yet");
  await draft.getByText("Last change").click();
  await expect(page.getByTestId("dernier-diff")).toContainText("+  inbox: { display: Nouvelles demandes, kind: wait }");
  await expect(mapBox.getByTestId("etat-inbox")).toContainText("Nouvelles demandes");

  // The process view reads the same draft; publishing creates the version following the one that was read.
  await page.getByRole("link", { name: "Steps" }).click();
  await expect(page.getByTestId("process-step-t-refine")).toContainText("Nouvelles demandes");
  await draft.getByRole("button", { name: "Publish v2" }).click();
  await expect(draft.getByRole("status")).toHaveText("default-simple v2 published — running items finish on their version");
});

test("workflows: the process view opens a step's panel, its time limit prefilled", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/steps");
  await page.getByRole("button", { name: "Edit t-refine" }).click();
  const panel = page.getByTestId("panneau-transition");
  await expect(panel).toBeVisible();
  // The time limit read is prefilled: t-implement has one, t-refine does not.
  await expect(panel.getByLabel("At most (hours, empty: the default)")).toHaveValue("");
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Edit t-implement" }).click();
  await expect(page.getByTestId("panneau-transition").getByLabel("At most (hours, empty: the default)")).toHaveValue("72");
});

test("the former workflow page leads to the list", async ({ page }) => {
  await page.goto("/p/billing-api/workflow");
  await expect(page).toHaveURL(/\/p\/billing-api\/workflows$/);
});

test("a workflow's history: two versions compare, an old one is republished", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/history");
  await expect(page.getByRole("table", { name: "Versions of default-simple" }).getByRole("row")).toHaveCount(3);
  // By default, the previous version against the active one: the gate added in v2.
  await expect(page.getByTestId("workflow-diff")).toContainText("gates: [scope_respected, ci_green]");
  await page.getByRole("button", { name: "Restore v1" }).click();
  await expect(page.getByRole("button", { name: "Republish v1" })).toBeVisible();
});

test("board per workflow: the request picks its workflow, and its fields come from it", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  await expect(page.getByText("Credit notes are not deducted from the total")).toBeVisible();
  await page.getByRole("button", { name: "New work item" }).click();
  await page.getByLabel("Workflow", { exact: true }).selectOption("hotfix");
  const fields = page.getByTestId("request-fields");
  await expect(fields.getByLabel("incident id (required)")).toBeVisible();
  await expect(fields.getByLabel("severity (required)")).toBeVisible();
  await expect(fields.getByLabel("detected on")).toHaveAttribute("type", "date");
  // The incident flow's board: its columns, not the default's.
  await page.keyboard.press("Escape");
  await page.getByRole("navigation", { name: "Board workflow" }).getByRole("link", { name: /hotfix/ }).click();
  await expect(page).toHaveURL(/workflow=hotfix/);
});

test("agents: the registry, and a connected Claude Code that acts as an external agent", async ({ page }) => {
  await page.goto("/agents");
  await expect(page.getByRole("heading", { name: "Agents", exact: true })).toBeVisible();
  await expect(page.getByTestId("agent-onboarding-coordinator")).toContainText("Onboarding coordinator");
  // The registry's 30-day columns (S30-04).
  await expect(page.getByTestId("agent-onboarding-coordinator")).toContainText("US$6.42");
  await expect(page.getByTestId("agent-onboarding-coordinator")).toContainText("86 %");
  // AI clients live on their own page now (S30-04).
  await expect(page.getByTestId("client-tok-claude")).toHaveCount(0);
  await page.goto("/integrations");
  await expect(page.getByRole("heading", { name: "Integrations", level: 1 })).toBeVisible();
  // Lea's Claude Code token called the door: connected, and attached to its external agent.
  const claude = page.getByTestId("client-tok-claude");
  await expect(claude).toContainText("Connected");
  await expect(claude).toContainText("claude-code/2.1.0");
  await expect(claude.getByRole("link", { name: "Acts as Léa's Claude Code" })).toBeVisible();
  // A client never called is not said to be connected, and registers.
  await expect(page.getByTestId("client-tok-cursor")).toContainText("Never called");
  await expect(page.getByTestId("client-tok-cursor").getByRole("button", { name: "Register as an external agent" })).toBeVisible();

  await page.goto("/agents");
  await page.getByRole("link", { name: "Onboarding coordinator" }).click();
  await expect(page.getByTestId("mesures")).toContainText("14 (12 succeeded, 2 failed)");
  await page.getByRole("link", { name: "Definition" }).click();
  await expect(page.getByTestId("definition-groups")).toContainText("onboarding-procedure@1");
  await page.locator("summary", { hasText: "Capabilities" }).click();
  await page.getByRole("link", { name: "onboarding-procedure@1" }).click();
  await expect(page.getByTestId("fichiers")).toContainText("Entra accounts ten days before the start date");
});

/** A new agent's page (S39-05): the short first step, then the builder; a folded section is opened by its summary. */
async function newAgentBuilder(page: Page, name: string) {
  await page.goto("/agents");
  await page.getByRole("link", { name: "New agent" }).click();
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("What should it do?").fill(`${name}: what it is for, in a sentence.`);
  await page.getByRole("button", { name: "Start from a blank form" }).click();
  return page.getByTestId("agent-builder");
}

async function unfold(builder: Locator, title: string) {
  await builder.locator("summary", { hasText: title }).click();
}

test("an agent is created from the builder alone, every field of its version set (S32-01)", async ({ page }) => {
  const builder = await newAgentBuilder(page, "Triage");
  await expect(builder.getByLabel("Slug")).toHaveValue("triage");
  await builder.getByLabel("Instructions").fill("Sort {{ ticket.title }} by urgency.");
  for (const title of ["Model and runtime", "Limits and budget", "Skills", "Tools (MCP servers)"]) await unfold(builder, title);
  await builder.getByLabel("Model").selectOption("platform/cheap");
  await builder.getByLabel("Backend").selectOption("claude-code");
  await builder.getByLabel("Max turns").fill("12");
  await builder.getByLabel("Budget per run (USD)").fill("0.5");
  await builder.getByLabel("Budget per day (USD)").fill("4");
  await builder.getByLabel("Add a skill").selectOption("onboarding-procedure");
  await builder.getByRole("button", { name: "Attach" }).click();
  await builder.getByLabel("Add a server").selectOption("supplier-agent");
  await builder.getByRole("button", { name: "Add", exact: true }).click();
  await builder.getByLabel("Tool patterns of supplier-agent").fill("track_*");
  await page.getByRole("tab", { name: "What it costs and allows" }).click();
  const allows = page.getByTestId("ce-que-ca-permet");
  await expect(allows).toContainText("A run stops at US$0.50");
  await expect(allows.getByTestId("outils-resolus")).toContainText("track_order — called freely");
  await expect(allows.getByTestId("outils-resolus")).not.toContainText("order_laptop");
  await builder.getByRole("button", { name: "Create and activate" }).click();
  await expect(page).toHaveURL(/\/agents\/triage$/);
  await page.getByRole("link", { name: "Definition" }).click();
  const version = page.getByTestId("definition-groups");
  await expect(version).toContainText("platform/cheap");
  await expect(version).toContainText("claude-code");
  await expect(version).toContainText("supplier-agent");
  await expect(version).toContainText("track_*");
  await expect(version).toContainText("onboarding-procedure");
});

test("the builder sets which knowledge bases an agent reads and whose memory it keeps, and the version holds them (S32-01)", async ({ page }) => {
  const builder = await newAgentBuilder(page, "Zoning reviewer");
  await expect(builder.getByLabel("Slug")).toHaveValue("zoning-reviewer");
  await builder.getByLabel("Instructions").fill("Review {{ ticket.title }} against the zoning principles.");
  await unfold(builder, "Knowledge");
  await unfold(builder, "Memory");
  const bases = builder.getByTestId("bases-de-la-version");
  await expect(bases).toContainText("None — it reads no knowledge base.");
  await expect(builder.getByRole("button", { name: "Read it" })).toBeDisabled();
  await builder.getByLabel("Add a knowledge base").selectOption("architecture-framework");
  await builder.getByRole("button", { name: "Read it" }).click();
  await expect(bases).toContainText("architecture-framework");
  // A base already read is no longer offered.
  // (The placeholder and the demo's project-scoped base remain.)
  await expect(builder.getByLabel("Add a knowledge base").locator("option")).toHaveCount(2);
  await expect(builder.getByLabel("Add a knowledge base").locator("option[value='architecture-framework']")).toHaveCount(0);
  // Memory: its own memory in the project, instead of the project's shared one (S40-11).
  await builder.getByRole("button", { name: "Remove project:shared" }).click();
  await builder.getByLabel("Add a memory").selectOption("project:own");
  await builder.getByRole("button", { name: "Add it" }).click();
  await builder.getByTestId("memory-binding").getByLabel("Read and write").check();
  await page.getByRole("tab", { name: "What it costs and allows" }).click();
  const allows = page.getByTestId("ce-que-ca-permet");
  await expect(allows.getByTestId("connaissances-resolues")).toContainText(
    "architecture-framework — laid read-only in the workspace; served to every project of the organisation.",
  );
  await expect(allows.getByTestId("memoire-resolue")).toContainText("Its own memory in the project — read and written");
  await builder.getByRole("button", { name: "Create and activate" }).click();
  await expect(page).toHaveURL(/\/agents\/zoning-reviewer$/);

  // Its next version starts from what v1 holds, and can drop both.
  await page.getByRole("link", { name: "Definition" }).click();
  await expect(page.getByTestId("definition-groups")).toContainText("architecture-framework");
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  const suivante = page.getByTestId("agent-builder");
  await expect(suivante.getByTestId("bases-de-la-version")).toContainText("architecture-framework");
  await expect(suivante.getByTestId("memory-binding")).toHaveCount(1);
  await expect(suivante.getByTestId("memory-binding")).toContainText("project:own");
  await suivante.getByRole("button", { name: "Remove architecture-framework" }).click();
  await suivante.getByRole("button", { name: "Remove project:own" }).click();
  await expect(page.getByTestId("connaissances-resolues")).toContainText("None.");
  await expect(page.getByTestId("memoire-resolue")).toContainText("No memory: a run neither reads nor writes");
});

test("an agent made from the form runs on a sample under a capped budget, and a failing eval blocks its next version (S32-03)", async ({ page }) => {
  const builder = await newAgentBuilder(page, "Escalation");
  await expect(builder.getByLabel("Slug")).toHaveValue("escalation");
  await builder.getByLabel("Instructions").fill("Escalate {{ ticket.title }} to the on-call engineer.");
  await unfold(builder, "Limits and budget");
  await unfold(builder, "Eval suite");
  await builder.getByLabel("Budget per run (USD)").fill("1");
  // Its eval suite: the instructions rendered on a VIP sample must escalate it.
  await builder.getByRole("button", { name: "Add a case" }).click();
  await builder.getByLabel("Case name").fill("vip");
  await builder.getByLabel("Case sample title").fill("VIP outage");
  await builder.getByLabel("Must contain (one per line)").fill("Escalate VIP outage");
  // Try it before creating: a project, a sample, a budget of 0.50 $ at most.
  const banc = page.getByTestId("banc-d-essai");
  await banc.getByLabel("Sample title").fill("Checkout down for every customer");
  await banc.getByLabel("Trial budget (USD, 0.50 at most)").fill("2");
  await banc.getByRole("button", { name: "Try it" }).click();
  const result = banc.getByTestId("resultat-d-essai");
  await expect(result).toContainText("capped at US$0.50");
  await expect(result).toContainText("succeeded");
  await expect(result).toContainText("Sorted: urgent");
  await expect(result.getByRole("list", { name: "Eval results" })).toContainText("Passed");
  await builder.getByRole("button", { name: "Create and activate" }).click();
  await expect(page).toHaveURL(/\/agents\/escalation$/);

  // Its next version breaks the case: the publication is refused, the case named.
  await page.getByRole("link", { name: "Definition" }).click();
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  const suivante = page.getByTestId("agent-builder");
  await suivante.getByRole("textbox", { name: "Instructions" }).fill("Handle {{ ticket.title }} calmly.");
  await suivante.getByLabel("What changed and why").fill("Calmer wording");
  await suivante.getByRole("button", { name: "Publish v2" }).click();
  await expect(suivante.getByTestId("evals-en-echec")).toContainText("vip: missing “Escalate VIP outage”");
  await page.getByRole("link", { name: "History" }).click();
  await expect(page.getByTestId("history").getByRole("listitem")).toHaveCount(1);
});

test("an agent's page is in tabs: overview, definition, history, lifecycle, runs, access (S30-05, S40-12)", async ({ page }) => {
  await page.goto("/agents/onboarding-coordinator");
  const tabs = page.getByRole("navigation", { name: "Sections of Onboarding coordinator" });
  await expect(tabs.getByRole("link")).toHaveText(["Overview", "Definition", "History", "Lifecycle", "Runs", "Access"]);
  await expect(tabs.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("mesures")).toBeVisible();
  await expect(page.getByTestId("version")).toHaveCount(0);

  await tabs.getByRole("link", { name: "History" }).click();
  await expect(page).toHaveURL(/tab=history/);
  await expect(page.getByTestId("history").getByRole("listitem")).not.toHaveCount(1);
  await expect(page.getByTestId("mesures")).toHaveCount(0);

  await tabs.getByRole("link", { name: "Runs" }).click();
  const runs = page.getByTestId("runs-de-l-agent");
  await expect(runs).toContainText("billing-api");
  await runs.getByRole("link").first().click();
  await expect(page).toHaveURL(/\/p\/billing-api\/runs\//);

  await page.goto("/agents/onboarding-coordinator?tab=access");
  await expect(page.getByRole("heading", { name: "Tools and servers" })).toBeVisible();
});

test("agents: an external agent shows its client, and a project its implicit agents", async ({ page }) => {
  await page.goto("/agents/leas-claude-code?tab=access");
  await expect(page.getByTestId("clients-de-l-agent")).toContainText("claude-code/2.1.0");
  await page.goto("/p/billing-api/agents");
  await expect(page.getByTestId("epingles")).toContainText("onboarding-coordinator");
  await expect(page.getByTestId("epingles")).toContainText("Tightened");
  await expect(page.getByTestId("implicites")).toContainText("refiner");
  await expect(page.getByTestId("implicites")).toContainText("dev");
  await page.getByTestId("implicites").getByRole("button", { name: "Register" }).first().click();
  await expect(page.getByTestId("implicites").getByRole("status")).toContainText("actors.refiner.agent: refiner");
});

test("settings: the connectors the workflows require, and why; a secret by reference", async ({ page }) => {
  await page.goto("/p/billing-api/settings/connections");
  const connectors = page.getByTestId("connecteurs");
  // Billing API ships software: a repository and a CI, each with its reason.
  await expect(page.getByTestId("connecteur-scm")).toContainText("Not connected");
  await expect(page.getByTestId("connecteur-scm")).toContainText("the guarantee scope_respected on t-implement");
  // Nothing requires a notification: the row does not exist until it is added.
  await expect(page.getByTestId("connecteur-notify")).toHaveCount(0);
  await page.getByLabel("Add a connector").selectOption("notify");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByTestId("connecteur-notify")).toBeVisible();
  // Jira: the form comes from its schema; a secret is typed (a password, sealed by the platform,
  // ADR 0058) or, for an operator, named by reference.
  await page.getByTestId("connecteur-tracker").getByRole("button", { name: "Edit" }).click();
  await page.getByTestId("connecteur-tracker").getByRole("combobox").first().selectOption("jira");
  await expect(page.getByLabel("api token value")).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Use a reference instead" }).first().click();
  await page.getByLabel("api token reference").fill("s3cr3t");
  await expect(page.getByTestId("connecteur-tracker").getByRole("alert")).toContainText("A reference is expected");
  await page.getByLabel("api token reference").fill("env:CONNECTOR_JIRA_TOKEN");
  await expect(page.getByTestId("connecteur-tracker").getByRole("alert")).toHaveCount(0);
  await expect(connectors).toBeVisible();
});

test("organisation connectors: every operation carries its policy; a project can only tighten", async ({ page }) => {
  await page.goto("/admin/connectors");
  const operations = page.getByTestId("operations-entra-acme");
  await expect(operations).toContainText("create_account");
  await expect(page.getByLabel("Policy of create_account")).toHaveValue("approval");
  await expect(page.getByLabel("Project groups of create_account")).toHaveValue("hr");

  await page.goto("/p/billing-api/settings/security");
  const projectBox = page.getByTestId("operations-du-projet");
  await expect(projectBox).toContainText("read_user");
  // `create_account` est `approval` dans l'organisation : le choice ne propose pas `allowed`.
  const choice = page.getByLabel("This project's policy for create_account");
  await expect(choice.locator("option")).toHaveText(["As the organisation", "approval", "forbidden"]);
});

test("connectors: business systems, MCP servers and delivery tools of the projects, each in its section", async ({
  page,
}) => {
  await page.goto("/admin/connectors");
  await expect(page.getByTestId("glossaire")).toHaveAttribute("open", "");
  await expect(page.getByTestId("section-metier")).toContainText("entra-acme");
  await expect(page.getByTestId("section-metier")).toContainText("directory (identity)");
  await expect(page.getByTestId("section-mcp")).toContainText("supplier-agent");
  await expect(page.getByTestId("section-metier")).not.toContainText("supplier-agent");
  await expect(page.getByTestId("livraison-checkout-web")).toContainText("Work tracker: jira");
  await expect(page.getByTestId("livraison-checkout-web")).toContainText("Deployment (CD): argocd");
  await page.getByTestId("livraison-checkout-web").getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/p\/checkout-web\/settings$/);
});

test("an organisation MCP server: discovering says what is born closed", async ({ page }) => {
  await page.goto("/admin/connectors");
  await expect(page.getByLabel("Policy of order_laptop")).toHaveValue("forbidden");
  // The demo also runs a plugin's server (S43-08): discover the supplier's.
  await page.getByTestId("connector-supplier-agent").getByRole("button", { name: "Discover" }).click();
  await expect(page.getByRole("status")).toHaveText(
    "1 new (closed until you open them), 1 changed their schema (closed again)",
  );
});

test("the decision inbox: an action proposed by an agent waits for a person", async ({ page }) => {
  // The former address leads to the inbox (S23-02).
  await page.goto("/approvals");
  await expect(page).toHaveURL(/\/inbox$/);
  const inbox = page.getByTestId("boite");
  await expect(inbox).toContainText("Order Léa's laptop");
  await expect(inbox).toContainText("proposed by the agent onboarding-coordinator");
  await inbox.getByRole("link", { name: /Order Léa's laptop/ }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/actions\/act-poste$/);
  await expect(page.getByRole("button", { name: "Reject" })).toBeDisabled();
  await page.getByRole("button", { name: "Approve" }).click();
  // An already decided action shows its decisions and the log of its effects, key by key.
  await page.goto("/p/billing-api/actions/act-comptes");
  await expect(page.getByTestId("decisions")).toContainText("approve by lea@acme.dev");
  await expect(page.getByTestId("effet-1")).toContainText("Key act-comptes:1 · 2 attempt(s)");
});

test("agents: the catalogue installs, and Claude Code connects in one click", async ({ page }) => {
  await page.goto("/agents");
  await page.getByRole("button", { name: "Add from catalogue" }).click();
  const catalogue = page.getByRole("dialog", { name: "Add from catalogue" }).getByTestId("catalogue");
  await expect(catalogue.getByTestId("catalogue-developer")).toContainText("Not installed");
  await expect(catalogue.getByTestId("catalogue-reviewer")).toContainText("an update is available");
  await catalogue.getByTestId("catalogue-developer").getByRole("button", { name: "Install" }).click();
  // Connecting a client is on the AI clients' page (S30-04).
  await page.goto("/integrations");
  const claude = page.getByTestId("client-catalogue-claude-code");
  await claude.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByTestId("extrait-claude-code")).toContainText("chg_demo_shown_once");
  await expect(page.getByTestId("client-catalogue-chatgpt")).toContainText("Not available here");
});

test("inbox: a work item stopped on a human request is decided there, and the header counts it (S23-02)", async ({ page }) => {
  await page.goto("/");
  // Two tickets (Spec to approve; the HR badge task, S30-08), two actions and two crossed budgets (S29-04),
  // and a request for network access (S39-22: counted for an administrator, who decides it) wait for someone.
  await expect(page.getByRole("link", { name: /^Inbox/ })).toContainText("7");
  await page.getByRole("link", { name: /^Inbox/ }).click();
  await expect(page).toHaveURL(/\/inbox$/);
  const ticket = page.getByTestId("attente-w2");
  await expect(ticket).toContainText("Export invoices as PDF");
  await expect(ticket).toContainText("Approval of the specification requested");
  await expect(ticket).toContainText("billing-api · acme/billing-api#124 · Spec to approve · asked");
  await expect(ticket).toContainText("due in 21 hours");
  await ticket.getByRole("button", { name: "Approve" }).click();
  await expect(ticket.getByRole("alert")).toHaveCount(0);
  await ticket.getByRole("link", { name: "Export invoices as PDF" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/items\/w2$/);
});

test("getting started: a new project says what it lacks; a running project says nothing (S23-03)", async ({ page }) => {
  await page.goto("/p/checkout-web");
  const checklist = page.getByTestId("mise-en-route");
  await expect(checklist).toBeVisible();
  await expect(page.getByTestId("etape-provisioned")).toContainText("Project provisioned");
  await expect(page.getByTestId("etape-connector-cd")).toContainText("demo is a stand-in");
  await expect(page.getByTestId("etape-people-release-captain")).toContainText("(to do)");
  await page.getByTestId("etape-connector-cd").getByRole("link", { name: "Fix" }).click();
  await expect(page).toHaveURL(/\/p\/checkout-web\/settings\/connections#connections$/);

  await page.goto("/p/billing-api");
  await expect(page.getByText("PRs merged on first pass")).toBeVisible();
  await expect(page.getByTestId("mise-en-route")).toHaveCount(0);
});


test("board: what waits for a person is said at the top, and leads there even off-screen (S23-09)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/p/billing-api/board");
  const attentes = page.getByTestId("attentes-du-board");
  await expect(attentes).toContainText("1 ticket waits for a person");
  const mapBox = page.locator("#carte-w2");
  // The banner still leads to the card; since 2026-11-02 the board hides its empty columns and uses
  // the whole width, so the card is no longer pushed out of sight to begin with.
  await attentes.getByRole("link").first().click();
  await expect(mapBox).toBeInViewport();
  await expect(mapBox.getByRole("button", { name: "Approve" })).toBeVisible();
  // Each workflow's summary names each of its shown columns, and says where someone is waited for.
  const sommaire = page.getByRole("navigation", { name: "Columns of default-simple" });
  await expect(sommaire.getByRole("link")).toHaveCount(
    await page.locator('section[id^="colonne-default-simple-"]').count(),
  );
  await expect(sommaire).toContainText("1 waiting");
});

test("costs: a screen, a currency, and a graph that reads (S23-10)", async ({ page }) => {
  for (const route of ["/p/billing-api", "/p/billing-api/items/w1", "/p/billing-api/board"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    // A work item's screen mixed the total in € and its runs in US$; the overview converted at 0.92.
    await expect(page.locator("main")).not.toContainText("€");
  }
  await page.goto("/p/billing-api");
  const graph = page.getByTestId("graphe-des-couts");
  await expect(graph.getByRole("img")).toHaveAttribute("aria-label", /^Cost per day, 14 days: highest US\$/);
  await expect(page.getByText("Daily budget")).toBeVisible();
  await graph.getByText("As a table").click();
  await expect(graph.getByRole("table", { name: "Cost per day" }).getByRole("row")).toHaveCount(15);
});

test("wayfinding: a ticket and a run have their breadcrumb, and the board entry stays active (S23-12, S30-03)", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  const tabs = page.getByRole("complementary", { name: "Sidebar" });
  await expect(tabs.getByRole("link", { name: "Board" })).toHaveAttribute("aria-current", "page");
  const fil = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(fil.getByRole("link", { name: "Board" })).toHaveAttribute("href", "/p/billing-api/board");

  await page.goto("/p/billing-api/runs/r3");
  await expect(tabs.getByRole("link", { name: "Board" })).toHaveAttribute("aria-current", "page");
  // Projects › Billing API › Board › the ticket › the run.
  await expect(fil.getByRole("link")).toHaveCount(4);
  await expect(fil.locator("[aria-current=page]")).toContainText("attempt");

  await page.goto("/p/billing-api/actions/act-poste");
  await expect(tabs.getByRole("link", { name: "Actions" })).toHaveAttribute("aria-current", "page");
});

test("landmarks: the journey legend tells \"in progress\" and \"done\" apart other than by colour (S23-12)", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  const legende = page.getByRole("list", { name: "Legend" });
  const pastille = (libelle: string) =>
    legende.getByRole("listitem").filter({ hasText: libelle }).locator("span[aria-hidden]").first();
  const enCours = await pastille("in progress").evaluate((e) => getComputedStyle(e).borderTopColor);
  const doneColour = await pastille("done").evaluate((e) => getComputedStyle(e).borderTopColor);
  // What lives wears a ring; what is done does not.
  expect(enCours).not.toBe("rgba(0, 0, 0, 0)");
  expect(doneColour).toBe("rgba(0, 0, 0, 0)");
});

test("a token, a name, a place: the three pages speak of \"access token\" and lead to the list (S23-14)", async ({ page }) => {
  await page.goto("/admin");
  const checklist = page.getByRole("heading", { name: "My access tokens" });
  await expect(checklist).toBeVisible();
  // The list says what each token opens: the API, or a client's MCP door.
  await expect(page.getByText(/· (API and CLI|AI client, (read-only|can open work items)) ·/).first()).toBeVisible();

  await page.goto("/integrations/claude-code");
  await expect(page.getByText(/Connected this way, Claude Code acts as you/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Admin › My access tokens" }).first()).toHaveAttribute("href", "/admin");

  // The caller's AI clients are on the AI clients' page, under the connection steps (S30-04).
  await page.goto("/integrations");
  await expect(page.getByRole("heading", { name: "Your AI clients" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Admin › My access tokens" }).first()).toHaveAttribute("href", "/admin");
  await expect(page.getByText(/To connect one simply as yourself/)).toBeVisible();
  await expect(page.locator("main")).not.toContainText(/your MCP clients|mint a token|API token/);
});
