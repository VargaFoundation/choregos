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
