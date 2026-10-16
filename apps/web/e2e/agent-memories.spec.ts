import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * An agent binds several memories, global and project (S40-11, ADR 0075), on the demo's mock API:
 * the builder's Memory section, the pages that curate the memories, and the project's selector.
 */
async function newAgentBuilder(page: Page, name: string) {
  await page.goto("/agents");
  await page.getByRole("link", { name: "New agent" }).click();
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("What should it do?").fill(`${name}: what it is for, in a sentence.`);
  await page.getByRole("button", { name: "Start from a blank form" }).click();
  return page.getByTestId("agent-builder");
}

async function unfold(builder: Locator, title: string) {
  await builder.locator("summary", { hasText: title }).click();
}

test("an agent binds an organisation memory and a project memory, and the version holds them in order", async ({ page }) => {
  const builder = await newAgentBuilder(page, "Architect two");
  await builder.getByLabel("Instructions").fill("Write one decision record for {{ ticket.title }}.");
  await unfold(builder, "Memory");
  const bindings = builder.getByTestId("memory-binding");
  await expect(bindings).toHaveCount(1);
  await expect(bindings.first()).toContainText("The project's shared memory");
  // Read the organisation's security lessons first, then the project's decisions; keep writing to the shared memory.
  await builder.getByLabel("Add a memory").selectOption("global:security-lessons");
  await builder.getByRole("button", { name: "Add it" }).click();
  await builder.getByLabel("Add a memory").selectOption("project:decisions");
  await builder.getByRole("button", { name: "Add it" }).click();
  await builder.getByRole("button", { name: "Move global:security-lessons up" }).click();
  await expect(bindings).toHaveCount(3);
  await expect(bindings.nth(0)).toContainText("global:security-lessons");
  await expect(bindings.nth(1)).toContainText("project:shared");
  await expect(bindings.nth(2)).toContainText("project:decisions");
  // One write at a time: writing to the project's decisions takes it from the shared memory.
  await bindings.nth(2).getByLabel("Read and write").check();
  await expect(bindings.nth(1).getByLabel("Read", { exact: true })).toBeChecked();
  await bindings.nth(1).getByLabel("Read and write").check();
  await expect(bindings.nth(2).getByLabel("Read", { exact: true })).toBeChecked();
  await expect(builder.locator("summary", { hasText: "Memory" })).toContainText("1 organisation, 2 project · writes to the project's shared memory");
  await builder.getByRole("button", { name: "Create and activate" }).click();
  await expect(page).toHaveURL(/\/agents\/architect-two$/);
  // Its next version starts from what v1 holds, in the same order.
  await page.getByRole("link", { name: "Definition" }).click();
  // The Definition tab reads the version first (S40-12): the bindings are listed, in order, with where it writes.
  await page.getByText("Capabilities", { exact: true }).click();
  const read = page.getByTestId("memory-bindings");
  await expect(read.getByRole("listitem")).toHaveCount(3);
  await expect(read.getByRole("listitem").nth(0)).toContainText("security-lessons (organisation)");
  await expect(read.getByRole("listitem").nth(2)).toContainText("decisions (project)");
  await expect(read).toContainText("It writes to the project's shared memory");
  await page.getByRole("link", { name: "Edit" }).click();
  const next = page.getByTestId("agent-builder");
  await expect(next.getByTestId("memory-binding")).toHaveCount(3);
  await expect(next.getByTestId("memory-binding").nth(0)).toContainText("global:security-lessons");
  await expect(next.getByTestId("memory-binding").nth(2)).toContainText("project:decisions");
  await page.getByRole("tab", { name: "What it costs and allows" }).click();
  await expect(page.getByTestId("memoire-resolue")).toContainText("Security lessons (organisation) — read");
  await expect(page.getByTestId("memoire-resolue")).toContainText("It writes to the project's shared memory");
});

test("the organisation's registry lists its memories, and an administrator creates one", async ({ page }) => {
  await page.goto("/memories");
  const table = page.getByTestId("memories");
  await expect(table).toContainText("Security lessons");
  await expect(table).toContainText("projects billing-api");
  await page.getByRole("button", { name: "New memory" }).click();
  const drawer = page.getByRole("dialog");
  await drawer.getByLabel("Slug").fill("lessons-two");
  await drawer.getByRole("button", { name: "Create" }).click();
  await expect(table).toContainText("lessons-two");
  const barre = page.getByRole("complementary", { name: "Sidebar" });
  await expect(barre.getByRole("link", { name: "Memories" })).toHaveAttribute("aria-current", "page");
});

test("an administrator decides what agents proposed to an organisation memory, and changes its reach", async ({ page }) => {
  await page.goto("/memories/security-lessons");
  const proposals = page.getByTestId("memory-proposals");
  await expect(proposals).toContainText("Pin the minimum TLS version");
  await proposals.getByRole("button", { name: "Accept" }).click();
  await expect(page.getByText("Nothing waits for a decision")).toBeVisible();
  await page.getByLabel("Reach").selectOption("projects");
  await page.getByLabel("Billing API").check();
  await page.getByRole("button", { name: "Save the reach" }).click();
  await expect(page.getByText("projects billing-api")).toBeVisible();
});

test("a project's memory page picks a memory, and an owner creates one", async ({ page }) => {
  await page.goto("/p/billing-api/memory");
  const picker = page.getByLabel("Memory", { exact: true });
  await expect(picker.locator("option[value='memory:decisions']")).toHaveCount(1);
  await expect(picker.locator("optgroup[label='Organisation (read-only here)'] option")).toHaveCount(2);
  await picker.selectOption("own");
  await expect(page.getByText("Choose an agent to read its own memory.")).toBeVisible();
  await page.getByLabel("Agent").selectOption({ index: 1 });
  await expect(page.getByText("Choose an agent to read its own memory.")).toHaveCount(0);
  await page.getByRole("button", { name: "New project memory" }).click();
  await page.getByRole("dialog").getByLabel("Slug").fill("runbooks");
  await page.getByRole("dialog").getByRole("button", { name: "Create" }).click();
  await expect(picker.locator("option[value='memory:runbooks']")).toHaveCount(1);
});

for (const path of ["/memories", "/memories/security-lessons", "/p/billing-api/memory"]) {
  test(`${path} is axe-clean and does not scroll sideways at 390 px`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 }).or(page.getByLabel("Memory", { exact: true })).first()).toBeVisible();
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(sideways).toBe(false);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
  });
}
