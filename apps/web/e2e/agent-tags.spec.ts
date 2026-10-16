import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/** Agent tags on mocks (S40-10): filter chips write the address, the details card edits the tags. */
test("a tag chip filters the registry and writes the address", async ({ page }) => {
  await page.goto("/agents");
  await expect(page.getByTestId("agent-developer")).toBeVisible();
  await page.getByTestId("tag-filter").getByRole("button", { name: /^madr \(1\)/ }).click();
  await expect(page).toHaveURL(/\/agents\?tag=madr$/);
  await expect(page.getByTestId("agent-architect")).toBeVisible();
  await expect(page.getByTestId("agent-developer")).toHaveCount(0);
  // a filtered list is a link
  await page.reload();
  await expect(page.getByTestId("agent-architect")).toBeVisible();
  await expect(page.getByTestId("agent-onboarding-coordinator")).toHaveCount(0);
});

test("the agent's page shows its tags and edits them without publishing a version", async ({ page }) => {
  await page.goto("/agents/architect?tab=definition");
  const header = page.getByRole("list", { name: "Tags" }).first();
  await expect(header.getByRole("link", { name: "madr" })).toBeVisible();
  await page.locator("summary", { hasText: "About" }).click();
  await page.getByRole("button", { name: "Edit details" }).click();
  const input = page.getByRole("combobox", { name: "Tags" });
  await input.fill("ADR");
  await input.press("Enter");
  await page.getByRole("button", { name: "Save details" }).click();
  await expect(page.getByTestId("agent-details").getByText("adr", { exact: true })).toBeVisible();
  await expect(page.getByTestId("agent-details").getByText("madr", { exact: true })).toBeVisible();
  // the new tag is a link in the header: it opens the registry filtered by it (the mock lives in memory)
  await page.getByRole("list", { name: "Tags" }).first().getByRole("link", { name: "adr", exact: true }).click();
  await expect(page).toHaveURL(/\/agents\?tag=adr$/);
  await expect(page.getByTestId("agent-architect")).toBeVisible();
  await expect(page.getByTestId("agent-developer")).toHaveCount(0);
});

test("the filtered registry has no accessibility violation", async ({ page }) => {
  await page.goto("/agents?tag=madr");
  await expect(page.getByTestId("agent-architect")).toBeVisible();
  const result = await new AxeBuilder({ page }).analyze();
  expect(result.violations).toEqual([]);
});
