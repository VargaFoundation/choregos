import { expect, test } from "@playwright/test";

/** Watch, star and fork counters of an agent on mocks (ADR 0152). */
test("the header shows the counters and a star moves the count", async ({ page }) => {
  await page.goto("/agents/developer");
  const bar = page.getByTestId("social-bar");
  await expect(bar.getByTestId("watchers")).toHaveText("7");
  await expect(bar.getByTestId("stars")).toHaveText("12");
  await expect(bar.getByTestId("forks-count")).toContainText("1");
  await bar.getByTestId("star").click();
  await expect(bar.getByTestId("stars")).toHaveText("13");
  await expect(bar.getByTestId("star")).toHaveText(/Starred/);
  await bar.getByTestId("watch").click();
  await page.getByRole("menuitemradio", { name: /Releases only/ }).click();
  await expect(bar.getByTestId("watch")).toContainText("Watching");
  await expect(bar.getByTestId("watchers")).toHaveText("8");
  await page.screenshot({ path: process.env.SOCIAL_SHOT ?? "test-results/agent-social.png" });
});

test("the Forks tab lists the visible forks", async ({ page }) => {
  await page.goto("/agents/developer");
  await page.getByTestId("forks-count").click();
  await expect(page).toHaveURL(/tab=forks$/);
  await expect(page.getByTestId("fork-developer-billing-api")).toContainText("v1");
});

test("the registry shows the counters, sorts by stars and keeps the starred", async ({ page }) => {
  await page.goto("/agents");
  await expect(page.getByTestId("social-developer")).toHaveText("7 / 12 / 1");
  await page.getByTestId("sort-stars").click();
  const first = page.getByTestId("registry").locator("tbody tr").first();
  await expect(first).toHaveAttribute("data-testid", "agent-developer");
  await page.getByTestId("starred-chip").click();
  await expect(page.getByTestId("empty")).toHaveCount(0);
  await expect(page.getByTestId("agent-developer")).toHaveCount(0);
});

test("the inbox lists what happened to the agents you watch", async ({ page }) => {
  await page.goto("/inbox");
  const card = page.getByTestId("watched-agents");
  await expect(card).toContainText("moved production v2");
  await card.getByRole("button", { name: "Mark all read" }).click();
  await expect(card).toContainText("0 unread");
});
