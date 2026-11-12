import { expect, test, type Page } from "@playwright/test";

/**
 * Outposts (ADR 0147) on the demo build: the page an administrator manages them from. No request
 * leaves the page; the mock API records every write on `window.__mockWrites`. The demo has no daemon:
 * an instance "enrols" by itself a moment after a token is issued.
 */
type Write = { method: string; path: string; body: Record<string, unknown> | null };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

test("the administration offers Outposts, and lists them with their state and their presence", async ({ page }) => {
  await page.goto("/admin");
  await page.getByRole("link", { name: "Outposts" }).first().click();
  await expect(page).toHaveURL(/\/admin\/outposts$/);
  const factory = page.getByTestId("outpost-factory-floor");
  await expect(factory).toContainText("Active");
  await expect(factory).toContainText("1 approved");
  await expect(factory).toContainText("1 pending");
  await expect(factory).toContainText("Online");
  const cold = page.getByTestId("outpost-cold-store");
  await expect(cold).toContainText("Paused");
  await expect(cold).toContainText("Last seen");
});

test("a paused outpost says so, and is resumed", async ({ page }) => {
  await page.goto("/admin/outposts/cold-store");
  await expect(page.getByTestId("outpost-paused")).toContainText("This outpost is paused");
  await page.getByRole("button", { name: "Resume" }).click();
  await expect(page.getByTestId("outpost-paused")).toHaveCount(0);
  await expect(page.getByTestId("outpost-summary")).toContainText("Active");
  expect(await writes(page, /\/outposts\/cold-store\/resume$/)).toHaveLength(1);
});

test("a pending instance is approved only once its fingerprint is confirmed, and the request carries it", async ({ page }) => {
  await page.goto("/admin/outposts/factory-floor");
  const pending = page.getByRole("list", { name: "Instances waiting for approval" }).getByRole("listitem").first();
  const fingerprint = (await pending.getByTestId("instance-fingerprint").innerText()).trim();
  expect(fingerprint).toMatch(/^SHA256:[A-Za-z0-9_-]{43}$/);
  const approve = pending.getByRole("button", { name: "Approve the instance" });
  await expect(approve).toBeDisabled();
  await pending.getByRole("checkbox").check();
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect(page.getByRole("list", { name: "Instances waiting for approval" })).toHaveCount(0);
  const [sent] = await writes(page, /\/instances\/[^/]+\/approve$/);
  expect(sent?.body).toEqual({ fingerprint });
  await expect(page.getByTestId("outpost-summary")).toContainText("2 approved, 0 pending");
});

test("enrolling shows the token once with the policy file's lines, and the instance arrives pending", async ({ page }) => {
  await page.goto("/admin/outposts/cold-store");
  const card = page.getByTestId("outpost-enrol");
  await card.getByRole("button", { name: "Create an enrolment token" }).click();
  const shown = card.getByTestId("outpost-enrolment");
  const token = (await shown.getByTestId("outpost-enrolment-token").innerText()).trim();
  expect(token).toMatch(/^choe_[0-9a-f]{32}[A-Za-z0-9_-]{43}$/);
  const lines = (await shown.getByTestId("outpost-policy-lines").innerText()).trim().split("\n");
  expect(lines[0]).toBe("control_plane: https://outposts.example.com");
  expect(lines[1]).toMatch(/^door_keys: \["[A-Za-z0-9_-]{43}", "[A-Za-z0-9_-]{43}"\]$/);
  await expect(shown.getByTestId("outpost-enrol-command")).toHaveText(`choregos-outpost enrol --token ${token}`);
  // The page looks for the instance while the token is on screen.
  const waiting = page.getByRole("list", { name: "Instances waiting for approval" });
  await expect(waiting.getByRole("listitem")).toHaveCount(1, { timeout: 15_000 });
  await shown.getByRole("button", { name: "Done, hide the token" }).click();
  await expect(page.getByText(token)).toHaveCount(0);
});

test("revoking is said to be final before it is done, and a deletion shows the API's refusal", async ({ page }) => {
  await page.goto("/admin/outposts/factory-floor");
  const summary = page.getByTestId("outpost-summary");
  await summary.getByRole("button", { name: "Delete" }).click();
  await summary.getByRole("button", { name: "Delete the outpost" }).click();
  await expect(summary.getByRole("alert")).toContainText("is active: revoke it first");
  await summary.getByRole("button", { name: "Cancel" }).click();

  await summary.getByRole("button", { name: "Revoke" }).click();
  await expect(summary.getByTestId("confirmation")).toContainText("This is final");
  expect(await writes(page, /\/outposts\/factory-floor\/revoke$/)).toHaveLength(0);
  await summary.getByRole("button", { name: "Revoke the outpost" }).click();
  await expect(page.getByTestId("outpost-revoked")).toContainText("That is final");
  await expect(page.getByTestId("outpost-enrol")).toHaveCount(0);
});

test("the recent calls read as states and refusal codes, never as a payload", async ({ page }) => {
  await page.goto("/admin/outposts/factory-floor");
  const calls = page.getByTestId("outpost-calls");
  const table = calls.getByRole("table");
  await expect(table).toContainText("In doubt");
  await expect(table).toContainText("write_denied");
  await expect(table).not.toContainText("in_doubt");
  await calls.getByLabel("State").selectOption("refused");
  await expect(table.getByRole("row")).toHaveCount(3);
  await expect(table).toContainText("tool_not_allowed");
  await expect(table).not.toContainText("Answered");
});
