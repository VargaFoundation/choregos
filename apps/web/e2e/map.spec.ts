import { expect, test } from "@playwright/test";

/**
 * The flat map reads without zoom (S21-07): eighteen states, where the former lane map
 * overflowed and opened at zoom 0.8 with an overview (review of 07/10: "not very readable").
 */
test("a map of eighteen states reads without zoom: one column, no overflow, no overlap, readable titles", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/workflows/release-full?view=list");
  const diagram = page.getByTestId("workflow-graph");
  await expect(diagram.locator("[data-state-id]")).toHaveCount(18);
  await page.evaluate(() => document.fonts.ready);
  const measure = await page.evaluate(() => {
    const boxes = [...document.querySelectorAll<HTMLElement>('[aria-label="Nominal path"] > li [data-state-id]')].map((el) =>
      el.closest("div")!.getBoundingClientRect(),
    );
    const titles = [...document.querySelectorAll<HTMLElement>("[data-state-id] [data-title]")].map((el) =>
      parseFloat(getComputedStyle(el).fontSize),
    );
    return {
      width: document.documentElement.scrollWidth,
      window: document.documentElement.clientWidth,
      lefts: [...new Set(boxes.map((r) => Math.round(r.left)))],
      overlaps: boxes.slice(1).filter((r, i) => r.top < boxes[i]!.bottom).length,
      titles: titles.length,
      smallestTitle: Math.min(...titles),
    };
  });
  expect(measure.width).toBeLessThanOrEqual(measure.window);
  expect(measure.lefts).toHaveLength(1);
  expect(measure.overlaps).toBe(0);
  // One title per state: without them, Math.min() would return Infinity and the next assertion would pass vacuously.
  expect(measure.titles).toBe(18);
  expect(measure.smallestTitle).toBeGreaterThanOrEqual(13);
  await expect(diagram.getByRole("list", { name: "Off the main path" }).locator(":scope > li")).toHaveCount(5);
});

test("the panel stays within reach when the map is scrolled down", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/p/billing-api/workflows/release-full?view=list");
  await page.getByTestId("state-verified_prod").click();
  await expect(page.getByTestId("state-panel")).toBeInViewport();
  await page.mouse.wheel(0, 3000);
  await expect(page.getByTestId("state-panel")).toBeInViewport();
});

test("Tab reaches an arrow, Enter opens its transition's panel", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/release-full?view=list");
  await page.getByTestId("state-ready").focus();
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("transition-t-implement")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("transition-panel")).toBeVisible();
  await expect(page.getByText("Transition t-implement", { exact: true })).toBeVisible();
});

test("the map opens as a snake, without overflowing, and a clicked step opens its transition (S22-05)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/workflows/release-full");
  const diagram = page.getByTestId("serpentine-map");
  await expect(diagram).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await diagram.getByTestId("step-t-implement").click();
  await expect(page.getByTestId("transition-panel")).toBeVisible();
  await page.getByRole("button", { name: "List" }).click();
  await expect(page).toHaveURL(/view=list/);
  await expect(page.getByTestId("workflow-graph")).toBeVisible();
});

test("from a step chosen on the diagram, Connect to… opens the transition form from the keyboard (S55-13)", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/release-full");
  await page.getByTestId("serpentine-map").getByTestId("step-t-implement").focus();
  await page.keyboard.press("Enter");
  await page.getByTestId("map-panels").getByRole("button", { name: "Connect to…" }).focus();
  await page.keyboard.press("Enter");
  const form = page.getByTestId("drawn-transition").getByRole("form", { name: /^New transition from / });
  await expect(form).toBeVisible();
  await expect(form.getByRole("combobox").first()).toBeFocused();
});
