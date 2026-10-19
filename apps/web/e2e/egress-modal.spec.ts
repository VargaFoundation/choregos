import { expect, test, type Page } from "@playwright/test";

/**
 * Network access (S39-22): a governed "add" whose host the organisation has not opened asks for it
 * in a modal, on the console's mock API (which answers 409 `egress_required` as the real one does).
 * An administrator allows in one gesture; anyone else asks, and the request reaches the inbox.
 */

const writes = (page: Page) =>
  page.evaluate(() => (window as unknown as { __mockWrites?: { method: string; path: string; body: Record<string, unknown> }[] }).__mockWrites ?? []);

async function declareMcp(page: Page, url: string) {
  await page.goto("/admin/connectors");
  await page.getByPlaceholder("entra-acme").fill("vendor");
  await page.locator("label:has(span:text-is('Type')) select").selectOption("mcp/mcp");
  await page.getByLabel(/^url/).fill(url);
  // The setup procedure (S39-23) opens with the type, once its file is read; below the xl breakpoint
  // it is a sheet over the form. Wait for it and close it: an instant `isVisible()` could look before
  // it opened, and the sheet then covered "Declare" (seen at 390 px on CI).
  await page.getByTestId("procedure-close").click();
  await page.getByRole("button", { name: "Declare" }).click();
}

test("catalogue-style add: the modal names the host, shows the platform change, and an administrator allows in one call", async ({ page }) => {
  await declareMcp(page, "https://mcp.example.test/mcp");
  const modal = page.getByTestId("egress-modal");
  await expect(modal).toBeVisible();
  await expect(modal.getByTestId("org-mcp.example.test")).toHaveText("not allowed");
  await expect(modal.getByTestId("ceiling-mcp.example.test")).toHaveText("outside the declared ceiling");
  await expect(modal.getByTestId("egress-snippet")).toContainText("mcp.example.test");
  await expect(modal.getByText("Choregos cannot change the cluster's network policy")).toBeVisible();
  await expect(modal.getByRole("link", { name: "Open a prefilled issue" })).toBeVisible();
  await modal.getByRole("button", { name: "Allow and continue" }).click();
  await expect(modal).toBeHidden();
  await expect(page.getByTestId("secrets-vendor")).toBeVisible();
  const posts = (await writes(page)).filter((w) => w.method === "POST" && w.path.endsWith("/connectors"));
  expect(posts.map((p) => p.body.allow_egress)).toEqual([undefined, true]);
});

test("a developer asks an administrator: the request carries the prefilled reason and origin", async ({ page }) => {
  await page.addInitScript(() => window.sessionStorage.setItem("choregos.mock.role", "developer"));
  await page.goto("/skills");
  await page.getByLabel("Repository").fill("https://gitlab.com/acme/skills");
  await page.getByRole("button", { name: "Preview" }).click();
  const modal = page.getByTestId("egress-modal");
  await expect(modal.getByTestId("egress-approvers")).toContainText("augustin@acme.dev");
  await expect(modal.getByRole("button", { name: "Allow and continue" })).toHaveCount(0);
  await modal.getByRole("button", { name: "Ask an administrator" }).click();
  await expect(modal).toBeHidden();
  await expect(page.getByText(/retry once it is approved/)).toBeVisible();
  const requests = (await writes(page)).filter((w) => w.path.endsWith("/egress/requests"));
  expect(requests).toHaveLength(1);
  expect(requests[0]!.body).toMatchObject({
    host: "gitlab.com",
    origin: "skill-import:gitlab.com/acme/skills",
    reason: "importing skills from https://gitlab.com/acme/skills",
  });
});

test("an administrator decides a request from the inbox", async ({ page }) => {
  await page.goto("/inbox");
  const card = page.getByTestId("egress-requests");
  await expect(card).toContainText("mcp.pending.test");
  await expect(card).toContainText("asked by lea@acme.dev");
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(card.getByText("mcp.pending.test")).toHaveCount(0);
  expect((await writes(page)).some((w) => w.path.endsWith("/egress/requests/eg-3/approve"))).toBe(true);
});

test("the admin page lists the rules and what the platform must still open", async ({ page }) => {
  await page.goto("/admin/egress");
  await expect(page.getByTestId("egress-rules")).toContainText("api.github.com");
  await expect(page.getByTestId("ceiling-api")).toContainText("github.com");
  await expect(page.getByTestId("egress-pending")).toContainText("mcp.pending.test");
});

test("the modal does not scroll the page sideways at 390 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await declareMcp(page, "https://mcp.example.test/mcp");
  await expect(page.getByTestId("egress-modal")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const modal = await page.getByTestId("egress-modal").boundingBox();
  expect(modal!.x + modal!.width).toBeLessThanOrEqual(390);
});
