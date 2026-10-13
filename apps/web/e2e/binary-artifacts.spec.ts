import { expect, test } from "@playwright/test";

/**
 * Binary artifacts (S31-06, ADR 0049): a PDF opens in the browser's viewer, a video in a player,
 * an office file is downloaded — never rendered by the console.
 */
test("a PDF is previewed, a video gets a player, an office file is downloaded (S31-06)", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  const tabs = page.getByRole("tablist", { name: "artifacts" });

  await tabs.getByRole("tab", { name: "quarterly_report" }).click();
  const pdf = page.getByTestId("artifact-pdf");
  await expect(pdf).toBeVisible();
  await expect(pdf).toHaveAttribute("src", /^blob:/);
  await expect(pdf).toHaveAttribute("title", "quarterly_report (PDF)");

  await tabs.getByRole("tab", { name: "demo" }).click();
  const video = page.getByTestId("artifact-video");
  await expect(video).toBeVisible();
  await expect(video).toHaveAttribute("controls", "");
  await expect(video.locator("source")).toHaveAttribute("type", "video/mp4");
  await expect(video.locator("source")).toHaveAttribute("src", /^blob:/);

  await tabs.getByRole("tab", { name: "board_deck" }).click();
  await expect(page.getByTestId("artifact-download-only")).toContainText("office file is downloaded");
  await expect(page.locator("iframe, video, [data-testid=artifact-image]")).toHaveCount(0);
  const download = page.getByTestId("artifact-download");
  await expect(download).toHaveAttribute("download", "board_deck.pptx");
  const [file] = await Promise.all([page.waitForEvent("download"), download.click()]);
  expect(file.suggestedFilename()).toBe("board_deck.pptx");
});
