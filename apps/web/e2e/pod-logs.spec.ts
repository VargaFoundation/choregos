import { expect, test } from "@playwright/test";

/**
 * The run page's "pod logs" tab (S36-03): what happened to the pod, next to what the agent said.
 */
test("a running run shows its live pod log tail, secrets masked", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r3");
  const tabs = page.getByRole("tablist", { name: "run output" });
  // The ACP journal stays the default tab.
  await expect(tabs.getByRole("tab", { name: "ACP journal" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("live-log")).toBeVisible();

  await tabs.getByRole("tab", { name: "pod logs" }).click();
  await expect(tabs.getByRole("tab", { name: "pod logs" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("pod-logs")).toHaveAttribute("data-source", "live");
  await expect(page.getByTestId("pod-logs-source")).toHaveText("live");
  const lines = page.getByTestId("pod-log-lines");
  await expect(lines).toContainText("acp: session started (claude-code)");
  await expect(lines).toContainText("GITHUB_TOKEN=[REDACTED]");
  await expect(page.getByTestId("live-log")).toHaveCount(0);
});

test("a pod killed before the agent spoke says why: exit code, reason, last lines", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r-oom");
  await page.getByRole("tab", { name: "pod logs" }).click();
  await expect(page.getByTestId("pod-logs")).toHaveAttribute("data-source", "archive");
  await expect(page.getByTestId("pod-logs-source")).toHaveText("archived");
  await expect(page.getByTestId("pod-exit-code")).toHaveText("exit 137");
  await expect(page.getByTestId("pod-reason")).toHaveText("OOMKilled");
  await expect(page.getByTestId("pod-log-lines")).toContainText("Killed");
  await expect(page.getByText("last 3 lines · secrets masked")).toBeVisible();
});
