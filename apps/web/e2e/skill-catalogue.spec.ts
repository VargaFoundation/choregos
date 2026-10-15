import { expect, test } from "@playwright/test";

/** S39-12d: browse the skills catalogue, install, find the skill in the library with its provenance (ADR 0065). */

test("install a bundled skill and read its provenance in the library", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/skills");
  await page.getByRole("link", { name: "Catalogue" }).click();
  await expect(page).toHaveURL(/\/skills\/catalogue$/);
  await expect(page.getByTestId("catalogue-version")).toContainText("Catalogue 2026.10.0");

  await page.getByLabel("Search the catalogue").fill("write spec");
  const card = page.getByTestId("entry-write-spec");
  await expect(card).toContainText("Low risk");
  await expect(card).toContainText("Permissive · Apache-2.0");
  await card.getByRole("button", { name: "Install" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByTestId("confirmation")).toContainText("Licence Apache-2.0 (permissive)");
  await drawer.getByRole("button", { name: "Install" }).click();

  // It lands on the skill, approved by the platform, with where it comes from.
  await expect(page).toHaveURL(/\/skills\/write-spec$/);
  const gate = page.getByTestId("porte");
  await expect(gate).toContainText("Approved by platform");
  await expect(gate).toContainText("github.com/anthropics/knowledge-work-plugins");
  await expect(gate).toContainText("Apache-2.0");

  // The library lists it, and the catalogue says it is installed.
  // Client-side navigation: the demo data lives in the page, a reload would forget the install.
  await page.getByRole("complementary", { name: "Sidebar" }).getByRole("link", { name: "Skills" }).click();
  await expect(page.getByTestId("bibliotheque")).toContainText("write-spec");
  await page.getByRole("link", { name: "Catalogue" }).click();
  await page.getByLabel("Search the catalogue").fill("write spec");
  await expect(page.getByTestId("entry-write-spec")).toContainText("Installed, version 1");
});

test("a high-risk git skill says a second administrator will have to approve it, and waits", async ({ page }) => {
  await page.goto("/skills/catalogue");
  await page.getByLabel("Search the catalogue").fill("systematic debugging");
  const card = page.getByTestId("entry-systematic-debugging");
  await expect(card).toContainText("High risk");
  await card.getByRole("button", { name: "Install" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByTestId("confirmation")).toContainText("a second administrator will have to approve it");
  await drawer.getByRole("button", { name: "Install" }).click();
  await expect(page).toHaveURL(/\/skills\/systematic-debugging$/);
  await expect(page.getByTestId("porte")).toContainText("Pending review");
});
