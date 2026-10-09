import { expect, test } from "@playwright/test";

/**
 * La casse de la console (ADR 0042, S23-15) : ce qu'elle NOMME est en minuscules — le design
 * system —, ce qu'elle DIT est une phrase, avec sa majuscule — la revue du 07/10. Les données
 * (un nom de projet, d'état, d'acteur, un titre de ticket) gardent la leur : `data-donnee`.
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
  "/agents/onboarding-coordinator",
  "/skills",
  "/skills/onboarding-procedure",
  "/integrations",
  "/integrations/claude-code",
  "/admin",
  "/admin/connectors",
  "/admin/members",
  "/admin/audit",
  "/admin/platform",
  "/admin/edition",
  "/projects/new",
  "/login",
];

/** Des noms propres et des sigles : ils commencent par une capitale, où qu'ils soient. */
const NOMS_PROPRES = /^(Claude|Cursor|ChatGPT|GitHub|GitLab|Jira|Slack|Okta|Entra|Argo|Choregos|Varga|YAML|API|MCP|CSV|SCIM|CLI|OAuth|DORA|CI|PR|SSO|SAML|HTTP|REST|URL)\b/;

test("ce que la console nomme est en minuscules, ce qu'elle dit commence par une capitale", async ({ page }) => {
  test.setTimeout(120_000);
  const ecarts: string[] = [];
  for (const chemin of PAGES) {
    await page.goto(chemin);
    await page.waitForLoadState("networkidle");
    const trouves = await page.evaluate((nomsPropres) => {
      const propres = new RegExp(nomsPropres);
      const visible = (e: Element) => (e as HTMLElement).offsetParent !== null || e.getClientRects().length > 0;
      const donnee = (e: Element) => e.closest("[data-donnee]") !== null;
      const premiereLigne = (e: Element) => ((e as HTMLElement).innerText ?? "").trim().split("\n")[0]!.trim();
      const sortie: string[] = [];
      const libelles = "button, [role=tab], nav a, th, summary, legend, label > span:first-child, h1, h2, h3";
      for (const e of document.querySelectorAll(libelles)) {
        if (!visible(e) || donnee(e)) continue;
        const texte = premiereLigne(e);
        if (/^[A-Z][a-z]/.test(texte) && !propres.test(texte)) sortie.push(`libellé « ${texte} »`);
      }
      for (const e of document.querySelectorAll("p")) {
        if (!visible(e) || donnee(e)) continue;
        const texte = premiereLigne(e);
        const phrase = /[a-z)]\.$/.test(texte) || /[a-z)]\. [A-Za-z]/.test(texte);
        // Une phrase qui s'ouvre sur un code ou un identifiant (`supplier-agent.order_laptop`, `mcp:write`) le garde.
        const code = /^[a-z0-9_-]+[._:/@][a-z0-9]/i.test(texte) || e.firstElementChild?.tagName === "CODE";
        if (phrase && /^[a-z]/.test(texte) && !code) sortie.push(`phrase « ${texte.slice(0, 70)} »`);
      }
      return sortie;
    }, NOMS_PROPRES.source);
    for (const ecart of new Set(trouves)) ecarts.push(`${chemin} : ${ecart}`);
  }
  expect(ecarts).toEqual([]);
});
