import { expect, type Page, test } from "@playwright/test";

/**
 * The YAML editor on the production build, under the real CSP (S21-05). Monaco was downloaded
 * from a CDN: the CSP blocked its stylesheet and its font, and the dev editor showed up without
 * style. Here, we listen to what the page refuses and what it requests elsewhere: neither.
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
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname) && !url.protocol.startsWith("data")) dehors.push(request.url());
  });
  return async () => {
    const vues = await page.evaluate(() => (window as unknown as { __violations?: string[] }).__violations ?? []);
    return { violations: [...violations, ...vues], dehors };
  };
}

test("the YAML editor renders and accepts typing under the production CSP, with no violation and no request to another domain", async ({
  page,
}) => {
  const report = await surveiller(page);
  await page.goto("/p/billing-api/workflows/default-simple/yaml");
  const editor = page.getByTestId("yaml-editor");
  await expect(editor.locator(".cm-editor")).toBeVisible();
  await expect(editor.locator(".cm-gutters")).toBeVisible();
  expect(await editor.locator(".cm-line").count()).toBeGreaterThan(5);
  const content = page.getByLabel("YAML of default-simple");
  await content.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("# a note typed in the console");
  await expect(editor.locator(".cm-content")).toContainText("# a note typed in the console");
  expect(await report()).toEqual({ violations: [], dehors: [] });
});

test("at lg width, the validation sits beside the editor, not below it", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/p/billing-api/workflows/default-simple/yaml");
  await expect(page.getByTestId("yaml-editor").locator(".cm-editor")).toBeVisible();
  const editor = (await page.getByTestId("yaml-editor").boundingBox())!;
  const validation = (await page.getByTestId("validation-yaml").boundingBox())!;
  expect(validation.x).toBeGreaterThan(editor.x + editor.width);
  expect(validation.y).toBeLessThan(editor.y + editor.height);
});

test("a project policy editor renders too, under the same CSP", async ({ page }) => {
  const report = await surveiller(page);
  await page.goto("/p/billing-api/settings/security");
  await page.locator("#policy summary").click(); // the policy sits behind a disclosure (S40-03b)
  await expect(page.getByLabel("Policy YAML")).toBeVisible();
  await expect(page.getByLabel("Policy YAML")).toContainText("per_ticket_usd");
  expect(await report()).toEqual({ violations: [], dehors: [] });
});

test("the text typed in the YAML tab survives going through the map, and is published with a single button", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/yaml");
  const content = page.getByLabel("YAML of default-simple");
  await content.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("# kept across tabs");
  await expect(page.getByTestId("draft")).toContainText("1 change not published yet");
  await page.getByRole("link", { name: "Graph", exact: true }).click();
  await expect(page.getByTestId("draft")).toContainText("1 change not published yet");
  await page.getByRole("link", { name: "YAML", exact: true }).click();
  await expect(page.getByTestId("yaml-editor").locator(".cm-content")).toContainText("# kept across tabs");
  // A single publish button, the draft bar's.
  await expect(page.getByRole("button", { name: /^Publish/ })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Publish v2" })).toBeEnabled();
  await page.getByRole("button", { name: "Publish v2" }).click();
  await expect(page.getByText(/published — running items finish on their version/)).toBeVisible();
});

test("an invalid text is underlined in the margin and cannot be published", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/yaml");
  const content = page.getByLabel("YAML of default-simple");
  await content.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("kind: Workflow");
  await expect(page.getByTestId("validation-yaml")).toContainText("a workflow declares its states");
  await expect(page.getByTestId("yaml-editor").locator(".cm-lint-marker-error")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Publish v2" })).toBeDisabled();
  // Clicking an error's line brings the cursor back into the editor, at that line.
  await page.getByTestId("validation-yaml").getByRole("button", { name: "Line 1" }).click();
  await expect(page.getByTestId("yaml-editor").locator(".cm-editor.cm-focused")).toHaveCount(1);
  // The map keeps the last valid version, and says so.
  await page.getByRole("link", { name: "Graph", exact: true }).click();
  await expect(page.getByTestId("unreadable-text")).toBeVisible();
});

test("a draft survives a detour through another console page (S23-06)", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/yaml");
  await page.getByLabel("YAML of default-simple").click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("# still here");
  await expect(page.getByTestId("draft")).toContainText("1 change not published yet");
  // Another project page unmounts the draft: before, it went away without a word.
  await page.getByRole("link", { name: "Board", exact: true }).click();
  await expect(page).toHaveURL(/\/board$/);
  await page.goBack();
  await expect(page.getByTestId("draft")).toContainText("1 change not published yet — kept from your last visit");
  await expect(page.getByTestId("yaml-editor").locator(".cm-content")).toContainText("# still here");
  await expect(page.getByRole("button", { name: "Publish v2" })).toBeEnabled();
  // Discarding it erases it for good.
  await page.getByRole("button", { name: "Discard" }).click();
  await page.getByRole("button", { name: "Discard my changes" }).click();
  await expect(page.getByTestId("draft")).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("yaml-editor").locator(".cm-content")).not.toContainText("# still here");
});

test("a new workflow does not take the name of an existing workflow (S23-06)", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/new");
  const nameField = page.getByLabel("Name");
  await nameField.fill("default-simple");
  await expect(page.getByText("A workflow named default-simple already exists")).toBeVisible();
  await expect(page.getByRole("button", { name: "Publish default-simple" })).toBeDisabled();
  await expect(page.getByRole("link", { name: "Open default-simple" })).toHaveAttribute(
    "href",
    "/p/billing-api/workflows/default-simple",
  );
  await nameField.fill("offboarding");
  await expect(page.getByRole("button", { name: "Publish offboarding" })).toBeEnabled();
});
