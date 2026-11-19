import { expect, test } from "@playwright/test";

/**
 * The board is live (S67-27), on the demo's scripted story: a card that runs says what runs and for
 * how long, then moves to the next column; there is no separate live page.
 */
test("the board refreshes live: a card runs, then moves on to the next column", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  await expect(page.getByTestId("live-indicator")).toContainText("LIVE");
  const first = page.locator('[data-card-id="demo-0"]');
  await expect(first).toBeVisible({ timeout: 10_000 });
  // The story starts a run about a second in; the card then says what runs and for how long.
  const running = page.locator('[data-status="running"]').first();
  await expect(running).toBeVisible({ timeout: 10_000 });
  await expect(running).toContainText(/\d+ s/);
  await expect(running.getByTestId("stage-line")).toBeVisible();
  // Then the card of the first run moves to the next column.
  const column = () => first.evaluate((el) => el.closest("section[id^=colonne]")?.id);
  const start = await column();
  await expect.poll(column, { timeout: 15_000 }).not.toBe(start);
  await page.getByText("Latest events").click();
  await expect(page.getByTestId("live-ticker").first()).toContainText("ACME-101");
});

test("there is no separate live page: the console has no Live entry and no such route", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  await expect(page.getByRole("link", { name: "Live", exact: true })).toHaveCount(0);
  await expect(page.getByTestId("board-live-link")).toHaveCount(0);
  const response = await page.goto("/p/billing-api/live");
  expect(response?.status()).toBe(404);
});
