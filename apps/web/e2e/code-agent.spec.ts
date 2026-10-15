import { expect, test } from "@playwright/test";

/**
 * An agent written in code (S39-17, ADR 0063), on the mock API: the builder offers a runtime, the
 * image is picked among the names the operator gave (never typed as a reference), and the agent's
 * page says it is code and whether its image is pinned by digest.
 */
test("registering a code agent: pick an image, give the command, read it back", async ({ page }) => {
  await page.goto("/agents");
  await page.getByRole("button", { name: "New agent" }).click();
  const builder = page.getByTestId("agent-builder");
  await builder.getByLabel("Slug").fill("ticket-triager-code");
  await builder.getByLabel("Name").fill("Ticket triager (code)");

  await builder.getByLabel("Runtime").selectOption("code");
  await expect(builder.getByRole("combobox", { name: "Backend" })).toHaveCount(0);
  const image = builder.getByLabel("Image");
  // Only names are offered; a tag-only image is flagged.
  await expect(image.getByRole("option", { name: "ticket-triager" })).toBeAttached();
  await expect(image.getByRole("option", { name: "scratch-bench (not pinned by digest)" })).toBeAttached();
  await image.selectOption("ticket-triager");
  await builder.getByLabel("Command").fill("python\n-m\nticket_triager");
  await expect(page.getByTestId("runtime-resolu")).toContainText("Your code runs in the image ticket-triager");

  await builder.getByRole("button", { name: "Create the agent" }).click();
  await expect(page.getByText("Ticket triager (code) registered as v1")).toBeVisible();

  // Client-side navigation: the mock API keeps the new agent for the time of the page.
  await page.getByRole("link", { name: /Ticket triager \(code\)/ }).first().click();
  await expect(page).toHaveURL(/\/agents\/ticket-triager-code/);
  await page.getByRole("link", { name: "Definition" }).click();
  const runtime = page.getByTestId("runtime-code");
  await expect(runtime).toContainText("Code · ticket-triager");
  await expect(runtime).toContainText("python -m ticket_triager");
  await expect(page.getByTestId("image-pinned")).toHaveText("pinned by digest");
});
