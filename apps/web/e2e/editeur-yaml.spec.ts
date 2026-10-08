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
