import { chromium } from "@playwright/test";
const b = await chromium.launch(); const p = await b.newPage();
for (const c of process.argv.slice(2)) { await p.goto("http://127.0.0.1:3917"+c); await p.waitForLoadState("networkidle");
  const r = await p.evaluate(() => [...document.querySelectorAll("main input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=file]), main textarea, main select")]
    .filter(e => e.offsetParent && !(e.labels && [...e.labels].some(l => l.innerText.trim() && !l.classList.contains("sr-only"))) && !e.getAttribute("aria-labelledby"))
    .map(e => e.getAttribute("aria-label") || e.getAttribute("placeholder") || e.name || e.id));
  if (r.length) console.log(c, JSON.stringify(r)); }
await b.close();
