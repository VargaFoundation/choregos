import { expect, type Page, test } from "@playwright/test";

/**
 * L'éditeur YAML sur le build de production, sous la vraie CSP (S21-05). Monaco se téléchargeait
 * depuis un CDN : la CSP bloquait sa feuille et sa police, et l'éditeur du dev s'affichait sans
 * style. Ici, on écoute ce que la page refuse et ce qu'elle demande ailleurs : rien des deux.
 */
async function surveiller(page: Page) {
  const violations: string[] = [];
  const dehors: string[] = [];
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      (window as unknown as { __violations: string[] }).__violations ??= [];
      (window as unknown as { __violations: string[] }).__violations.push(`${event.violatedDirective} ${event.blockedURI}`);
    });
  });
  page.on("console", (message) => {
    if (/Content Security Policy|Refused to/i.test(message.text())) violations.push(message.text());
  });
  page.on("request", (requete) => {
    const url = new URL(requete.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname) && !url.protocol.startsWith("data")) dehors.push(requete.url());
  });
  return async () => {
    const vues = await page.evaluate(() => (window as unknown as { __violations?: string[] }).__violations ?? []);
    return { violations: [...violations, ...vues], dehors };
  };
}

test("l'éditeur YAML se rend et accepte la frappe sous la CSP de production, sans violation ni requête vers un autre domaine", async ({
  page,
}) => {
  const bilan = await surveiller(page);
  await page.goto("/p/billing-api/workflows/default-simple/yaml");
  const editeur = page.getByTestId("yaml-editor");
  await expect(editeur.locator(".cm-editor")).toBeVisible();
  await expect(editeur.locator(".cm-gutters")).toBeVisible();
  expect(await editeur.locator(".cm-line").count()).toBeGreaterThan(5);
  const contenu = page.getByLabel("YAML of default-simple");
  await contenu.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("# a note typed in the console");
  await expect(editeur.locator(".cm-content")).toContainText("# a note typed in the console");
  expect(await bilan()).toEqual({ violations: [], dehors: [] });
});

test("à largeur lg, la validation se tient à côté de l'éditeur, pas dessous", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/workflows/default-simple/yaml");
  await expect(page.getByTestId("yaml-editor").locator(".cm-editor")).toBeVisible();
  const editeur = (await page.getByTestId("yaml-editor").boundingBox())!;
  const validation = (await page.getByTestId("validation-yaml").boundingBox())!;
  expect(validation.x).toBeGreaterThan(editeur.x + editeur.width);
  expect(validation.y).toBeLessThan(editeur.y + editeur.height);
});

test("l'éditeur de la politique d'un projet se rend aussi, sous la même CSP", async ({ page }) => {
  const bilan = await surveiller(page);
  await page.goto("/p/billing-api/settings");
  await expect(page.getByLabel("policy YAML")).toBeVisible();
  await expect(page.getByLabel("policy YAML")).toContainText("per_ticket_usd");
  expect(await bilan()).toEqual({ violations: [], dehors: [] });
});

test("le texte tapé dans l'onglet YAML survit au passage par la carte, et se publie d'un seul bouton", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/yaml");
  const contenu = page.getByLabel("YAML of default-simple");
  await contenu.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("# kept across tabs");
  await expect(page.getByTestId("brouillon")).toContainText("1 change not published yet");
  await page.getByRole("link", { name: "map", exact: true }).click();
  await expect(page.getByTestId("brouillon")).toContainText("1 change not published yet");
  await page.getByRole("link", { name: "YAML", exact: true }).click();
  await expect(page.getByTestId("yaml-editor").locator(".cm-content")).toContainText("# kept across tabs");
  // Un seul bouton de publication, celui de la barre du brouillon.
  await expect(page.getByRole("button", { name: /^publish/ })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "publish v2" })).toBeEnabled();
  await page.getByRole("button", { name: "publish v2" }).click();
  await expect(page.getByText(/published — running items finish on their version/)).toBeVisible();
});

test("un texte invalide se souligne dans la marge et interdit de publier", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/yaml");
  const contenu = page.getByLabel("YAML of default-simple");
  await contenu.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("kind: Workflow");
  await expect(page.getByTestId("validation-yaml")).toContainText("a workflow declares its states");
  await expect(page.getByTestId("yaml-editor").locator(".cm-lint-marker-error")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "publish v2" })).toBeDisabled();
  // Cliquer la ligne d'une erreur ramène le curseur dans l'éditeur, à cette ligne.
  await page.getByTestId("validation-yaml").getByRole("button", { name: "line 1" }).click();
  await expect(page.getByTestId("yaml-editor").locator(".cm-editor.cm-focused")).toHaveCount(1);
  // La carte garde la dernière version valide, et le dit.
  await page.getByRole("link", { name: "map", exact: true }).click();
  await expect(page.getByTestId("texte-illisible")).toBeVisible();
});
