import { expect, test } from "@playwright/test";

/**
 * What the agent does, readable (S25): its plan, as it publishes it over ACP, revision after revision.
 */
test("the agent's plan reads on a run's page: progress, tasks, removals (S25-01)", async ({ page }) => {
  await page.goto("/p/billing-api/runs/r3");
  const plan = page.getByRole("list", { name: "Agent plan" });
  // Three tasks in the current plan, and two removed along the way: one reworded, one abandoned.
  await expect(plan.getByRole("listitem")).toHaveCount(5);
  await expect(page.getByTestId("plan-progression")).toHaveText("2 of 3 done");
  await expect(plan.locator('[data-statut="removed"]')).toHaveCount(2);
  await expect(plan.locator('[data-statut="removed"]').first()).toContainText("removed in revision 3");
  // The raw journal, for its part, says what each revision contains, without JSON.
  await expect(page.getByTestId("live-log")).toContainText("Plan · 2 of 3 done");
});

test("a run's activity reads above the journal: each call once, the refusal on the blocked call (S25-02)", async ({
  page,
}) => {
  await page.goto("/p/billing-api/runs/r3");
  await expect(page.getByTestId("activite-resume")).toHaveText("5 tool calls · 5 finished · 1 refused · 1 sub-agent");
  const calls = page.getByRole("table", { name: "Tool calls" });
  await expect(calls.locator("tbody tr")).toHaveCount(5);
  const blocked = calls.locator('tr[data-appel="tc-4"]');
  await expect(blocked).toContainText("Edit src/billing/rates.py");
  await expect(blocked).toContainText("Denied");
  await expect(blocked).toContainText("Failed");
  // The activity is ABOVE the raw journal.
  const activity = await page.getByTestId("activite-du-run").boundingBox();
  const journal = await page.getByTestId("live-log").boundingBox();
  expect(activity!.y).toBeLessThan(journal!.y);
});

test("a run that the agent says is done but with a failing guarantee shows \"declared done, not observed\" (S25-03)", async ({
  page,
}) => {
  await page.goto("/p/billing-api/runs/r3");
  await expect(page.getByTestId("fin-declaree")).toHaveText("declared done, not observed");
  // Each item of the summary says where it comes from: the cost is observed, the plan declared, the coverage
  // declared (the runner did not measure it), the tests observed.
  await expect(page.locator("section", { has: page.getByRole("heading", { name: "Cost", exact: true }) }).locator("[data-provenance]")).toHaveText(/Observed/);
  await expect(page.locator("section", { has: page.getByRole("heading", { name: "Agent plan", exact: true }) }).locator("[data-provenance]")).toHaveText(/Declared/);
  const evidence = page.locator("section", { has: page.getByRole("heading", { name: "Evidence", exact: true }) });
  await expect(evidence.getByRole("listitem").filter({ hasText: "Coverage" }).locator("[data-provenance]")).toHaveText(/Declared/);
  await expect(evidence.getByRole("listitem").filter({ hasText: "Tests" }).locator("[data-provenance]")).toHaveText(/Observed/);
});

test("a ticket's hand-off receipts: each output, who produced it, who read it (S25-04)", async ({ page }) => {
  await page.goto("/p/billing-api/items/w1");
  const passages = page.getByRole("list", { name: "Hand-offs" });
  await expect(passages.locator(":scope > li")).toHaveCount(2);
  await expect(passages.locator('[data-sortie="spec_markdown"]')).toContainText("Produced by refine · attempt 1");
  await expect(passages.locator('[data-sortie="spec_markdown"]')).toContainText("Read by implement · attempt 1");
  // The next step read the exact revision produced, for the spec as for the plan.
  await expect(passages.locator('[data-lecture="same"]')).toHaveCount(2);
  await expect(page.getByTestId("modifiee-apres-coup")).toHaveCount(0);
});
