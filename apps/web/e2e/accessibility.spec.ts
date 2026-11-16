import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Accessibility measured, not declared: axe runs on the main screens in demo mode.
 * Every violation blocks, best practices included (S23-05): when only "serious" and "critical"
 * blocked, 23 pages out of 36 jumped from `h1` to `h3` without anything turning red.
 */
const PAGES = [
  "/",
  "/p/billing-api",
  "/p/billing-api/board",
  "/p/billing-api/trains",
  "/p/billing-api/knowledge",
  "/memories",
  "/memories/security-lessons",
  "/p/billing-api/memory",
  "/p/billing-api/knowledge/billing-runbooks",
  "/p/billing-api/workflows",
  "/p/billing-api/workflows/new",
  "/p/billing-api/workflows/library",
  "/p/billing-api/workflows/default-simple/steps",
  "/p/billing-api/workflows/default-simple",
  "/p/billing-api/workflows/default-simple/yaml",
  "/p/billing-api/workflows/default-simple/history",
  "/login",
  "/account",
  "/admin",
  "/admin/connectors",
  "/library/mcp-servers",
  "/library/connectors",
  "/library/plugins",
  "/admin/members",
  "/admin/outposts",
  "/admin/outposts/factory-floor",
  "/admin/audit",
  "/admin/platform",
  "/admin/edition",
  "/admin/x/scim",
  "/integrations",
  "/inbox",
  "/p/billing-api/inbox",
  "/p/billing-api/actions",
  "/p/billing-api/actions/act-poste",
  "/p/billing-api/actions/act-comptes",
  "/agents",
  "/agents?tag=madr",
  "/agents/new",
  "/agents/onboarding-coordinator",
  "/agents/onboarding-coordinator?tab=definition",
  "/agents/onboarding-coordinator?tab=definition&edit=1",
  "/agents/onboarding-coordinator?tab=history",
  "/p/billing-api/settings",
  "/p/billing-api/settings/general",
  "/p/billing-api/settings/connections",
  "/p/billing-api/settings/agents",
  "/p/billing-api/settings/security",
  "/p/billing-api/settings/people",
  "/p/billing-api/settings/danger",
  "/agents/architect?tab=history&compare=1..3",
  "/agents/onboarding-coordinator?tab=lifecycle",
  "/agents/onboarding-coordinator?tab=runs",
  "/agents/leas-claude-code",
  "/skills",
  "/skills/library",
  "/costs",
  "/activity",
  "/skills/onboarding-procedure",
  "/p/billing-api/agents",
  "/p/billing-api/integrations/claude-desktop",
  "/p/billing-api/actions/pr1",
  "/p/billing-api/workflows/release-full",
  "/p/billing-api/workflows/release-full?view=list",
  // A ticket's animated journey (S22-02): in progress, and finished.
  "/p/billing-api/items/w1",
  "/p/billing-api/items/w4",
];

// Both themes (ADR 0043): a contrast that holds in dark can fall in light, and the reverse.
for (const theme of ["dark", "light"] as const) {
  test.describe(`theme ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((choice) => window.localStorage.setItem("choregos.theme", choice), theme);
    });

    for (const path of PAGES) {
      test(`${path} has no serious violation`, async ({ page }) => {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        const results = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
          .analyze();
        const blocking = results.violations;
        expect(
          blocking.map((v) => `${v.id} (${v.impact}) : ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
        ).toEqual([]);
      });
    }
  });
}

/** The map being edited (S16-12): a state's panel and the draft pass axe too. */
test("a map being edited has no serious violation", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple?view=list");
  await page.getByTestId("workflow-graph").getByTestId("state-inbox").click();
  const panel = page.getByTestId("state-panel");
  await panel.getByLabel("label").fill("New requests");
  await panel.getByRole("button", { name: "Set label" }).click();
  await expect(page.getByTestId("draft")).toContainText("1 change not published yet");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]).analyze();
  const blocking = results.violations;
  expect(
    blocking.map((v) => `${v.id} (${v.impact}) : ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
  ).toEqual([]);
});

/** A project's connectors (S19-01): the section alone — the rest of the settings has its own pages. */
test("settings: the connectors section has no serious violation", async ({ page }) => {
  await page.goto("/p/billing-api/settings/connections");
  await page.getByTestId("connector-kind-tracker").getByRole("button", { name: "Edit" }).click();
  const results = await new AxeBuilder({ page })
    .include('[data-testid="connectors"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
    .analyze();
  const blocking = results.violations;
  expect(
    blocking.map((v) => `${v.id} (${v.impact}) : ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
  ).toEqual([]);
});

/**
 * The workflow graph can be walked with the keyboard: Tab reaches the states, the arrows follow
 * the transitions, and the state under the cursor is described (aria-live) under the map.
 */
test("workflow graph: keyboard walk and state description", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple?view=list");
  const map = page.getByTestId("workflow-graph");
  await expect(map.locator("[data-state-id]")).toHaveCount(4);
  const focus = page.getByTestId("workflow-graph-focus");
  await expect(focus).toContainText("Tab reaches the states");

  await map.getByTestId("state-inbox").focus();
  await expect(focus).toHaveText("To triage (agent): → Ready (by refiner).");

  await page.keyboard.press("ArrowRight");
  await expect(map.getByTestId("state-ready")).toBeFocused();
  await expect(focus).toHaveText("Ready (agent): → Done (by dev, gates scope_respected); → Needs a human when retries run out.");
  // By keyboard as by hover: the escalations of the state visited show, then fold back.
  await expect(map.getByTestId("secondary-ready")).toBeVisible();

  await page.keyboard.press("ArrowRight");
  await expect(map.getByTestId("state-done")).toBeFocused();
  await expect(focus).toHaveText("Done (end, terminal): no outgoing transition.");
  await expect(map.getByTestId("secondary-ready")).toHaveCount(0);

  // At the end of the path, → stays in place; Home goes back to the first state.
  await page.keyboard.press("ArrowRight");
  await expect(map.getByTestId("state-done")).toBeFocused();
  await page.keyboard.press("Home");
  await expect(map.getByTestId("state-inbox")).toBeFocused();
  await expect(map.getByTestId("state-inbox")).toHaveAccessibleName(/To triage/);
  await expect(map.getByTestId("state-inbox")).not.toHaveAttribute("aria-label");

  // Enter on a state opens its panel: the map can be edited by keyboard too (S16-12).
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("state-panel")).toBeVisible();
});

test("tab bars do not scroll vertically: Windows drew its arrows there", async ({ page }) => {
  // The active tab drops one pixel onto the rule; without the margin that receives it, each bar
  // overflowed by one pixel in height, and Windows drew ▲ ● ▼ at the end of the line (noted on 10/06).
  const bars: Array<[string, string]> = [
    ["/p/billing-api/workflows/default-simple", "views of default-simple"],
    ["/integrations/claude-code", "clients"],
  ];
  for (const [path, name] of bars) {
    await page.goto(path);
    const bar = page.getByRole("navigation", { name });
    await expect(bar).toBeVisible();
    const overflow = await bar.evaluate((element) => element.scrollHeight - element.clientHeight);
    expect(overflow, `${path} · ${name}`).toBe(0);
  }
});

/**
 * At 1280 px, nothing wraps (S21-03): the navigation is in the sidebar (S24-03), the
 * organisation and the session in the top bar.
 */
test("the header fits on one line at 1280 px, without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  for (const path of ["/", "/skills", "/costs", "/integrations", "/admin"]) {
    await page.goto(path);
    await expect(page.getByRole("complementary", { name: "Sidebar" })).toBeVisible();
    // The session arrives after the page: the header carries its real weight (organisation, the
    // person's menu) only once it is there.
    await expect(page.getByRole("button", { name: /^Account of / })).toBeVisible();
    // Measuring before the fonts load is measuring the fallback font: narrower, it breaks nothing.
    await page.evaluate(() => document.fonts.ready);
    const measured = await page.evaluate(() => ({
      width: document.documentElement.scrollWidth,
      viewport: document.documentElement.clientWidth,
      // The header and the bar have a fixed height: a label that wraps overflows INSIDE it without
      // changing it. What is measured is each element: one line of text, not three.
      // The top bar alone: a page's header (`<header>` too) carries 32 px buttons.
      tall: [
        ...document.querySelectorAll(
          'nav[aria-label="Main navigation"] :is(a, button, span, select), header[data-top-bar] :is(a, button, span, select)',
        ),
      ]
        .filter((el) => el.getBoundingClientRect().height > 30)
        .map((el) => (el.textContent ?? "").trim()),
    }));
    expect(measured.width, path).toBeLessThanOrEqual(measured.viewport);
    expect(measured.tall, path).toEqual([]);
  }
});

/** Every page has its title, and the first tab stop leads to the content (S23-05). */
test("distinct page titles, and a skip link to the content", async ({ page }) => {
  const titles = new Map<string, string>();
  for (const path of ["/", "/inbox", "/p/billing-api", "/p/billing-api/board", "/p/billing-api/items/w1", "/admin"]) {
    await page.goto(path);
    await expect(page).not.toHaveTitle("");
    titles.set(await page.title(), path);
  }
  expect(titles.size).toBe(6);
  await page.goto("/p/billing-api/board");
  await expect(page).toHaveTitle("board · billing-api · choregos");
  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to content" });
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("main#content")).toBeFocused();
});

