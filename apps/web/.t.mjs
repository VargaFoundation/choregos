import { chromium } from "@playwright/test";
const PAGES = ["/","/p/billing-api","/p/billing-api/board","/p/billing-api/trains","/p/billing-api/findings","/p/billing-api/memory","/p/billing-api/workflows","/p/billing-api/workflows/default-simple","/p/billing-api/workflows/default-simple/map","/p/billing-api/workflows/default-simple/yaml","/p/billing-api/workflows/default-simple/history","/p/billing-api/agents","/p/billing-api/actions","/p/billing-api/actions/act-poste","/p/billing-api/integrations","/p/billing-api/settings","/p/billing-api/items/w1","/p/billing-api/items/w2","/p/billing-api/runs/r3","/inbox","/agents","/agents/onboarding-coordinator","/skills","/skills/onboarding-procedure","/integrations","/integrations/claude-code","/admin","/admin/connectors","/admin/members","/admin/audit","/admin/platform","/admin/edition","/projects/new","/login","/p/checkout-web"];
const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const lab = {}, prose = {};
for (const c of PAGES) { await p.goto("http://127.0.0.1:3917"+c); await p.waitForLoadState("networkidle");
  const r = await p.evaluate(() => {
    const vis = (e) => e.offsetParent !== null || e.getClientRects().length;
    const t = (e) => (e.innerText || "").trim().split("\n")[0].trim();
    const L = [...document.querySelectorAll("button, nav a, h1, h2, h3, th, label > span:first-child, summary, legend, [role=tab]")].filter(vis).map(t).filter(Boolean);
    const P = [...document.querySelectorAll("main p")].filter(vis).map(e => (e.innerText||"").trim()).filter(x => x.length > 30 && /[a-z]{3,} [a-z]{3,} [a-z]{3,}/.test(x));
    return { L, P };
  });
  for (const x of r.L) if (/^[A-Z][a-z]/.test(x)) (lab[x] ??= new Set()).add(c);
  for (const x of r.P) if (/^[a-z]/.test(x)) (prose[x.slice(0,90)] ??= new Set()).add(c);
}
console.log("LABELS CAPITALISES:"); for (const [k,v] of Object.entries(lab)) console.log(" ", JSON.stringify(k), [...v].slice(0,2).join(" "));
console.log("PROSE EN MINUSCULE:"); for (const [k,v] of Object.entries(prose)) console.log(" ", JSON.stringify(k), [...v].slice(0,2).join(" "));
await b.close();
