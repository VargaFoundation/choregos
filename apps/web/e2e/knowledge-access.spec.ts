import { expect, test, type Page } from "@playwright/test";

/**
 * Rights on a knowledge base (S42-11, ADR 0081), in demo mode: the mock API answers `can` and
 * records every write on `window.__mockWrites`.
 */
type Write = { method: string; path: string; body: Record<string, unknown> };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

test("an owner grants a role to a group and to an e-mail, and the whole list is saved", async ({ page }) => {
  await page.goto("/knowledge/architecture-framework");
  const card = page.getByTestId("knowledge-access");
  await expect(card).toContainText("you: owner");
  await card.getByLabel("Grant to").selectOption("group");
  await card.getByLabel("Group").fill("architects");
  await card.getByLabel("Role", { exact: true }).selectOption("editor");
  await card.getByRole("button", { name: "Add" }).click();
  await card.getByLabel("Grant to").selectOption("email");
  await card.getByLabel("E-mail").fill("Ana@Varga.dev");
  await card.getByLabel("Role", { exact: true }).selectOption("viewer");
  await card.getByRole("button", { name: "Add" }).click();
  await card.getByRole("button", { name: "Save access" }).click();
  await expect(page.getByText("Access saved")).toBeVisible();
  const [put] = await writes(page, /\/knowledge\/architecture-framework\/grants$/);
  expect(put?.method).toBe("PUT");
  expect(put?.body).toEqual({
    grants: [
      { subject_type: "group", subject: "architects", role: "editor" },
      { subject_type: "user", subject: "ana@varga.dev", role: "viewer" },
    ],
  });
  // The next read gives them back, the e-mail still waiting for a member.
  await expect(page.getByTestId("knowledge-grants")).toContainText("waiting for sign-in");
});

test("an administrator restricts a base", async ({ page }) => {
  await page.goto("/knowledge/architecture-framework");
  await page.getByRole("checkbox", { name: /Restricted/ }).check();
  await expect.poll(async () => (await writes(page, /\/knowledge\/architecture-framework$/)).length).toBe(1);
  const [patch] = await writes(page, /\/knowledge\/architecture-framework$/);
  expect(patch?.method).toBe("PATCH");
  expect(patch?.body).toEqual({ restricted: true });
});

test("a reader sees the base but every gesture is disabled with the reason", async ({ page }) => {
  await page.goto("/knowledge/security-policy");
  await expect(page.getByTestId("knowledge-read-only")).toContainText("viewer grant");
  await expect(page.getByRole("button", { name: "Distill" })).toBeDisabled();
  await expect(page.getByLabel("Documents to upload")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save access" })).toHaveCount(0);
});
