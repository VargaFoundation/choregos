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
  // A connector of the demo routes to it (ADR 0149): the API refuses that first, and names it.
  await expect(summary.getByRole("alert")).toContainText("still has connectors that route to it");
  await expect(summary.getByRole("alert")).toContainText("tracker-onsite");
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

test("a connector at an outpost: the outpost and its alias are chosen, and the request carries no URL and no secret", async ({ page }) => {
  await page.goto("/admin/connectors");
  await expect(page.getByTestId("route-tracker-onsite")).toContainText("at outpost: factory-floor/tracker");
  const form = page.locator("#declare");
  await form.getByPlaceholder("entra-acme").fill("warehouse-plant");
  await form.getByLabel("Type").selectOption("mcp/mcp-outpost");
  const fields = form.getByTestId("outpost-connector-fields");
  await expect(fields).toContainText("No URL and no secret");
  await fields.getByLabel("Outpost").selectOption("factory-floor");
  await expect(fields.getByLabel("Endpoint").locator("option")).toHaveText(["—", "tracker", "warehouse"]);
  await fields.getByLabel("Endpoint").selectOption("warehouse");
  await form.getByRole("button", { name: "Declare" }).click();
  await expect(page.getByTestId("connector-warehouse-plant")).toContainText("at outpost: factory-floor/warehouse");
  const [sent] = await writes(page, /\/orgs\/acme\/connectors$/);
  expect(sent?.body).toEqual({ name: "warehouse-plant", type: "mcp-outpost", kind: "mcp", config: { outpost: "factory-floor", endpoint: "warehouse" } });
});

test("discovering where nobody polls says the outpost is offline; an outpost that announces no alias yet lets it be typed", async ({ page }) => {
  await page.goto("/admin/connectors");
  const card = page.getByTestId("connector-erp-branch");
  await card.getByRole("button", { name: "Discover" }).click();
  const refusal = card.getByTestId("outpost-route-refusal");
  await expect(refusal).toContainText("The outpost is offline");
  await expect(refusal).toContainText("outpost_offline");
  await expect(refusal).toContainText("Alias erp");
  const form = page.locator("#declare");
  await form.getByLabel("Type").selectOption("mcp/mcp-outpost");
  const fields = form.getByTestId("outpost-connector-fields");
  await fields.getByLabel("Outpost").selectOption("branch-office");
  await expect(fields).toContainText("No instance of branch-office announces an endpoint yet");
  await expect(fields.getByLabel("Endpoint")).toBeEditable();
});

test("an agent's access tab says where a tool lives, and that one outpost is online and the other offline", async ({ page }) => {
  await page.goto("/agents/onboarding-coordinator?tab=access");
  const online = page.getByTestId("tool-connector-tracker-onsite__search_issues").getByTestId("tool-outpost");
  await expect(online).toContainText("At the outpost factory-floor, alias tracker");
  await expect(online).toContainText("Online");
  const offline = page.getByTestId("tool-connector-erp-branch__stock_level").getByTestId("tool-outpost");
  await expect(offline).toContainText("At the outpost branch-office, alias erp");
  await expect(offline).toContainText("Offline");
});
