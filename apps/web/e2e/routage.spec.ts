import { expect, test } from "@playwright/test";

/**
 * Router les demandes, choisir le défaut, désactiver un workflow (S23-13) : l'API le savait, la
 * console ne savait que le dire. La démo garde ces changements le temps de la page.
 */
test("les règles de routage se changent en brouillon et s'enregistrent d'un geste", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  const regles = page.getByRole("list", { name: "routing rules" });
  await expect(regles.getByRole("listitem")).toHaveCount(1);
  await expect(regles).toContainText("labelled incident → hotfix");

  const formulaire = page.getByRole("form", { name: "new routing rule" });
  const ajouter = formulaire.getByRole("button", { name: "add the rule" });
  await expect(ajouter).toBeDisabled();
  await expect(ajouter).toHaveAccessibleDescription("a rule needs a label or a type");
  await formulaire.getByLabel("of type").fill("Bug");
  await formulaire.getByLabel("goes to").selectOption("hotfix");
  await ajouter.click();
  await expect(regles.getByRole("listitem")).toHaveCount(2);
  await expect(page.getByText("the routing has unsaved changes")).toBeVisible();
  // L'ordre compte : la première règle qui correspond l'emporte.
  await page.getByRole("button", { name: "up rule 2" }).click();
  await expect(regles.getByRole("listitem").first()).toContainText("of type Bug → hotfix");
  await page.getByRole("button", { name: "save the routing" }).click();
  await expect(page.getByRole("status")).toContainText("routing saved");
  await expect(page.getByTestId("workflow-card-hotfix")).toContainText("of type Bug; labelled incident");
});

test("le défaut se choisit, et un workflow sans règle ni défaut se désactive", async ({ page }) => {
  await page.goto("/p/billing-api/workflows");
  const carte = (nom: string) => page.getByRole("listitem").filter({ has: page.getByTestId(`workflow-card-${nom}`) });
  // hotfix est la cible d'une règle : pas de désactivation, et la raison est dite.
  await expect(carte("hotfix")).toContainText("a routing rule sends requests here — remove it first");
  await expect(carte("hotfix").getByRole("button", { name: "deactivate" })).toHaveCount(0);
  await expect(carte("default-simple").getByRole("button", { name: "make default" })).toHaveCount(0);

  await carte("hotfix").getByRole("button", { name: "make default" }).click();
  await expect(page.getByTestId("confirmation")).toContainText("tickets already running stay where they are");
  await page.getByRole("button", { name: "make hotfix the default" }).click();
  await expect(carte("hotfix").getByText("default", { exact: true })).toBeVisible();

  // default-simple n'est plus le défaut ni une cible : il se désactive, après confirmation.
  await carte("default-simple").getByRole("button", { name: "deactivate" }).click();
  await expect(page.getByTestId("confirmation")).toContainText("its 2 open tickets finish on their version");
  await page.getByRole("button", { name: "deactivate default-simple" }).click();
  await expect(page.getByTestId("workflow-card-default-simple")).toHaveCount(0);
});

test("une étape s'ajoute depuis la vue processus, sans passer par la carte (S23-13)", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple");
  const formulaire = page.getByRole("form", { name: "new step" });
  await formulaire.getByLabel("from").selectOption({ label: "Ready" });
  await formulaire.getByRole("combobox", { name: /^to/ }).selectOption("(new)");
  await formulaire.getByLabel("name of the new state").fill("in_review");
  await formulaire.getByRole("button", { name: "add" }).click();
  // Le geste part dans le brouillon commun ; ce qu'il écrit est prouvé en test unitaire (la démo ne
  // simule que les libellés).
  await expect(page.getByTestId("brouillon")).toContainText("1 change not published yet");
});

test("une skill se rattache à un agent dans sa nouvelle version (S23-13)", async ({ page }) => {
  await page.goto("/agents/onboarding-coordinator");
  const skills = page.getByTestId("skills-de-la-version");
  await expect(skills).toContainText("onboarding-procedure@1");
  await skills.getByRole("button", { name: "remove onboarding-procedure" }).click();
  await expect(skills).toContainText("None");
  await skills.getByLabel("add a skill").selectOption("onboarding-procedure");
  await expect(skills.getByLabel("version")).toHaveValue("");
  await skills.getByRole("button", { name: "attach" }).click();
  await expect(skills).toContainText("onboarding-procedure (latest)");
  await page.getByRole("button", { name: /^publish v\d+$/ }).click();
  await expect(page.getByRole("status")).toContainText("published");
  // La version publiée porte la skill, sans version : la dernière, lue à chaque run.
  await expect(page.getByTestId("version")).toContainText("onboarding-procedure");
  await expect(page.getByTestId("version")).not.toContainText("onboarding-procedure@1");
});
