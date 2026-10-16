import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * An agent's Definition, editing and History on mocks (S40-12, ADR 0076), with the `architect` agent: three
 * versions, the first pinned by billing-api. The mock plays the API's refusals (stale base, no change).
 */
type Write = { method: string; path: string; body: Record<string, unknown> };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

test("the Definition tab reads one version; editing, publishing, comparing and restoring", async ({ page }) => {
  await page.goto("/agents/architect?tab=definition");
  const bar = page.getByTestId("version-bar");
  await expect(bar).toContainText("v3");
  await expect(bar).toContainText("Current");
  await expect(bar.getByTestId("version-message")).toContainText("Tighten the run budget");
  await expect(bar.getByTestId("pinned")).toContainText("billing-api (v1)");
  await expect(page.getByRole("textbox")).toHaveCount(0); // read-only until Edit

  const axeRead = await new AxeBuilder({ page }).analyze();
  expect(axeRead.violations).toEqual([]);

  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await expect(page).toHaveURL(/edit=1/);
  const editor = page.getByTestId("agent-builder");
  await expect(editor.getByRole("button", { name: "Publish v4" })).toBeDisabled();
  await editor.getByRole("textbox", { name: "Instructions" }).fill("You write ONE ADR.\nStop at the status.");
  await page.getByRole("tab", { name: /^Changes \(1\)/ }).click();
  await expect(page.getByTestId("change-instructions")).toBeVisible();
  const axeEdit = await new AxeBuilder({ page }).analyze();
  expect(axeEdit.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
  await editor.getByLabel("What changed and why").fill("Shorter instructions");
  await editor.getByRole("button", { name: "Publish v4" }).click();
  await expect(page).not.toHaveURL(/edit=1/);
  await expect(page.getByRole("status")).toContainText("v4 published");
  const [publish] = await writes(page, /\/versions\/publish$/);
  expect(publish?.body).toMatchObject({ message: "Shorter instructions", base_version: 3 });

  await page.getByRole("link", { name: "History" }).click();
  const history = page.getByTestId("history");
  await expect(history.getByTestId("revision-4")).toContainText("Shorter instructions");
  await expect(history.getByTestId("revision-4")).toContainText("Current");
  const axeHistory = await new AxeBuilder({ page }).analyze();
  expect(axeHistory.violations).toEqual([]);

  await history.getByTestId("revision-1").getByRole("checkbox").check();
  await history.getByTestId("revision-4").getByRole("checkbox").check();
  await page.getByRole("button", { name: "Compare v1 and v4" }).click();
  await expect(page).toHaveURL(/compare=1\.\.4/);
  await expect(page.getByTestId("compare").getByTestId("change-instructions")).toBeVisible();

  await history.getByTestId("revision-1").getByRole("button", { name: "Restore v1" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Restore as v5" }).click();
  await expect(page).toHaveURL(/tab=history/);
  await expect(page.getByTestId("revision-5")).toContainText("Restored from v1");
  await expect(page.getByTestId("revision-5")).toContainText("Restore v1");
  const [restore] = await writes(page, /\/versions\/1\/restore$/);
  expect(restore?.body).toMatchObject({ base_version: 4 });
});

test("publishing an unchanged definition says nothing changed", async ({ page }) => {
  await page.goto("/agents/architect?tab=definition&edit=1");
  const editor = page.getByTestId("agent-builder");
  await editor.getByLabel("What changed and why").fill("Nothing really");
  await editor.getByRole("button", { name: "Publish v4" }).click();
  await expect(page.getByText("Nothing changed since v3.")).toBeVisible();
});

test("an older version is read with its banner, and the former Versions address opens History", async ({ page }) => {
  await page.goto("/agents/architect?tab=definition&version=1");
  await expect(page.getByText("You are reading v1")).toBeVisible();
  await expect(page.getByRole("button", { name: "Restore as v4" })).toBeVisible();
  await page.goto("/agents/architect?tab=versions");
  await expect(page.getByTestId("history")).toBeVisible();
});
