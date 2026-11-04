import { expect, test } from "@playwright/test";

/**
 * Route requests, choose the default, deactivate a workflow (S23-13): the API knew how, the
 * console could only say so. The demo keeps these changes for the time of the page.
 */
test("routing rules are changed in a draft and saved in one gesture", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  const rules = page.getByRole("list", { name: "Routing rules" });
  await expect(rules.getByRole("listitem")).toHaveCount(1);
  await expect(rules).toContainText("labelled incident → hotfix");

  const form = page.getByRole("form", { name: "New routing rule" });
  const add = form.getByRole("button", { name: "Add the rule" });
  await expect(add).toBeDisabled();
  await expect(add).toHaveAccessibleDescription("A rule needs a label or a type");
  await form.getByLabel("Of type").fill("Bug");
  await form.getByLabel("Goes to").selectOption("hotfix");
  await add.click();
  await expect(rules.getByRole("listitem")).toHaveCount(2);
  await expect(page.getByText("The routing has unsaved changes")).toBeVisible();
  // Order matters: the first rule that matches wins.
  await page.getByRole("button", { name: "Up rule 2" }).click();
  await expect(rules.getByRole("listitem").first()).toContainText("of type Bug → hotfix");
  await page.getByRole("button", { name: "Save the routing" }).click();
  await expect(page.getByRole("status")).toContainText("Routing saved");
  await expect(page.getByTestId("workflow-card-hotfix")).toContainText("of type Bug; labelled incident");
});

test("a routing rule is edited in place, labels_all included", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  const rules = page.getByRole("list", { name: "Routing rules" });
  await page.getByRole("button", { name: "Edit rule 1" }).click();
  const form = page.getByRole("form", { name: "Edit routing rule 1" });
  await expect(form.getByLabel("Labelled (any of)")).toHaveValue("incident");
  await form.getByLabel("Labelled (all of)").fill("customer");
  await form.getByRole("button", { name: "Update rule 1" }).click();
  await expect(rules.getByRole("listitem")).toHaveCount(1);
  await expect(rules).toContainText("labelled incident, labelled customer → hotfix");
  await page.getByRole("button", { name: "Save the routing" }).click();
  await expect(page.getByRole("status")).toContainText("Routing saved");
});

test("a small model routes what no rule claims, and the routing can be tried (ADR 0116)", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  await page.getByLabel("Strategy").selectOption("rules_then_llm");
  await page.getByLabel("Routing hint for hotfix").fill("outage in production");
  await page.getByRole("button", { name: "Save the routing" }).click();
  await expect(page.getByRole("status").first()).toContainText("Routing saved");

  const tryIt = page.getByRole("form", { name: "Try the routing" });
  await tryIt.getByLabel("Title").fill("Checkout outage since 9am");
  await tryIt.getByRole("button", { name: "Try" }).click();
  await expect(page.getByTestId("routing-preview")).toContainText("hotfix (chosen by the model)");
  await tryIt.getByLabel("Labels").fill("incident");
  await tryIt.getByRole("button", { name: "Try" }).click();
  await expect(page.getByTestId("routing-preview")).toContainText("hotfix (by a rule): routing rule 1");
});

test("the default is chosen, and a workflow with neither rule nor default is deactivated", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  const card = (name: string) => page.getByRole("listitem").filter({ has: page.getByTestId(`workflow-card-${name}`) });
  // hotfix is the target of a rule: no deactivation, and the reason is given.
  await expect(card("hotfix")).toContainText("A routing rule sends requests here — remove it first");
  await expect(card("hotfix").getByRole("button", { name: "Deactivate" })).toHaveCount(0);
  await expect(card("default-simple").getByRole("button", { name: "Make default" })).toHaveCount(0);

  await card("hotfix").getByRole("button", { name: "Make default" }).click();
  await expect(page.getByTestId("confirmation")).toContainText("tickets already running stay where they are");
  await page.getByRole("button", { name: "Make hotfix the default" }).click();
  await expect(card("hotfix").getByText("Default", { exact: true })).toBeVisible();

  // default-simple is neither the default nor a target any more: it is deactivated, after confirmation.
  await card("default-simple").getByRole("button", { name: "Deactivate" }).click();
  await expect(page.getByTestId("confirmation")).toContainText("its 2 open tickets finish on their version");
  await page.getByRole("button", { name: "Deactivate default-simple" }).click();
  await expect(page.getByTestId("workflow-card-default-simple")).toHaveCount(0);
});

test("a step is added from the process view, without going through the map (S23-13)", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/steps");
  const form = page.getByRole("form", { name: "New step" });
  await form.getByLabel("From").selectOption({ label: "Ready" });
  await form.getByRole("combobox", { name: /^To/ }).selectOption("(new)");
  await form.getByLabel("Name of the new state").fill("in_review");
  await form.getByRole("button", { name: "Add" }).click();
  // The gesture goes into the shared draft; what it writes is proven in a unit test (the demo only
  // simulates labels).
  await expect(page.getByTestId("draft")).toContainText("1 change not published yet");
});

test("a skill is attached to an agent in its new version (S23-13)", async ({ page }) => {
  await page.goto("/agents/onboarding-coordinator?tab=definition&edit=1");
  const skills = page.getByTestId("version-skills");
  await expect(skills).toContainText("onboarding-procedure@1");
  await skills.getByRole("button", { name: "Remove onboarding-procedure" }).click();
  await expect(skills).toContainText("None");
  await skills.getByLabel("Add a skill").selectOption("onboarding-procedure");
  await expect(skills.getByLabel("Version")).toHaveValue("");
  await skills.getByRole("button", { name: "Attach" }).click();
  await expect(skills).toContainText("onboarding-procedure (latest)");
  await page.getByLabel("What changed and why").fill("Follow the latest procedure");
  await page.getByRole("button", { name: /^Publish v\d+$/ }).click();
  await expect(page.getByRole("status")).toContainText("sent to testing");
  // The published version carries the skill, without a version: the latest, read at every run.
  await expect(page.getByTestId("definition-groups")).toContainText("onboarding-procedure");
  await expect(page.getByTestId("definition-groups")).not.toContainText("onboarding-procedure@1");
});
