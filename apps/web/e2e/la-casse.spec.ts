import { expect, test } from "@playwright/test";

/**
 * The console's case (ADR 0048, which supersedes ADR 0042): everything the console writes — a
 * heading, a button, a tab, a link of the navigation, a table header, a field's label, a sentence —
 * is in sentence case: it starts with a capital. Data keeps its own case (a project's name, a
 * state's label, an actor, a ticket's title): it is marked `data-donnee`. An identifier or a code
 * (`supplier-agent.order_laptop`, `mcp:write`, `owner/repo`) keeps its case too.
 */
const PAGES = [
  "/",
  "/p/billing-api",
  "/p/billing-api/board",
  "/p/billing-api/trains",
  "/p/billing-api/findings",
  "/p/billing-api/memory",
  "/p/billing-api/workflows",
  "/p/billing-api/workflows/default-simple",
  "/p/billing-api/workflows/default-simple/map",
  "/p/billing-api/workflows/default-simple/history",
  "/p/billing-api/agents",
  "/p/billing-api/actions",
  "/p/billing-api/actions/act-poste",
  "/p/billing-api/integrations",
  "/p/billing-api/settings",
  "/p/billing-api/items/w1",
  "/p/billing-api/items/w2",
  "/p/billing-api/runs/r3",
  "/p/checkout-web",
  "/inbox",
  "/agents",
  "/agents?tag=madr",
  "/agents/onboarding-coordinator",
  "/skills",
  "/skills/catalogue",
  "/costs",
  "/skills/onboarding-procedure",
  "/knowledge",
  "/integrations",
  "/integrations/claude-code",
  "/admin",
  "/admin/connectors",
  "/admin/connectors/catalogue",
  "/admin/members",
  "/admin/audit",
  "/admin/platform",
  "/admin/edition",
  "/projects/new",
  "/login",
];

/** Names written in lowercase by their owners: they keep it, wherever they stand. */
const NOMS_EN_MINUSCULES = /^(claude\.ai|kubectl|git|npm|pnpm|uv|curl|helm|e\.g\.|i\.e\.|vs\b)/;

test("everything the console writes is in sentence case; data and codes keep theirs", async ({ page }) => {
  test.setTimeout(180_000);
  const ecarts: string[] = [];
  for (const chemin of PAGES) {
    await page.goto(chemin);
    await page.waitForLoadState("networkidle");
    const trouves = await page.evaluate((minuscules) => {
      const noms = new RegExp(minuscules);
      const visible = (e: Element) => (e as HTMLElement).offsetParent !== null || e.getClientRects().length > 0;
      const donnee = (e: Element) => e.closest("[data-donnee]") !== null;
      const premiereLigne = (e: Element) => ((e as HTMLElement).innerText ?? "").trim().split("\n")[0]!.trim();
      // A code or an identifier: `mcp:write`, `owner/repo`, `supplier-agent.order_laptop`, `#124`, a mail.
      const code = (e: Element, texte: string) =>
        /^[a-z0-9_-]+[._:/@#][a-z0-9]/i.test(texte) ||
        /^[a-z0-9]+(?:[_-][a-z0-9]+)+$/.test(texte) ||
        e.firstElementChild?.tagName === "CODE" ||
        (e.firstElementChild !== null && e.firstElementChild.closest("[data-donnee]") !== null && premiereLigne(e.firstElementChild) !== "" && texte.startsWith(premiereLigne(e.firstElementChild)));
      const sortie: string[] = [];
      const libelles =
        "button, [role=tab], [role=menuitem], nav a, th, summary, legend, label > span:first-child, h1, h2, h3, h4, dt, caption";
      for (const e of document.querySelectorAll(libelles)) {
        if (!visible(e) || donnee(e)) continue;
        const texte = premiereLigne(e);
        if (/^[a-z]/.test(texte) && !noms.test(texte) && !code(e, texte)) sortie.push(`label « ${texte.slice(0, 70)} »`);
      }
      for (const e of document.querySelectorAll("p, li > span:first-child, [role=status], [role=alert]")) {
        if (!visible(e) || donnee(e)) continue;
        const texte = premiereLigne(e);
        if (/^[a-z]/.test(texte) && !noms.test(texte) && !code(e, texte)) sortie.push(`text « ${texte.slice(0, 70)} »`);
      }
      return sortie;
    }, NOMS_EN_MINUSCULES.source);
    for (const ecart of new Set(trouves)) ecarts.push(`${chemin} : ${ecart}`);
  }
  expect(ecarts).toEqual([]);
});
