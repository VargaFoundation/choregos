import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Network access in the console (ADR 0070), on the demo API: an administrator writes the
 * organisation's deny list and reads it back on a project's settings; the builder publishes an agent
 * with "Only these hosts". In demo mode no request leaves the page: the mock API records every write
 * on `window.__mockWrites`.
 */
type Write = { method: string; path: string; body: Record<string, unknown> };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

async function toTheBuilder(page: Page) {
  await page.goto("/agents/new");
  await page.getByLabel("Name").fill("Release notes writer");
  await page.getByLabel("What should it do?").fill("Writes the release notes of a project from its merged pull requests.");
  await page.getByRole("button", { name: "Start from a blank form" }).click();
  const builder = page.getByTestId("agent-builder");
  await expect(builder).toBeVisible();
  return builder;
}

test("the administrator's deny list is written to the ceiling and shows on a project's settings", async ({ page }) => {
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Network access" })).toBeVisible();
  await page.getByRole("textbox", { name: "Denied hosts" }).fill("news.ycombinator.com\ngithub.com");
  await page.getByRole("button", { name: "Save network access" }).click();
  await expect(page.getByText("Network access saved")).toBeVisible();

  // The write keeps the other keys of the ceiling, and carries the list.
  const [put] = await writes(page, /\/policy-ceiling$/);
  expect(put?.method).toBe("PUT");
  const ceiling = put!.body.ceiling as { budgets?: unknown; network: { deny_domains: string[] } };
  expect(ceiling.budgets).toBeTruthy();
  expect(ceiling.network.deny_domains).toEqual(["news.ycombinator.com", "github.com"]);

  // The demo keeps the ceiling for the time of the page: the project's effective network reads it back.
  // (client-side navigation: a reload would forget the demo's ceiling)
  await page.getByRole("link", { name: "All of acme" }).click();
  await page.getByRole("link", { name: "Billing API" }).first().click();
  await page.getByRole("link", { name: "Settings" }).first().click();
  await expect(page).toHaveURL(/\/p\/billing-api\/settings$/);
  const network = page.getByTestId("project-network");
  await expect(network).toBeVisible();
  await expect(network.getByText("news.ycombinator.com")).toBeVisible();
  await expect(network.getByTestId("network-clamps")).toContainText("github.com");
});

test("network activity lists the refusals first", async ({ page }) => {
  await page.goto("/admin");
  const table = page.getByRole("table").filter({ hasText: "api.stripe.com" });
  await expect(table).toBeVisible();
  await expect(table.getByText("Refused").first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "github.com", exact: true })).toHaveCount(0);
  await page.getByLabel("Refused only").uncheck();
  await expect(page.getByRole("cell", { name: "github.com", exact: true })).toBeVisible();
});

test("the builder publishes an agent with “Only these hosts”", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const builder = await toTheBuilder(page);
  await builder.getByText("Network access", { exact: true }).click();
  await builder.getByLabel("Only these hosts").check();
  await builder.getByLabel("Allowed hosts").fill("pypi.org\napi.github.com");
  await builder.getByLabel("Requests per minute, per host").fill("10");
  await builder.getByRole("button", { name: "Create and activate" }).click();
  await expect(page).toHaveURL(/\/agents\/release-notes-writer$/);
  const [created] = await writes(page, /\/orgs\/[^/]+\/agents$/);
  expect((created?.body.spec as { network: unknown }).network).toEqual({
    domains: ["pypi.org", "api.github.com"],
    per_host_per_minute: 10,
  });
});

test("an agent's page says its network", async ({ page }) => {
  await page.goto("/agents/onboarding-coordinator?tab=definition");
  await expect(page.getByTestId("agent-network-line").first()).toHaveText("2 hosts · 10/min per host");
});

for (const theme of ["dark", "light"] as const) {
  test(`the builder's network section has no accessibility violation (${theme})`, async ({ page }) => {
    await page.addInitScript((choice) => window.localStorage.setItem("choregos.theme", choice), theme);
    const builder = await toTheBuilder(page);
    await builder.getByText("Network access", { exact: true }).click();
    await builder.getByLabel("Only these hosts").check();
    await builder.getByLabel("Allowed hosts").fill("pypi.org");
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
      .analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
}

for (const width of [360, 768, 1280]) {
  test(`the network cards and the builder's section do not scroll sideways at ${width} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const path of ["/admin", "/p/billing-api/settings"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      expect(await page.evaluate(() => document.documentElement.scrollWidth), path).toBeLessThanOrEqual(width);
    }
    const builder = await toTheBuilder(page);
    await builder.getByText("Network access", { exact: true }).click();
    await builder.getByLabel("Only these hosts").check();
    await builder.getByLabel("Allowed hosts").fill("a-rather-long-host-name.subdomain.example.com");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

test("the headings of the network cards are in sentence case", async ({ page }) => {
  await page.goto("/admin");
  for (const name of ["Network access", "Network activity"]) {
    await expect(page.getByRole("heading", { name })).toBeVisible();
    expect(name).not.toMatch(/[a-z] [A-Z]/);
  }
});
