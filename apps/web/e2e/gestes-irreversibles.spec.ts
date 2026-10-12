import { expect, test, type Page } from "@playwright/test";

/**
 * Un geste qu'on ne défait pas se confirme dans la page, avant de partir (S23-07). La seconde passe
 * du 08/10 : révoquer un jeton, arrêter un ticket, relancer une étape payante, changer un rôle (le
 * sien compris) ou assouplir la politique d'une opération partaient au premier clic ou au premier
 * changement de liste ; les trains passaient par `confirm()` et `prompt()`.
 */
function sansDialogueNatif(page: Page) {
  const dialogues: string[] = [];
  page.on("dialog", (dialogue) => {
    dialogues.push(dialogue.message());
    void dialogue.dismiss();
  });
  return dialogues;
}

test("révoquer un jeton se confirme ; Échap annule et rend le focus", async ({ page }) => {
  await page.goto("/admin");
  const jetons = page.getByTestId("confirmation");
  await page.getByRole("button", { name: "Revoke" }).first().click();
  await expect(jetons).toContainText("a token does not come back");
  await expect(jetons.getByRole("button", { name: /^Revoke / })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(jetons).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Revoke" }).first()).toBeFocused();
});

test("arrêter un ticket et relancer une étape se confirment", async ({ page }) => {
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

test("un train s'approuve et s'abandonne dans la page, l'abandon exige une raison", async ({ page }) => {
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

test("changer un rôle ne part qu'une fois confirmé", async ({ page }) => {
  await page.goto("/admin/members");
  const role = page.getByRole("combobox", { name: /^Role of / }).first();
  const avant = await role.inputValue();
  await role.selectOption("viewer" === avant ? "developer" : "viewer");
  await expect(page.getByTestId("confirmation")).toBeVisible();
  await page.getByTestId("confirmation").getByRole("button", { name: "Cancel" }).click();
  await expect(role).toHaveValue(avant);
});

test("assouplir la politique d'une opération se confirme, la resserrer non", async ({ page }) => {
  await page.goto("/admin/connectors");
  const politique = page.getByLabel("Policy of create_account");
  await politique.selectOption("allowed");
  const confirmation = page.getByTestId("confirmation");
  await expect(confirmation).toContainText("Loosen create_account to allowed?");
  await expect(confirmation).toContainText("agents run it without asking anyone");
  await confirmation.getByRole("button", { name: "Cancel" }).click();
  await expect(politique).toHaveValue("approval");
  await politique.selectOption("forbidden");
  await expect(confirmation).toHaveCount(0);
});

test("désépingler un agent dit ce qui se perd", async ({ page }) => {
  await page.goto("/p/billing-api/agents");
  await page.getByTestId("epingles").getByRole("button", { name: "Unpin" }).first().click();
  await expect(page.getByTestId("confirmation")).toContainText("Its workflows run its latest version from now on");
});
