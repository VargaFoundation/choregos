import { expect, test } from "@playwright/test";

/** The CSP is the one we believe: a nonce per request, no `unsafe-inline` for scripts. */
test("every page ships with a nonce CSP, without unsafe-inline for scripts", async ({ page }) => {
  const response = await page.goto("/");
  expect(response).not.toBeNull();
  const csp = response!.headers()["content-security-policy"] ?? "";
  const scriptSrc = csp.split(";").map((d) => d.trim()).find((d) => d.startsWith("script-src")) ?? "";
  expect(scriptSrc).toMatch(/'nonce-[A-Za-z0-9+/=]+'/);
  expect(scriptSrc).toContain("'strict-dynamic'");
  expect(scriptSrc).not.toContain("'unsafe-inline'");
  expect(csp).toContain("frame-ancestors 'none'");
  // And the page still works: Next's scripts carry the nonce.
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  const second = await page.goto("/login");
  expect(second!.headers()["content-security-policy"]).not.toEqual(csp);
});
