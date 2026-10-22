import { expect, test } from "@playwright/test";

/**
 * An organisation's own name and logo (S42-13), on the demo build (no API). The cookie `choregos_org`
 * makes the root layout draw that organisation's sample theme on the first paint; the layered
 * Acme section has a `format: "asset"` field that uploads through the mock.
 */
test.describe("the remembered organisation's theme, drawn by the server", () => {
  // No script runs: whatever shows was rendered on the server, so this is the first paint.
  test.use({ javaScriptEnabled: false });

  test("name, logo, favicon and footer are the organisation's before any script", async ({ page, context, baseURL }) => {
    await context.addCookies([{ name: "choregos_org", value: "acme-labs", url: baseURL! }]);
    await page.goto("/");
    await expect(page.getByText("Acme Labs", { exact: true }).locator("visible=true").first()).toBeVisible();
    await expect(page.locator("img[data-testid^=wordmark-logo]").locator("visible=true").first()).toHaveAttribute(
      "src",
      "/api/v1/mock-theme-assets/logo.svg",
    );
    await expect(page.locator('link[rel="icon"]')).toHaveAttribute("href", "/api/v1/mock-theme-assets/favicon.svg");
    await expect(page.getByTestId("footer-text")).toHaveText("Operated by Acme Labs");
    await expect(page.getByTestId("footer-foundation")).toContainText("Acme Labs, built on Choregos by Varga Foundation");
  });

  test("a cookie that is not a slug is ignored", async ({ page, context, baseURL }) => {
    await context.addCookies([{ name: "choregos_org", value: "..%2F..%2Fadmin", url: baseURL! }]);
    await page.goto("/");
    await expect(page.getByTestId("footer-foundation")).toContainText("Choregos by Varga Foundation");
    await expect(page.getByText("Acme Labs")).toHaveCount(0);
  });

  test("the sign-in page wears the remembered organisation too", async ({ page, context, baseURL }) => {
    await context.addCookies([{ name: "choregos_org", value: "acme-labs", url: baseURL! }]);
    await page.goto("/login");
    await expect(page.getByText("Acme Labs", { exact: true }).locator("visible=true").first()).toBeVisible();
  });
});

test("an asset field uploads a file, shows it, saves its ref and removes it", async ({ page }) => {
  await page.goto("/admin/x/acme-layered");
  const section = page.getByTestId("admin-section-acme-layered");
  const logo = section.getByLabel("logo");
  await expect(logo).toHaveAttribute("type", "file");
  await expect(logo).toHaveAttribute("accept", "image/png,image/svg+xml");

  // The browser refuses what the schema does not accept, before any request.
  await logo.setInputFiles({ name: "x.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a") });
  await expect(section.getByRole("alert")).toContainText("not accepted");
  await logo.setInputFiles({ name: "big.png", mimeType: "image/png", buffer: Buffer.alloc(300_000) });
  await expect(section.getByRole("alert")).toContainText("larger than 256 KiB");

  await logo.setInputFiles({ name: "logo.svg", mimeType: "image/svg+xml", buffer: Buffer.from("<svg xmlns='http://www.w3.org/2000/svg'/>") });
  await expect(section.getByAltText("Current file")).toHaveAttribute("src", "/api/v1/mock-theme-assets/logo.svg");
  await expect(section.getByRole("alert")).toHaveCount(0);

  await section.getByRole("button", { name: "Remove" }).click();
  await expect(section.getByAltText("Current file")).toHaveCount(0);

  // Chosen again, then saved with the form's own Save: the ref goes out in the write.
  await logo.setInputFiles({ name: "logo.svg", mimeType: "image/svg+xml", buffer: Buffer.from("<svg xmlns='http://www.w3.org/2000/svg'/>") });
  await expect(section.getByAltText("Current file")).toBeVisible();
  await section.getByRole("button", { name: "Save" }).first().click();
  await expect(section.getByRole("status")).toHaveText("Saved");
});
