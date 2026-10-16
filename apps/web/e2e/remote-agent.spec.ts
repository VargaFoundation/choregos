import { expect, test, type Page } from "@playwright/test";

/**
 * Remote agents over A2A (S39-08, ADR 0062), on the demo build: no request leaves the page, the mock
 * API records every write on `window.__mockWrites`. The fixtures carry Atlassian's real card, summarised.
 */
type Write = { method: string; path: string; body: Record<string, unknown> };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

test("the administration lists the credentials and connects a pending one", async ({ page }) => {
  await page.goto("/admin/remote-agents");
  const table = page.getByTestId("remote-credentials");
  await expect(table).toBeVisible();
  await expect(page.getByTestId("status-atlassian-rovo")).toContainText("Acts as choregos-bot@acme.dev");
  await expect(page.getByTestId("status-partner-agent")).toContainText("not yet authorised");

  // "Connect" goes to the provider; in demo mode the provider sends the administrator straight back.
  await table.getByRole("row", { name: /partner-agent/ }).getByRole("button", { name: "Connect" }).click();
  await expect(page).toHaveURL(/\/admin\/remote-agents\?connected=partner-agent$/);
  await expect(page.getByTestId("connected-note")).toContainText("partner-agent");
});

test("the sidebar of the administration offers Remote agents", async ({ page }) => {
  await page.goto("/admin");
  await page.getByRole("link", { name: "Remote agents" }).first().click();
  await expect(page).toHaveURL(/\/admin\/remote-agents$/);
});

test("a remote agent is registered from its card: read, choose, publish", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/agents/new");
  await page.getByRole("link", { name: "Or register a remote agent (A2A)" }).click();
  await expect(page).toHaveURL(/\/agents\/new\/remote$/);

  await page.getByLabel(/Card URL/).fill("https://a2a.atlassian.com/.well-known/agent.json");
  await page.getByRole("button", { name: "Read the card" }).click();
  const summary = page.getByTestId("card-summary");
  await expect(summary).toContainText("Atlassian Rovo");
  await expect(page.getByTestId("card-skills")).toContainText("work-in-jira");
  await expect(page.getByTestId("card-skills")).toContainText("work-in-confluence");
  await expect(page.getByTestId("card-auth")).toContainText("OAuth");
  await expect(page.getByTestId("egress-note")).toContainText("a2a.atlassian.com");

  await page.getByLabel("Skill", { exact: true }).selectOption("work-in-jira");
  await page.getByLabel(/Cost per task/).fill("0.05");
  await page.getByRole("button", { name: "Publish the agent" }).click();
  await expect(page).toHaveURL(/\/agents\/atlassian-rovo$/);
  const [created] = await writes(page, /\/orgs\/[^/]+\/agents$/);
  expect(created?.body).toMatchObject({ slug: "atlassian-rovo", kind: "remote" });
  expect(created?.body.spec).toMatchObject({
    runtime: { kind: "a2a", skill: "work-in-jira", credential: "atlassian-rovo", declared_cost: { per_task_usd: 0.05 } },
  });
});

test("a remote agent's page says what it is, and warns when its card changed", async ({ page }) => {
  await page.goto("/agents/rovo");
  await expect(page.getByText("Remote · A2A").first()).toBeVisible();
  // Its latest run stopped on a card that moved: the way out is on the page.
  const warning = page.getByTestId("card-changed");
  await expect(warning).toContainText("card changed");
  await warning.getByRole("link", { name: /publish a new version/i }).click();
  await expect(page).toHaveURL(/tab=definition/);
  await expect(page.getByTestId("remote-endpoint")).toHaveText("https://a2a.atlassian.com/v1/rovo");
  await expect(page.getByTestId("remote-digest")).toContainText("sha256:");
  await expect(page.getByTestId("remote-cost")).toContainText("0.05 USD per task");
  // The next version is the card read again.
  await expect(page.getByRole("button", { name: "Read again" }).or(page.getByRole("button", { name: "Read the card" }))).toBeVisible();
});

test("a run shows its remote task and its states", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r-remote");
  const task = page.getByTestId("remote-task");
  await expect(task).toBeVisible();
  await expect(page.getByTestId("remote-task-id")).toHaveText("task-7f3a");
  const states = page.getByTestId("remote-states");
  await expect(states).toContainText("submitted");
  await expect(states).toContainText("working");
  await expect(states).toContainText("completed");
  await expect(page.getByTestId("remote-final")).toContainText("completed after");
});

test("a run that stopped on a changed card says nothing was sent", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r-remote-drift");
  await expect(page.getByTestId("remote-task-id")).toContainText("nothing was sent");
  await expect(page.getByTestId("remote-reason")).toContainText("Publish a new version");
});
