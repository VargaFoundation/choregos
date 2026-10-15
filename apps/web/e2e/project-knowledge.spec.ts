import { expect, test, type Page } from "@playwright/test";

/**
 * A project's knowledge, from inside the project (S39-14): the sidebar's Knowledge section lists
 * what the project's runs are served and creates a base scoped to the project by construction.
 * In demo mode the mock API records every write on `window.__mockWrites`.
 */
type Write = { method: string; path: string; body: Record<string, unknown> };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

test("the project's Knowledge section lists its bases, then the shared ones, and stays in the project", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  const bar = page.getByRole("navigation", { name: "Main navigation" });
  await bar.getByRole("link", { name: "Knowledge" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/knowledge$/);
  await expect(page.getByTestId("project-knowledge-own")).toContainText("Billing runbooks");
  await expect(page.getByTestId("project-knowledge-shared")).toContainText("Architecture framework");
  await page.getByRole("link", { name: "Billing runbooks" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/knowledge\/billing-runbooks$/);
  await expect(page.getByTestId("project-switcher")).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
});

test("a base created from the project is scoped to it, with no scope to choose", async ({ page }) => {
  await page.goto("/p/billing-api/knowledge");
  await page.getByRole("button", { name: "New knowledge base" }).click();
  const form = page.getByRole("dialog");
  await expect(form.getByText("Scope", { exact: true })).toHaveCount(0);
  await form.getByRole("textbox", { name: "Slug" }).fill("handbook");
  await form.getByRole("textbox", { name: "Name" }).fill("Handbook");
  await form.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/knowledge\/handbook$/);
  // Sent to the project's own route: the API scopes it and records the project as its owner.
  const [write] = await writes(page, /^\/projects\/[^/]+\/knowledge$/);
  expect(write?.path).toBe("/projects/billing-api/knowledge");
  expect(write?.body).toMatchObject({ slug: "handbook" });
  expect(write?.body).not.toHaveProperty("scope");
});
