import { expect, test } from "@playwright/test";

/** The Overview tab of an agent says its five components (S60-07, ADR 0127), on the mock API. */
test("the Overview tab shows the composition of the current version, with links to Definition and Access", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/agents/onboarding-coordinator");
  const card = page.getByTestId("agent-composition");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("composition-model")).toBeVisible();
  await expect(card.getByTestId("composition-knowledge")).toBeVisible();
  await expect(card.getByTestId("composition-complete").or(card.getByTestId("composition-inherits"))).toBeVisible();
  await card.getByTestId("composition-access-link").click();
  await expect(page.getByTestId("agent-access")).toBeVisible();
  expect(errors).toEqual([]);
});

/** The product owner's review of 2026-11-12: an agent with no declared tool must not look empty. */
test("the Composition card says which model a by_size agent resolves to, what its runtime brings, and links its knowledge", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto("/agents/developer");
  const card = page.getByTestId("agent-composition");
  await expect(card.getByTestId("composition-complete")).toBeVisible();
  await expect(card.getByTestId("composition-model")).toContainText("profile:by_size");
  await expect(card.getByTestId("composition-model-platform/strong")).toContainText("reasoning model");
  await expect(card.getByTestId("composition-model-platform/standard").getByRole("img", { name: "Anthropic" })).toBeVisible();
  await expect(card.getByTestId("composition-builtin-shell")).toBeVisible();
  await expect(card.getByTestId("composition-tools-catalogue")).toContainText("none declared");
  await expect(card.getByTestId("composition-kb-architecture-framework").getByRole("link")).toHaveAttribute("href", "/knowledge/architecture-framework");
  await expect(card.getByTestId("composition-limits")).toContainText("120 a run");
  await expect(card.getByTestId("composition-limits")).toContainText("$12.00 a run");
  if (process.env.COMPOSITION_SHOT) await card.screenshot({ path: process.env.COMPOSITION_SHOT });
  expect(errors).toEqual([]);
});
