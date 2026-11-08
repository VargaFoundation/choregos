import { expect, test } from "@playwright/test";

/**
 * What a person reads and touches (S23-11, second pass of 10/08): states in words, not in codes;
 * a visible label above every input field; 44 px for a finger; a table that scrolls shows it.
 */
test("a state reads in words, and an engine sentence has no backticks", async ({ page }) => {
  await page.goto("/p/billing-api/trains");
  await expect(page.locator("main")).toContainText("Rolled back");
  await expect(page.locator("main")).not.toContainText("rolled_back");
  await expect(page.locator("main")).not.toContainText("awaiting_approval");

  await page.goto("/p/billing-api/workflows/default-simple/steps");
  const step = page.getByRole("list", { name: "Steps of the process" });
  await expect(step).toContainText("the agent refiner");
  await expect(step).not.toContainText("`");
  await expect(step.locator("code").first()).toHaveText("refiner");
});

const FIELDS: [string, string][] = [
  ["/account", "Token name"],
  ["/admin/members", "Member e-mail"],
  ["/admin/audit", "Actor"],
  ["/p/billing-api/memory", "Search the memory"],
  ["/p/billing-api/trains", "Freeze reason"],
  ["/p/billing-api/runs/r3", "Filter the journal"],
  ["/inbox", "Reason for sending back"],
];

test("every input field carries a visible label, not only a placeholder", async ({ page }) => {
  for (const [path, label] of FIELDS) {
    await page.goto(path);
    await expect(page.getByText(label, { exact: true }).first(), `${path}: ${label}`).toBeVisible();
    await expect(page.getByLabel(label, { exact: true }).first()).toBeVisible();
  }
});

test.describe("on a finger", () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });

  test("a small button is 44 px tall, and a table that scrolls carries its shadow", async ({ page }) => {
    await page.goto("/admin/members");
    const button = page.getByRole("button", { name: /^Remove / }).first();
    expect((await button.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    const container = page.getByRole("table").first().locator("xpath=..");
    expect(await container.evaluate((e) => e.scrollWidth > e.clientWidth)).toBe(true);
    expect(await container.evaluate((e) => getComputedStyle(e).backgroundImage)).toContain("radial-gradient");
  });
});
