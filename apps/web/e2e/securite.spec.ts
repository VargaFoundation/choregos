import { expect, test } from "@playwright/test";

/** La CSP est celle qu'on croit : un nonce par requête, aucun `unsafe-inline` pour les scripts. */
test("chaque page part avec une CSP à nonce, sans unsafe-inline pour les scripts", async ({ page }) => {
  const reponse = await page.goto("/");
  expect(reponse).not.toBeNull();
  const csp = reponse!.headers()["content-security-policy"] ?? "";
  const scriptSrc = csp.split(";").map((d) => d.trim()).find((d) => d.startsWith("script-src")) ?? "";
  expect(scriptSrc).toMatch(/'nonce-[A-Za-z0-9+/=]+'/);
  expect(scriptSrc).toContain("'strict-dynamic'");
  expect(scriptSrc).not.toContain("'unsafe-inline'");
  expect(csp).toContain("frame-ancestors 'none'");
  // Et la page fonctionne quand même : les scripts de Next portent le nonce.
  await expect(page.getByRole("heading", { name: "projects" })).toBeVisible();
  const deuxieme = await page.goto("/login");
  expect(deuxieme!.headers()["content-security-policy"]).not.toEqual(csp);
});
