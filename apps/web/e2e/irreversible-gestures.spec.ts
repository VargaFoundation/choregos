import { expect, test, type Page } from "@playwright/test";

/**
 * A gesture that cannot be undone is confirmed in the page, before it leaves (S23-07). The second pass
 * of 8 Oct: revoking a token, stopping a ticket, rerunning a paid step, changing a role (one's
 * own included) or loosening an operation's policy left at the first click or at the first
 * change of a list; the trains went through `confirm()` and `prompt()`.
 */
function sansDialogueNatif(page: Page) {
  const dialogues: string[] = [];
  page.on("dialog", (dialogue) => {
    dialogues.push(dialogue.message());
    void dialogue.dismiss();
  });
  return dialogues;
}

test("revoking a token is confirmed; Escape cancels and gives back the focus", async ({ page }) => {
  await page.goto("/admin");
  const tokens = page.getByTestId("confirmation");
  await page.getByRole("button", { name: "Revoke" }).first().click();
  await expect(tokens).toContainText("a token does not come back");
  await expect(tokens.getByRole("button", { name: /^Revoke / })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(tokens).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Revoke" }).first()).toBeFocused();
});

test("stopping a ticket and rerunning a step are confirmed", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.getByTestId("confirmation")).toContainText("it does not resume");
  await page.getByRole("button", { name: "Cancel" }).click();

  await page.goto("/p/billing-api/runs/r3");
  await page.getByRole("button", { name: "Replay the stage" }).click();
  await expect(page.getByTestId("confirmation")).toContainText("billed like this one");
  await page.getByTestId("confirmation").getByRole("button", { name: "Replay the stage" }).click();
  await expect(page.getByRole("status")).toContainText("Replay asked");
});

test("a train is approved and abandoned in the page, abandoning requires a reason", async ({ page }) => {
  const dialogues = sansDialogueNatif(page);
  await page.goto("/p/billing-api/trains");
  await page.getByRole("button", { name: "Abort" }).click();
  const confirmation = page.getByTestId("confirmation");
  await expect(confirmation).toContainText("Abort batch R-43?");
  const abandon = confirmation.getByRole("button", { name: "Abort R-43" });
  await expect(abandon).toBeDisabled();
  await confirmation.getByLabel(/Why/).fill("the refund migration is not reversible");
  await expect(abandon).toBeEnabled();
  await confirmation.getByRole("button", { name: "Cancel" }).click();

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(confirmation).toContainText("go to prod");
  await confirmation.getByRole("button", { name: "Approve R-43" }).click();
  await expect(confirmation).toHaveCount(0);
  expect(dialogues).toEqual([]);
});

test("changing a role leaves only once confirmed", async ({ page }) => {
  await page.goto("/admin/members");
  const role = page.getByRole("combobox", { name: /^Role of / }).first();
  const avant = await role.inputValue();
  await role.selectOption("viewer" === avant ? "developer" : "viewer");
  await expect(page.getByTestId("confirmation")).toBeVisible();
  await page.getByTestId("confirmation").getByRole("button", { name: "Cancel" }).click();
  await expect(role).toHaveValue(avant);
});

test("loosening an operation's policy is confirmed, tightening it is not", async ({ page }) => {
  await page.goto("/admin/connectors");
  const policy = page.getByLabel("Policy of create_account");
  await policy.selectOption("allowed");
  const confirmation = page.getByTestId("confirmation");
  await expect(confirmation).toContainText("Loosen create_account to allowed?");
  await expect(confirmation).toContainText("agents run it without asking anyone");
  await confirmation.getByRole("button", { name: "Cancel" }).click();
  await expect(policy).toHaveValue("approval");
  await policy.selectOption("forbidden");
  await expect(confirmation).toHaveCount(0);
});

test("unpinning an agent says what is lost", async ({ page }) => {
  await page.goto("/p/billing-api/agents");
  await page.getByTestId("epingles").getByRole("button", { name: "Unpin" }).first().click();
  await expect(page.getByTestId("confirmation")).toContainText("Its workflows run its production version from now on");
});
