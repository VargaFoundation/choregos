import { expect, test } from "@playwright/test";

/** The Platform page (S42-07), on the mock API: a backend is disabled with a reason, executors are read-only. */
test("a coding-agent backend is disabled with a reason; executors have no control", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/admin/platform");
  await expect(page.getByText(/coding agent CLI that the runner image ships/)).toBeVisible({ timeout: 20_000 });
  const claude = page.getByTestId("backend-claude-code");
  await expect(claude.getByText(/24\/24 passed/)).toBeVisible();
  await claude.getByRole("button", { name: "Disable claude-code" }).click();
  await expect(claude.getByRole("button", { name: "Confirm disable" })).toBeDisabled();
  await claude.getByLabel("Why disable claude-code?").fill("cost spike");
  await claude.getByRole("button", { name: "Confirm disable" }).click();
  await expect(claude.getByText("Disabled because: cost spike")).toBeVisible();
  await expect(page.getByTestId("executor-agent_sandbox").getByRole("button")).toHaveCount(0);
  await expect(page.getByText(/Change it in the deployment/)).toBeVisible();
});
