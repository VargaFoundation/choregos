import { expect, test } from "@playwright/test";

/**
 * The live view (S67-27), on the demo's scripted story: the workflow's lanes, a card that runs and then
 * moves on, the wall that takes the console away, and the way in from the board.
 */
test("the live view shows the workflow's lanes and a card that runs and moves on", async ({ page }) => {
  await page.goto("/p/billing-api/live");
  const board = page.getByTestId("live-board");
  await expect(board).toBeVisible();
  await expect(page.getByTestId("live-indicator")).toContainText("LIVE");
  await expect(board.locator("[data-lane]")).toHaveCount(4);
  await expect(board.locator("[data-card-id]").first()).toBeVisible();
  // The story starts a run about a second in; the card then says what runs and for how long.
  const running = board.locator('[data-status="running"]').first();
  await expect(running).toBeVisible({ timeout: 10_000 });
  await expect(running).toContainText(/\d+ s/);
  // Then the card of the first run moves to the next lane.
  const first = board.locator('[data-card-id="demo-0"]');
  const lane = () => first.evaluate((el) => el.closest("[data-lane]")?.getAttribute("data-lane"));
  const start = await lane();
  await expect.poll(lane, { timeout: 15_000 }).not.toBe(start);
  await expect(page.getByTestId("live-ticker")).toContainText("ACME-101");
});

test("the wall takes the console away, and Escape brings it back", async ({ page }) => {
  await page.goto("/p/billing-api/live");
  await page.getByRole("button", { name: "Wall mode" }).click();
  await expect(page).toHaveURL(/wall=1/);
  await expect(page.getByTestId("live-wall")).toBeVisible();
  await expect(page.getByTestId("live-board")).toHaveAttribute("data-wall", "true");
  // The console's navigation is still in the page but out of reach: inert, and under the wall.
  const board = page.getByRole("link", { name: "Board", exact: true });
  expect(await board.evaluate((el) => Boolean(el.closest("[inert]")))).toBe(true);
  expect(await board.evaluate((el) => document.elementFromPoint(el.getBoundingClientRect().x + 4, el.getBoundingClientRect().y + 4)?.closest("[data-testid=live-wall]") !== null)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("live-wall")).toHaveCount(0);
  await expect(page).not.toHaveURL(/wall=1/);
});

test("the board links to the live view", async ({ page }) => {
  await page.goto("/p/billing-api/board");
  await page.getByTestId("board-live-link").click();
  await expect(page).toHaveURL(/\/p\/billing-api\/live/);
  await expect(page.getByTestId("live-board")).toBeVisible();
});
