import { expect, test } from "@playwright/test";

/**
 * The Access tab of an agent (S60-03, ADR 0127), on the mock API: the five components of what a version
 * reaches in a project, each with its origin, and a banner naming what the version leaves to the project.
 */
test("the Access tab says what the version reaches in a project, with the origin of each part", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/agents/onboarding-coordinator?tab=access");
  const access = page.getByTestId("agent-access");
  await expect(access).toBeVisible();
  await expect(page.getByLabel("Project")).toBeVisible();
  await expect(page.getByTestId("access-inherits")).toContainText("model, memory");
  await expect(access.getByTestId("access-model")).toContainText("platform/standard");
  await expect(access.getByTestId("tool-catalogue-shell").getByTestId("origin-refused")).toBeVisible();
  await expect(access.getByTestId("tool-catalogue-shell")).toContainText("the project does not open shell");
  await expect(access.getByTestId("source-web")).toContainText("fetch");
  await expect(access.getByTestId("access-memories")).toContainText("project:shared");
  expect(errors).toEqual([]);
});
