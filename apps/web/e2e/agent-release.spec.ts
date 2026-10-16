import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * An agent's lifecycle on the mock API (S40-02c, ADR 0071): send a revision to testing, try to promote it
 * (blocked, the checklist says why), let two runs finish, promote, roll back; the history keeps every move.
 */
type Write = { method: string; path: string; body: Record<string, unknown> };
async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

test("send to testing, be blocked, promote after two runs, roll back, read the history", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/agents/onboarding-coordinator?tab=lifecycle");
  await expect(page.getByTestId("production-card")).toContainText("v1");
  await expect(page.getByText(/v2 is saved and sits in no channel/)).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("lifecycle-1440.png"), fullPage: true });

  await page.getByLabel(/Share of work items/).fill("10");
  await page.getByRole("button", { name: "Send to testing" }).click();
  await expect(page.getByTestId("testing-card")).toContainText("v2 · 10 % of work items");
  const [sent] = await writes(page, /\/agents\/onboarding-coordinator\/releases$/);
  expect(sent?.body).toMatchObject({ channel: "candidate", version: 2, percent: 10 });

  await page.getByRole("button", { name: "Promote to production…" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByTestId("checks").locator('[data-check="candidate_runs"]')).toContainText("0 of 2 finished runs");
  await expect(dialog.getByRole("button", { name: "Release to production" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel" }).click();

  await page.evaluate(() => (window as unknown as { __mockAddCandidateRuns: (s: string, n: number) => void }).__mockAddCandidateRuns("onboarding-coordinator", 2));
  await page.getByRole("button", { name: "Promote to production…" }).click();
  const second = page.getByRole("dialog");
  const go = second.getByRole("button", { name: "Release to production" });
  await expect(go).toBeEnabled();
  await go.click();
  await expect(second.getByText("v2 is in production.")).toBeVisible();
  await second.getByRole("button", { name: "Close" }).first().click();
  await expect(page.getByTestId("channel-production").first()).toHaveText("Production v2");

  await page.getByLabel("Roll back to").selectOption("1");
  await page.getByRole("button", { name: "Roll back to v1…" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Roll back to v1", exact: true }).click();
  await expect(page.getByTestId("channel-production").first()).toHaveText("Production v1");

  await expect(page.getByTestId("release-history").locator("tbody tr")).toHaveCount(4);
});

for (const width of [1440, 390]) {
  test(`the Lifecycle tab at ${width} px: no sideways scroll, no axe violation`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/agents/onboarding-coordinator?tab=lifecycle");
    await expect(page.getByTestId("release-pipeline")).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: test.info().outputPath(`lifecycle-${width}.png`), fullPage: true });
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]).analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
}
