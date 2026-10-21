import { expect, test } from "@playwright/test";

/**
 * The policy ceiling form (S42-08), on the mock API: an administrator sets a ticket budget in the form,
 * saves, and reads it back. The mock keeps the ceiling for the length of the page (a reload would
 * forget it), so "persists" is checked by leaving /admin and coming back client-side, and by the write
 * the mock recorded on `window.__mockWrites`.
 */
test("a ticket budget set in the form is saved and read back, the network section kept", async ({ page }) => {
  await page.goto("/admin");
  await expect(page.getByTestId("policy-ceiling-form")).toBeVisible();
  const ticketL = page.getByLabel("Ticket budget L");
  await expect(ticketL).toHaveValue("");
  await ticketL.fill("45");
  await page.getByLabel("Max turns default").fill("0");
  await page.getByRole("button", { name: "Save the ceiling" }).click();
  await expect(page.getByText("Must be at least 1")).toBeVisible();
  await page.getByLabel("Max turns default").fill("50");
  await page.getByRole("button", { name: "Save the ceiling" }).click();
  await expect(page.getByText("Ceiling saved")).toBeVisible();

  const writes = await page.evaluate(() => (window as unknown as { __mockWrites?: { method: string; path: string; body: { ceiling: Record<string, unknown> } }[] }).__mockWrites ?? []);
  const put = writes.filter((w) => /\/policy-ceiling$/.test(w.path)).at(-1)!;
  expect(put.method).toBe("PUT");
  expect(put.body.ceiling.budgets).toEqual({ ticket_usd: { M: 20, L: 45 }, max_turns: { default: 50 } });
  expect(put.body.ceiling.network).toBeTruthy();

  // Away and back, without a reload: the form shows what the API now holds.
  await page.getByRole("link", { name: "All of acme" }).click();
  await page.getByRole("link", { name: "Admin", exact: true }).first().click();
  await expect(page.getByLabel("Ticket budget L")).toHaveValue("45");
  await expect(page.getByLabel("Ticket budget M")).toHaveValue("20");
});
