import { expect, test, type Page } from "@playwright/test";

/**
 * A new agent on a full page (S39-05). The drawer docked the builder in 672 px, of which the form
 * got about 350: the page must be wide and usable at 1440, 1280 and on a phone. In demo mode no
 * request leaves the page: the mock API records every write on `window.__mockWrites`.
 */
type Write = { method: string; path: string; body: Record<string, unknown> };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

const DESCRIPTION = "Writes the release notes of a project from its merged pull requests.";

async function toTheForm(page: Page) {
  await page.goto("/agents/new");
  await page.getByLabel("Name").fill("Release notes writer");
  await page.getByLabel("What should it do?").fill(DESCRIPTION);
  await page.getByRole("button", { name: "Start from a blank form" }).click();
  const builder = page.getByTestId("agent-builder");
  await expect(builder).toBeVisible();
  return builder;
}

async function box(locator: ReturnType<Page["getByTestId"]>) {
  const found = await locator.boundingBox();
  if (!found) throw new Error("not on the page");
  return found;
}

test("'New agent' opens a full page whose builder is wide, and 'Save as draft' reaches the API", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/agents");
  await page.getByRole("link", { name: "New agent" }).click();
  await expect(page).toHaveURL(/\/agents\/new$/);
  await expect(page.getByRole("heading", { level: 1, name: "New agent" })).toBeVisible();
  // Step 1 is short: no limits, no budget.
  await expect(page.getByLabel("Max turns")).toHaveCount(0);

  const builder = await toTheForm(page);
  await expect(page).toHaveURL(/step=configure/);
  // The drawer capped it at 672 px.
  expect((await box(builder)).width).toBeGreaterThanOrEqual(1100);
  await expect(builder.getByLabel("Slug")).toHaveValue("release-notes-writer");

  await builder.getByRole("button", { name: "Save as draft" }).click();
  await expect(page).toHaveURL(/\/agents\/release-notes-writer$/);
  const [created] = await writes(page, /\/orgs\/[^/]+\/agents$/);
  expect(created?.method).toBe("POST");
  expect(created?.body).toMatchObject({ slug: "release-notes-writer", description: DESCRIPTION, lifecycle: "draft" });
});

test("'Create and activate' sends an active agent", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const builder = await toTheForm(page);
  await builder.getByRole("button", { name: "Create and activate" }).click();
  await expect(page).toHaveURL(/\/agents\/release-notes-writer$/);
  const [created] = await writes(page, /\/orgs\/[^/]+\/agents$/);
  expect(created?.body).toMatchObject({ lifecycle: "active" });
});

test("at 1280 px the trial and the costs sit beside the form, and the advanced sections are folded", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const builder = await toTheForm(page);
  const form = await box(builder.getByRole("form", { name: "New agent" }));
  const panel = await box(page.getByTestId("panneau-lateral"));
  expect(panel.x).toBeGreaterThanOrEqual(form.x + form.width);
  // The form is not the 350 px of the drawer.
  expect(form.width).toBeGreaterThanOrEqual(480);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280);
  await expect(builder.locator("details[open]")).toHaveCount(0);
  // A folded section opens on its summary and its fields become usable.
  await builder.getByText("Limits and budget").click();
  await builder.getByLabel("Max turns").fill("12");
  await expect(builder.getByText("12 turns", { exact: true })).toBeVisible();
});

test("at 390 px the page does not scroll sideways and the panel falls under the form", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/agents/new");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const builder = await toTheForm(page);
  const form = await box(builder.getByRole("form", { name: "New agent" }));
  const panel = await box(page.getByTestId("panneau-lateral"));
  expect(form.width).toBeGreaterThanOrEqual(300);
  expect(panel.y).toBeGreaterThanOrEqual(form.y + form.height - 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  // Opening the widest section does not push the page sideways either.
  await builder.getByText("Limits and budget").click();
  await builder.getByText("Eval suite").click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(builder.getByRole("button", { name: "Save as draft" })).toBeVisible();
});

test("'Draft it for me' pre-fills the form, then 'Create and activate' sends the drafted agent", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/agents/new");
  await page.getByLabel("Name").fill("Release notes writer");
  await page.getByLabel("What should it do?").fill(DESCRIPTION);
  await page.getByRole("button", { name: "Draft it for me" }).click();
  const builder = page.getByTestId("agent-builder");
  await expect(builder).toBeVisible();
  await expect(builder.getByLabel("Instructions")).not.toHaveValue("");
  await expect(page.getByText("dropped skill `ghost`")).toBeVisible();
  await expect(builder.getByTestId("banc-d-essai").getByLabel("Sample title", { exact: true })).toHaveValue("Release notes for 1.2");
  expect((await writes(page, /\/agents$/)).length).toBe(0);
  await builder.getByRole("button", { name: "Create and activate" }).click();
  await expect(page).toHaveURL(/\/agents\/release-notes-writer$/);
  const [created] = await writes(page, /\/orgs\/[^/]+\/agents$/);
  expect(created?.body).toMatchObject({ lifecycle: "active", description: DESCRIPTION });
  const spec = created?.body.spec as { instructions: string; skills: { slug: string }[] };
  expect(spec.instructions).toContain("release notes writer");
  expect(spec.skills).toEqual([{ slug: "onboarding-procedure" }]);
});
