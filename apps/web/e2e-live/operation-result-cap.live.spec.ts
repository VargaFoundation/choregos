import { expect, test } from "@playwright/test";

/**
 * An administrator sets an operation's result cap in the console, against the real API (S40-17c).
 * The mock journeys and `tests/operation-result-cap.test.tsx` only prove what the console sends;
 * here the API stores it, the page reads it back after a reload, and the API's own refusal (a cap
 * above the deployment's ceiling) reaches the administrator.
 */
const CONNECTORS = "/api/v1/orgs/varga/connectors";
// A fresh connector per run: a reused API keeps the previous run's.
const NAME = `supplier-${Date.now()}`;

test("the cap set in the console is the one the API keeps, and the API's refusal is shown", async ({ page }) => {
  await page.goto("/api/v1/auth/login?as=admin@varga.dev");
  const created = await page.request.post(CONNECTORS, {
    data: { name: NAME, type: "mcp", config: { url: "https://supplier.test/mcp" } },
  });
  expect(created.status(), await created.text()).toBe(201);
  const discovered = await page.request.post(`${CONNECTORS}/${NAME}/discover`);
  expect(discovered.status(), await discovered.text()).toBe(200);

  const stored = async () => {
    const answer = await page.request.get(CONNECTORS);
    const supplier = (await answer.json()).find((c: { name: string }) => c.name === NAME);
    return supplier.operations.find((o: { name: string }) => o.name === "order_status").result_max_bytes;
  };
  const row = () => page.getByTestId(`operations-${NAME}`).getByRole("row").filter({ hasText: "order_status" });
  const LABEL = "Result cap of order_status";
  const cap = () => row().getByLabel(LABEL);
  const set = () => row().locator("form").filter({ has: page.getByLabel(LABEL) }).getByRole("button", { name: "Set" });

  await page.goto("/admin/connectors");
  await expect(cap()).toHaveValue("");
  await cap().fill("4096");
  await set().click();
  await expect.poll(stored).toBe(4096);
  await page.reload();
  await expect(cap()).toHaveValue("4096");

  // Below the API's minimum, the console does not send it.
  await cap().fill("1000");
  await expect(row().getByText("at least 1024 bytes")).toBeVisible();
  await expect(set()).toBeDisabled();

  // Above the deployment's ceiling (1 MiB by default), only the API knows: its refusal is shown.
  await cap().fill("2000000");
  await set().click();
  await expect(row()).toContainText(/ceiling/i);
  expect(await stored()).toBe(4096);

  // Emptied, the operation is back to the platform default.
  await cap().fill("");
  await set().click();
  await expect.poll(stored).toBeNull();
  await page.reload();
  await expect(cap()).toHaveValue("");
});
