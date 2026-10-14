import { expect, test } from "@playwright/test";

/**
 * The work item's artifacts (S31-04, ADR 0049): one tab per output, a viewer for what the console
 * can show safely, a download for everything, and the runs that produced and read each revision.
 */
test("the artifacts of a work item: markdown, SARIF, image, download, producer and readers (S31-04)", async ({
  page,
}) => {
  await page.goto("/p/billing-api/items/w1");
  const tabs = page.getByRole("tablist", { name: "artifacts" });
  await expect(tabs.getByRole("tab")).toHaveText([
    "architecture.png",
    "board_deck",
    "budget_summary",
    "demo",
    "quarterly_report",
    "security_report",
    "spec_markdown",
  ]);

  // An image, decoded by the browser from the bytes the API served.
  const image = page.getByTestId("artifact-image");
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);

  // Markdown, rendered as elements; the latest revision first, then the one the next step read.
  await tabs.getByRole("tab", { name: "spec_markdown" }).click();
  const panel = page.getByRole("tabpanel");
  await expect(panel.getByRole("heading", { name: "Acceptance" })).toBeVisible();
  await expect(panel.getByTestId("artifact-producer")).toContainText("Produced by refine · attempt 1 · agent analyst@3");
  await expect(panel.getByTestId("artifact-download")).toHaveAttribute("download", "spec_markdown.md");
  await panel.getByLabel("revision").selectOption("1");
  await expect(panel.getByTestId("markdown-view")).toContainText("Deduct credit notes from the order total.");
  const reader = panel.getByTestId("artifact-reader");
  await expect(reader).toHaveText("implement");
  await expect(reader).toHaveAttribute("href", "/p/billing-api/runs/r3");

  // A SARIF report, as a table.
  await tabs.getByRole("tab", { name: "security_report" }).click();
  const results = page.getByRole("table", { name: "sarif results" });
  await expect(results.locator("tbody tr")).toHaveCount(2);
  await expect(results.locator('tr[data-level="error"]')).toContainText("src/orders/repository.py:88");

  // Content that no longer matches its digest is refused, not shown.
  await tabs.getByRole("tab", { name: "budget_summary" }).click();
  await expect(page.getByTestId("artifact-refused")).toContainText("no longer matches its digest");
  await expect(page.getByTestId("artifact-download")).toHaveCount(0);
});
