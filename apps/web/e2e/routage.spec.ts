import { expect, test } from "@playwright/test";

/**
 * Router les demandes, choisir le défaut, désactiver un workflow (S23-13) : l'API le savait, la
 * console ne savait que le dire. La démo garde ces changements le temps de la page.
 */
test("les règles de routage se changent en brouillon et s'enregistrent d'un geste", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  const regles = page.getByRole("list", { name: "Routing rules" });
  await expect(regles.getByRole("listitem")).toHaveCount(1);
  await expect(regles).toContainText("labelled incident → hotfix");

  const formulaire = page.getByRole("form", { name: "New routing rule" });
  const ajouter = formulaire.getByRole("button", { name: "Add the rule" });
  await expect(ajouter).toBeDisabled();
  await expect(ajouter).toHaveAccessibleDescription("A rule needs a label or a type");
  await formulaire.getByLabel("Of type").fill("Bug");
  await formulaire.getByLabel("Goes to").selectOption("hotfix");
  await ajouter.click();
  await expect(regles.getByRole("listitem")).toHaveCount(2);
  await expect(page.getByText("The routing has unsaved changes")).toBeVisible();
  // L'ordre compte : la première règle qui correspond l'emporte.
  await page.getByRole("button", { name: "Up rule 2" }).click();
  await expect(regles.getByRole("listitem").first()).toContainText("of type Bug → hotfix");
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

test("le défaut se choisit, et un workflow sans règle ni défaut se désactive", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  const carte = (nom: string) => page.getByRole("listitem").filter({ has: page.getByTestId(`workflow-card-${nom}`) });
  // hotfix est la cible d'une règle : pas de désactivation, et la raison est dite.
  await expect(carte("hotfix")).toContainText("A routing rule sends requests here — remove it first");
  await expect(carte("hotfix").getByRole("button", { name: "Deactivate" })).toHaveCount(0);
  await expect(carte("default-simple").getByRole("button", { name: "Make default" })).toHaveCount(0);

  await carte("hotfix").getByRole("button", { name: "Make default" }).click();
  await expect(page.getByTestId("confirmation")).toContainText("tickets already running stay where they are");
  await page.getByRole("button", { name: "Make hotfix the default" }).click();
  await expect(carte("hotfix").getByText("Default", { exact: true })).toBeVisible();

  // default-simple n'est plus le défaut ni une cible : il se désactive, après confirmation.
  await carte("default-simple").getByRole("button", { name: "Deactivate" }).click();
  await expect(page.getByTestId("confirmation")).toContainText("its 2 open tickets finish on their version");
  await page.getByRole("button", { name: "Deactivate default-simple" }).click();
  await expect(page.getByTestId("workflow-card-default-simple")).toHaveCount(0);
});

test("une étape s'ajoute depuis la vue processus, sans passer par la carte (S23-13)", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/steps");
  const formulaire = page.getByRole("form", { name: "New step" });
  await formulaire.getByLabel("From").selectOption({ label: "Ready" });
  await formulaire.getByRole("combobox", { name: /^To/ }).selectOption("(new)");
  await formulaire.getByLabel("Name of the new state").fill("in_review");
  await formulaire.getByRole("button", { name: "Add" }).click();
  // Le geste part dans le brouillon commun ; ce qu'il écrit est prouvé en test unitaire (la démo ne
  // simule que les libellés).
  await expect(page.getByTestId("draft")).toContainText("1 change not published yet");
});

test("une skill se rattache à un agent dans sa nouvelle version (S23-13)", async ({ page }) => {
  await page.goto("/agents/onboarding-coordinator?tab=definition&edit=1");
  const skills = page.getByTestId("skills-de-la-version");
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
  // La version publiée porte la skill, sans version : la dernière, lue à chaque run.
  await expect(page.getByTestId("definition-groups")).toContainText("onboarding-procedure");
  await expect(page.getByTestId("definition-groups")).not.toContainText("onboarding-procedure@1");
});
