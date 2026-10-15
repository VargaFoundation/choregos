import { expect, test, type Page } from "@playwright/test";

/**
 * S39-16: a work item is filed from any project's board, whatever its tracker. On Checkout Web (Jira in
 * the demo) the platform would create the issue there first and record the work item under its key
 * (ADR 0059). In demo mode no request leaves the page: the mock API records every write.
 */
type Write = { method: string; path: string; body: Record<string, unknown> };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

test("a Jira project's board offers New work item, and the issue key comes back", async ({ page }) => {
  await page.goto("/p/checkout-web/board");
  await page.getByRole("button", { name: "New work item" }).click();
  const drawer = page.getByRole("dialog", { name: "New work item" });
  await expect(drawer.getByTestId("tracker-notice")).toContainText("Creates the issue in jira");
  await drawer.getByLabel("Title", { exact: true }).fill("Add Apple Pay");
  await drawer.getByLabel("Description").fill("Customers ask for it.");
  await drawer.getByLabel("Budget in USD").fill("2.5");
  await drawer.getByRole("button", { name: "Create and start" }).click();

  const status = page.getByTestId("work-item-created");
  await expect(status).toContainText("Created CHK-101");
  await expect(status.getByRole("link", { name: "open the issue" })).toHaveAttribute(
    "href",
    "https://jira.example/browse/CHK-101",
  );
  const [created] = await writes(page, /\/projects\/[^/]*checkout-web\/work-items$/);
  expect(created?.method).toBe("POST");
  expect(created?.body).toMatchObject({ title: "Add Apple Pay", body: "Customers ask for it.", budget_usd: 2.5, start: true });
});
