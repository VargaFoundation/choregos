import { expect, test, type Page } from "@playwright/test";

/**
 * The two ways a person gives data to a workflow (S30-08, plan item 15), on the HR project of the
 * demo (`joiners-leavers` template): the inputs known when the ticket is filed, from the workflow's
 * `metadata.inputs`; the data known later, from a task's `form`, with its attestation.
 *
 * In demo mode no request leaves the page: the mock API records every write on `window.__mockWrites`,
 * and the assertions read what the API would have been sent.
 */
type Write = { method: string; path: string; body: Record<string, unknown> };

async function writes(page: Page, pattern: RegExp): Promise<Write[]> {
  const all = await page.evaluate(() => (window as unknown as { __mockWrites?: Write[] }).__mockWrites ?? []);
  return all.filter((write) => pattern.test(write.path));
}

test("an onboarding ticket is filed from its input form, and its fields reach the API", async ({ page }) => {
  await page.goto("/p/people-ops/board");
  const shipped = page.getByRole("region", { name: /^Laptop ordered, enrolled, shipped/ });
  await expect(shipped.getByRole("link", { name: "Paul Martin joins as data analyst" })).toBeVisible();
  await page.getByRole("button", { name: "new request" }).click();
  const fields = page.getByTestId("request-fields");
  // The form is the workflow's `metadata.inputs`: its titles, its required fields, a date picker.
  await expect(fields.getByLabel("Start date (required)")).toHaveAttribute("type", "date");
  await fields.getByLabel("Full name (required)").fill("Nora Benali");
  await fields.getByLabel("Sign-in name (UPN) (required)").fill("nora.benali@acme.test");
  await fields.getByLabel("Start date (required)").fill("2026-11-02");
  await fields.getByLabel("Job title (required)").fill("Payroll specialist");
  await fields.getByLabel("Access group of the role (required)").fill("grp-payroll");
  await fields.getByLabel("Sensitive group (stronger approval) (required)").fill("grp-payroll-admin");
  await fields.getByLabel("Laptop delivery address (required)").fill("4 Quay Road, Nantes");
  await page.getByLabel("request title").fill("Nora Benali joins payroll");
  await page.getByRole("button", { name: "file and start" }).click();

  // The ticket is on the board, in the workflow's first column.
  const received = page.getByRole("region", { name: /^Request received/ });
  await expect(received.getByText("Nora Benali joins payroll")).toBeVisible();
  const [created] = await writes(page, /\/projects\/[^/]*people-ops\/work-items$/);
  expect(created?.method).toBe("POST");
  expect(created?.body).toMatchObject({
    title: "Nora Benali joins payroll",
    workflow: "onboarding",
    start: true,
    fields: {
      nom: "Nora Benali",
      upn: "nora.benali@acme.test",
      date_arrivee: "2026-11-02",
      poste: "Payroll specialist",
      groupe: "grp-payroll",
      groupe_sensible: "grp-payroll-admin",
      adresse: "4 Quay Road, Nantes",
    },
  });
  // An optional field left empty is not sent.
  expect(created?.body.fields).not.toHaveProperty("modele");
});

test("the badge task: its form and its attestation reach the decision", async ({ page }) => {
  await page.goto("/p/people-ops/board");
  await page.getByRole("link", { name: "do the task" }).click();
  await expect(page).toHaveURL(/\/p\/people-ops\/items\/hr1$/);
  // The card of the pending request: the summary, the instructions, the form, the statement.
  await expect(page.getByRole("heading", { name: "decision awaited" })).toBeVisible();
  const task = page.getByRole("main");
  await expect(task.getByText("Hand over the badge", { exact: true })).toBeVisible();
  await expect(task.getByText(/hold it to the reader to read its UID/)).toBeVisible();
  const done = task.getByRole("button", { name: "done", exact: true });
  // Nothing goes until the form is filled and the statement attested.
  await expect(done).toBeDisabled();
  await task.getByRole("textbox", { name: "Badge UID (required)" }).fill("04A1B2C3D4");
  await expect(done).toBeDisabled();
  await task.getByLabel("I attest: I handed the badge to its holder in person").check();
  await done.click();

  // The request is closed: its card goes.
  await expect(page.getByRole("heading", { name: "decision awaited" })).toHaveCount(0);
  const decisions = await writes(page, /^\/work-items\/hr1\/decisions$/);
  expect(decisions).toHaveLength(1);
  expect(decisions[0]!.body).toEqual({ kind: "complete", values: { badge_uid: "04A1B2C3D4" }, attested: true });
});
