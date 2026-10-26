import { expect, test } from "@playwright/test";

/**
 * An administrator sets an operation's policy, project groups and approver group in the console,
 * against the real API (S40-17d). `operation-result-cap.live.spec.ts` does the same for the result
 * cap: each field is read back from the API and from the page after a reload, so a field the
 * console names differently from the API (accepted with a 200, then ignored) shows up here.
 */
const CONNECTORS = "/api/v1/orgs/varga/connectors";
// A fresh connector per run: a reused API keeps the previous run's.
const NAME = `supplier-${Date.now()}`;

type Operation = { name: string; policy: string; groups: string[]; approver_group: string | null };

test("the policy, groups and approver set in the console are the ones the API keeps", async ({ page }) => {
  await page.goto("/api/v1/auth/login?as=admin@varga.dev");
  const created = await page.request.post(CONNECTORS, {
    data: { name: NAME, type: "mcp", config: { url: "https://supplier.test/mcp" } },
  });
  expect(created.status(), await created.text()).toBe(201);
  const discovered = await page.request.post(`${CONNECTORS}/${NAME}/discover`);
  expect(discovered.status(), await discovered.text()).toBe(200);

  const stored = async (operation: string): Promise<Operation> => {
    const answer = await page.request.get(CONNECTORS);
    const supplier = (await answer.json()).find((c: { name: string }) => c.name === NAME);
    return supplier.operations.find((o: Operation) => o.name === operation);
  };
  const row = (operation: string) =>
    page.getByTestId(`operations-${NAME}`).getByRole("row").filter({ hasText: operation });
  const setIn = (operation: string, label: string) =>
    row(operation)
      .locator("form")
      .filter({ has: page.getByLabel(label) })
      .getByRole("button", { name: "Set" });

  await page.goto("/admin/connectors");

  // A tool the server adds is born forbidden; loosening it asks first, then the API keeps it.
  const policy = row("order_laptop").getByLabel("Policy of order_laptop");
  await expect(policy).toHaveValue("forbidden");
  await policy.selectOption("approval");
  expect((await stored("order_laptop")).policy).toBe("forbidden");
  await row("order_laptop").getByRole("button", { name: "Set approval" }).click();
  await expect.poll(async () => (await stored("order_laptop")).policy).toBe("approval");
  await page.reload();
  await expect(row("order_laptop").getByLabel("Policy of order_laptop")).toHaveValue("approval");
  // Tightening applies at once.
  await row("order_laptop").getByLabel("Policy of order_laptop").selectOption("forbidden");
  await expect.poll(async () => (await stored("order_laptop")).policy).toBe("forbidden");

  // Project groups: the API keeps them sorted and once each.
  const groups = row("order_status").getByLabel("Project groups of order_status");
  await groups.fill("ops, finance, ops");
  await setIn("order_status", "Project groups of order_status").click();
  await expect.poll(async () => (await stored("order_status")).groups).toEqual(["finance", "ops"]);
  await page.reload();
  await expect(row("order_status").getByLabel("Project groups of order_status")).toHaveValue("finance, ops");
  await row("order_status").getByLabel("Project groups of order_status").fill("");
  await setIn("order_status", "Project groups of order_status").click();
  await expect.poll(async () => (await stored("order_status")).groups).toEqual([]);

  // The approver group: a write only; a read says nobody approves it.
  await expect(row("order_status")).toContainText("A read: nobody approves it");
  const approver = row("order_laptop").getByLabel("Approver group of order_laptop");
  await approver.fill("release-managers");
  await setIn("order_laptop", "Approver group of order_laptop").click();
  await expect.poll(async () => (await stored("order_laptop")).approver_group).toBe("release-managers");
  await page.reload();
  await expect(row("order_laptop").getByLabel("Approver group of order_laptop")).toHaveValue("release-managers");
  await row("order_laptop").getByLabel("Approver group of order_laptop").fill("");
  await setIn("order_laptop", "Approver group of order_laptop").click();
  await expect.poll(async () => (await stored("order_laptop")).approver_group).toBeNull();
});
