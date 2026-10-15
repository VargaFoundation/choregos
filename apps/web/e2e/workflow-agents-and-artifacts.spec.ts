import { expect, test } from "@playwright/test";

/**
 * A workflow shows who carries each step and what each step produces (S39-13, Vincent's question:
 * "I do not see the agents nor the artifacts"). Demo mocks: the `dev-complex` template as the API
 * renders it.
 */
test("the process view names the registry agent of a step and links to it", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/dev-complex");
  const etape = page.getByTestId("process-step-t-implement");
  const lien = etape.getByRole("link", { name: "Developer" });
  await expect(lien).toBeVisible();
  await expect(etape).toContainText("v1 · latest");
  await expect(etape).toContainText("changes on the work item's branch");
  await expect(etape).toContainText("test_report — from Tested (t-test)");
  await lien.click();
  await expect(page).toHaveURL(/\/agents\/developer$/);
});

test("the map says what each box produces and lists the artifacts, highlighting who writes and reads one", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/workflows/dev-complex/map");
  await expect(page.getByText("→ spec_markdown, allowed_paths")).toBeVisible();
  const table = page.getByRole("table", { name: "Artifacts" });
  const ligne = table.getByTestId("artefact-test_report");
  await expect(ligne).toBeVisible();
  await ligne.hover();
  await expect(page.getByTestId("etape-t-test")).toHaveAttribute("data-flow", "produces");
  await expect(page.getByTestId("etape-t-implement")).toHaveAttribute("data-flow", "reads");
  await page.mouse.move(0, 0);
  await expect(page.getByTestId("etape-t-test")).not.toHaveAttribute("data-flow", /.+/);
  // A chosen box opens its card above the editor.
  await page.getByTestId("etape-t-specify").click();
  await expect(page.getByTestId("details-t-specify")).toContainText("Spec writer");
});
