import { expect, test, type Page } from "@playwright/test";

/**
 * Agent governance on mocks (S39-09b, ADR 0061). The mock API plays the server's decision: the
 * common `developer` agent comes with `can.publish = false` and the server's sentence; the console
 * shows both, then forks it into a project and lands on the fork. Every write is recorded on
 * `window.__mockWrites`.
 */
type Write = { method: string; path: string; body: Record<string, unknown> };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

test("the registry shows a badge per scope and filters by scope", async ({ page }) => {
  await page.goto("/agents");
  const row = (slug: string) => page.getByTestId(`agent-${slug}`);
  await expect(row("developer").getByTestId("scope")).toHaveText("Common");
  await expect(row("developer-billing-api").getByTestId("scope")).toHaveText("Project · billing-api");
  await expect(row("leas-scratchpad").getByTestId("scope")).toHaveText("Personal");
  await page.getByRole("button", { name: /^Personal \(1\)/ }).click();
  await expect(row("leas-scratchpad")).toBeVisible();
  await expect(row("developer")).toHaveCount(0);
});

test("a common agent is read only with the server's reason, and can be forked into a project", async ({ page }) => {
  await page.goto("/agents/developer");
  await expect(page.getByTestId("cannot-reason")).toContainText("only an organisation admin changes it");
  await expect(page.getByRole("button", { name: "Suspend" })).toBeDisabled();

  await page.goto("/agents/developer?tab=definition");
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeDisabled();
  await expect(page.getByTestId("why-readonly")).toContainText("Fork it into your project");

  await page.getByRole("button", { name: "Fork" }).click();
  const dialog = page.getByRole("dialog", { name: "Fork Developer" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Scope").selectOption("project");
  await dialog.getByLabel("Project").selectOption("billing-api");
  await dialog.getByLabel("New slug").fill("developer-billing");
  await dialog.getByRole("button", { name: "Fork", exact: true }).click();

  await expect(page).toHaveURL(/\/agents\/developer-billing$/);
  const [fork] = await writes(page, /\/agents\/developer\/fork$/);
  expect(fork?.body).toMatchObject({ slug: "developer-billing", scope: "project", project: "billing-api", version: 2 });
  await expect(page.getByTestId("scope")).toHaveText("Project · billing-api");
});

test("a fork behind its source says so", async ({ page }) => {
  await page.goto("/agents/developer-billing-api");
  await expect(page.getByTestId("upstream")).toContainText("moved on to v2");
});
