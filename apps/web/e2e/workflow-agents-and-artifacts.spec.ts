import { expect, test } from "@playwright/test";

/**
 * A workflow shows who carries each step and what each step produces (S39-13, Vincent's question:
 * "I do not see the agents nor the artifacts"). Demo mocks: the `dev-complex` template as the API
 * renders it.
 */
test("the process view names the registry agent of a step and links to it", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/dev-complex/steps");
  const step = page.getByTestId("process-step-t-implement");
  const link = step.getByRole("link", { name: "Developer" });
  await expect(link).toBeVisible();
  await expect(step).toContainText("v1 · latest");
  await expect(step).toContainText("changes on the work item's branch");
  await expect(step).toContainText("test_report — from Tested (t-test)");
  await link.click();
  await expect(page).toHaveURL(/\/agents\/developer$/);
});

test("the map says what each box produces and lists the artifacts, highlighting who writes and reads one", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/workflows/dev-complex");
  await expect(page.getByText("→ spec_markdown, allowed_paths")).toBeVisible();
  const table = page.getByRole("table", { name: "Artifacts" });
  const row = table.getByTestId("artifact-row-test_report");
  await expect(row).toBeVisible();
  await row.hover();
  await expect(page.getByTestId("step-t-test")).toHaveAttribute("data-flow", "produces");
  await expect(page.getByTestId("step-t-implement")).toHaveAttribute("data-flow", "reads");
  await page.mouse.move(0, 0);
  await expect(page.getByTestId("step-t-test")).not.toHaveAttribute("data-flow", /.+/);
  // A chosen box opens its card above the editor.
  await page.getByTestId("step-t-specify").click();
  await expect(page.getByTestId("details-t-specify")).toContainText("Spec writer");
});
