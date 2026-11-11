import { expect, test } from "@playwright/test";

/**
 * The journeys of the assistants' screens (S63-18) in a real browser, against a fake API
 * (`e2e-assistants/fake-api.mjs`): my assistants, a conversation (a streamed answer, the memory proposal and
 * the thumb, with nothing in the link to say whose it is), and the owner's installation form.
 * Run: `pnpm e2e:assistants` (CI). The Vitest files `assistants-*.test.tsx` prove each gesture on its own.
 */
const API = "http://127.0.0.1:4200/api/v1";

type Call = { method: string; path: string; body: unknown };
async function calls(request: import("@playwright/test").APIRequestContext): Promise<Call[]> {
  return (await (await request.get(`${API}/__calls`)).json()) as Call[];
}

test("from my assistants to a conversation: the streamed answer, the memory proposal, the thumb", async ({ page, request }) => {
  await page.goto("/assistants");
  await expect(page.getByText("One-on-one coach")).toBeVisible();
  await page.getByRole("button", { name: "Converse" }).click();

  // The link carries no hint: the conversation says it is the assistant `acme/coach`'s.
  await expect(page).toHaveURL(/\/conversations\/c1$/);
  await expect(page.getByText("Start with")).toBeVisible();
  await expect(page.getByLabel("Citations")).toContainText("Coaching guide · v2");
  await expect(page.getByTestId("memory-proposals")).toContainText("Prefers short meetings");

  await page.getByLabel("Message").fill("And then?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Ask what went well.")).toBeVisible();

  // The thumb goes to the conversation's organisation (`acme`), not to the session's current one.
  await page.getByRole("button", { name: "Bad answer" }).click();
  await page.getByLabel("Comment").fill("too vague");
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByText("Thank you for your feedback.")).toBeVisible();
  const sent = (await calls(request)).filter((c) => c.path.endsWith("/feedback"));
  expect(sent.at(-1)).toMatchObject({ path: "/orgs/acme/agent-sessions/c1/feedback", body: { rating: "down" } });
});

test("a conversation opened from the list shows the same memory proposals", async ({ page }) => {
  await page.goto("/conversations");
  await page.getByRole("link", { name: "Preparing Ada" }).click();
  await expect(page.getByTestId("memory-proposals")).toContainText("Prefers short meetings");
});

test("an owner installs an assistant with the form its config_schema draws", async ({ page, request }) => {
  await page.goto("/assistants/manage");
  await page.getByLabel("Plugin").selectOption("coaching");
  await page.getByLabel("Assistant").selectOption("one-on-one-coach");
  // The defaults are in, the required field without one is asked for, the list of URLs is typed as a URL list.
  await expect(page.getByLabel(/company name/i)).toHaveValue("Diametral");
  await page.getByLabel(/hr contact/i).fill("hr@acme.test");
  await page.getByLabel(/embed origins/i).fill("https://scorecard.example.org, https://hr.example.org");
  await page.getByRole("button", { name: /^Install/ }).click();
  const installs = (await calls(request)).filter((c) => c.method === "POST" && c.path === "/orgs/acme/assistants");
  expect(installs.at(-1)?.body).toMatchObject({
    plugin: "coaching",
    assistant: "one-on-one-coach",
    config: {
      company_name: "Diametral",
      hr_contact: "hr@acme.test",
      embed_origins: ["https://scorecard.example.org", "https://hr.example.org"],
    },
  });
});
