import { expect, test } from "@playwright/test";

/**
 * No page scrolls sideways (S23-01). On 10/08, ALL the console's pages overflowed below
 * 1280 px — the header did not fold (1091 px wide in a 390 px window) — and the findings page
 * overflowed even at 1280. Three causes, three guards: the header folded behind "menu", the
 * tables that scroll inside their box, and the `sr-only` texts that no longer escape a
 * scrolling container (`globals.css`).
 */
const PAGES = [
  "/",
  "/p/billing-api",
  "/p/billing-api/board",
  "/p/billing-api/trains",
  "/p/billing-api/findings",
  "/p/billing-api/memory",
  "/memories",
  "/memories/security-lessons",
  "/p/billing-api/workflows",
  "/p/billing-api/workflows/default-simple/steps",
  "/p/billing-api/workflows/default-simple",
  "/p/billing-api/workflows/default-simple/yaml",
  "/p/billing-api/workflows/default-simple/history",
  "/p/billing-api/workflows/release-full",
  "/p/billing-api/workflows/dev-complex/steps",
  "/p/billing-api/workflows/dev-complex",
  "/p/billing-api/agents",
  "/p/billing-api/actions",
  "/p/billing-api/actions/act-poste",
  "/p/billing-api/integrations",
  "/p/billing-api/settings",
  "/p/billing-api/settings/general",
  "/p/billing-api/settings/connections",
  "/p/billing-api/settings/agents",
  "/p/billing-api/settings/security",
  "/p/billing-api/settings/people",
  "/p/billing-api/settings/danger",
  "/p/dev/settings",
  "/p/billing-api/items/w1",
  "/admin/remote-agents",
  "/agents/new/remote",
  "/agents/rovo",
  "/p/billing-api/runs/r-remote",
  "/p/billing-api/items/w2",
  "/p/billing-api/runs/r3",
  "/inbox",
  "/agents",
  "/agents?tag=madr",
  "/agents/new",
  "/agents/onboarding-coordinator",
  "/agents/architect?tab=definition",
  "/agents/architect?tab=definition&edit=1",
  "/agents/architect?tab=history&compare=1..3",
  "/agents/onboarding-coordinator?tab=lifecycle",
  "/skills",
  "/skills/library",
  "/costs",
  "/activity",
  "/skills/onboarding-procedure",
  "/integrations",
  "/integrations/claude-code",
  "/account",
  "/admin",
  "/admin/connectors",
  "/library/mcp-servers",
  "/library/connectors",
  "/library/plugins",
  "/admin/egress",
  "/admin/members",
  "/admin/audit",
  "/admin/platform",
  "/admin/edition",
  "/projects/new",
  "/login",
];

for (const width of [360, 768, 1024, 1280]) {
  test(`no page scrolls sideways at ${width} px`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    const overflowing: string[] = [];
    for (const path of PAGES) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await page.evaluate(() => document.fonts.ready);
      const pageWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      if (pageWidth > width) overflowing.push(`${path}: ${pageWidth} px`);
    }
    expect(overflowing).toEqual([]);
  });
}

test("below 1280 px, the navigation folds behind \"menu\", and opens from it", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/");
  const navigation = page.getByRole("navigation", { name: "Main navigation" });
  await expect(navigation.getByRole("link", { name: "Skills" })).toBeHidden();
  const menu = navigation.getByRole("button", { name: "Menu" });
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  // A touch target at least 44 px tall.
  expect((await menu.boundingBox())?.height).toBeGreaterThanOrEqual(44);

  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await expect(navigation.getByRole("link", { name: "Projects" })).toHaveAttribute("aria-current", "page");
  await expect(navigation.getByRole("button", { name: "Sign out" })).toBeVisible();
  // Escape closes it, and gives the focus back to the button.
  await page.keyboard.press("Escape");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(menu).toBeFocused();

  // Following a link closes the menu.
  await menu.click();
  await navigation.getByRole("link", { name: "Skills" }).click();
  await expect(page).toHaveURL(/\/skills$/);
  await expect(menu).toHaveAttribute("aria-expanded", "false");
});
