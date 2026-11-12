import { expect, test } from "@playwright/test";

/**
 * The console's case (ADR 0048, which supersedes ADR 0042): everything the console writes — a
 * heading, a button, a tab, a link of the navigation, a table header, a field's label, a sentence —
 * is in sentence case: it starts with a capital. Data keeps its own case (a project's name, a
 * state's label, an actor, a ticket's title): it is marked `data-verbatim`. An identifier or a code
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
  "/p/billing-api/workflows/default-simple/steps",
  "/p/billing-api/workflows/default-simple",
  "/p/billing-api/workflows/default-simple/history",
  "/p/billing-api/agents",
  "/p/billing-api/actions",
  "/p/billing-api/actions/act-poste",
  "/p/billing-api/integrations",
  "/p/billing-api/settings",
  "/p/billing-api/settings/general",
  "/p/billing-api/settings/connections",
  "/p/billing-api/settings/agents",
  "/p/billing-api/settings/security",
  "/p/billing-api/settings/people",
  "/p/billing-api/settings/danger",
  "/p/billing-api/items/w1",
  "/p/billing-api/items/w2",
  "/p/billing-api/runs/r3",
  "/p/checkout-web",
  "/inbox",
  "/agents",
  "/agents?tag=madr",
  "/agents/onboarding-coordinator",
  "/agents/onboarding-coordinator?tab=definition",
  "/agents/onboarding-coordinator?tab=history",
  "/skills",
  "/skills/library",
  "/costs",
  "/activity",
  "/skills/onboarding-procedure",
  "/knowledge",
  "/memories",
  "/memories/security-lessons",
  "/integrations",
  "/integrations/claude-code",
  "/account",
  "/admin",
  "/admin/connectors",
  "/library/mcp-servers",
  "/library/connectors",
  "/library/plugins",
  "/admin/members",
  "/admin/audit",
  "/admin/platform",
  "/admin/edition",
  "/projects/new",
  "/login",
];

/** Names written in lowercase by their owners: they keep it, wherever they stand. */
const LOWERCASE_NAMES = /^(claude\.ai|kubectl|git|npm|pnpm|uv|curl|helm|e\.g\.|i\.e\.|vs\b)/;

test("everything the console writes is in sentence case; data and codes keep theirs", async ({ page }) => {
  test.setTimeout(180_000);
  const deviations: string[] = [];
  for (const path of PAGES) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const found = await page.evaluate((lowercase) => {
      const names = new RegExp(lowercase);
      const visible = (e: Element) => (e as HTMLElement).offsetParent !== null || e.getClientRects().length > 0;
      const isData = (e: Element) => e.closest("[data-verbatim]") !== null;
      const firstLine = (e: Element) => ((e as HTMLElement).innerText ?? "").trim().split("\n")[0]!.trim();
      // A code or an identifier: `mcp:write`, `owner/repo`, `supplier-agent.order_laptop`, `#124`, a mail.
      const code = (e: Element, text: string) =>
        /^[a-z0-9_-]+[._:/@#][a-z0-9]/i.test(text) ||
        /^[a-z0-9]+(?:[_-][a-z0-9]+)+$/.test(text) ||
        e.firstElementChild?.tagName === "CODE" ||
        (e.firstElementChild !== null && e.firstElementChild.closest("[data-verbatim]") !== null && firstLine(e.firstElementChild) !== "" && text.startsWith(firstLine(e.firstElementChild)));
      const out: string[] = [];
      const labels =
        "button, [role=tab], [role=menuitem], nav a, th, summary, legend, label > span:first-child, h1, h2, h3, h4, dt, caption";
      for (const e of document.querySelectorAll(labels)) {
        if (!visible(e) || isData(e)) continue;
        const text = firstLine(e);
        if (/^[a-z]/.test(text) && !names.test(text) && !code(e, text)) out.push(`label "${text.slice(0, 70)}"`);
      }
      for (const e of document.querySelectorAll("p, li > span:first-child, [role=status], [role=alert]")) {
        if (!visible(e) || isData(e)) continue;
        const text = firstLine(e);
        if (/^[a-z]/.test(text) && !names.test(text) && !code(e, text)) out.push(`text "${text.slice(0, 70)}"`);
      }
      return out;
    }, LOWERCASE_NAMES.source);
    for (const deviation of new Set(found)) deviations.push(`${path}: ${deviation}`);
  }
  expect(deviations).toEqual([]);
});
