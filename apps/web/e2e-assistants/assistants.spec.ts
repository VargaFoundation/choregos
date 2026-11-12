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

test("a write waiting for me shows what the source says it would change", async ({ page }) => {
  await page.goto("/conversations/c1");
  await expect(page.getByText("Update Ada's phone")).toBeVisible();
  await expect(page.getByTestId("action-diff")).toContainText('"to": "02"');
});

test("the memory page exports everything and adds a fact of my own", async ({ page, request }) => {
  await page.goto("/assistants/acme/coach");
  await expect(page.getByText("Prefers short meetings")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export everything" }).click();
  expect((await download).suggestedFilename()).toBe("coach-memory.json");

  await page.getByLabel("Add a fact").fill("Reads Les Echos");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect
    .poll(async () => (await calls(request)).filter((c) => c.method === "PATCH" && c.path.endsWith("/memory/preferred_sources")).length)
    .toBeGreaterThan(0);
  const patch = (await calls(request)).filter((c) => c.method === "PATCH").at(-1);
  expect(patch?.body).toEqual({ action: "add", content: "Reads Les Echos" });
});

test("a conversation opened from the list shows the same memory proposals", async ({ page }) => {
  await page.goto("/conversations");
  await page.getByRole("link", { name: "Preparing Ada" }).click();
  await expect(page.getByTestId("memory-proposals")).toContainText("Prefers short meetings");
});

test("an owner installs an assistant with the form its config_schema draws", async ({ page, request }) => {
  await page.goto("/assistants/manage");
  // The session's current organisation is `varga`; the owner installs in `acme`.
  await page.getByLabel("Current organisation").selectOption({ label: "Acme" });
  await page.getByLabel("Plugin").selectOption("coaching");
  await page.getByLabel("Assistant").selectOption("one-on-one-coach");
  // The defaults are in, the required field without one is asked for, the list of URLs is typed as a URL list.
  await expect(page.getByLabel(/company name/i)).toHaveValue("Globex");
  await page.getByLabel(/hr contact/i).fill("hr@acme.test");
  await page.getByLabel(/embed origins/i).fill("https://scorecard.example.org, https://hr.example.org");
  // A oneOf of constants is a choice, a nullable integer a number field, a list of `$ref`'d objects an item list.
  await page.getByLabel("tone").selectOption({ label: "Direct" });
  await page.getByLabel("review days").fill("14");
  await page.getByRole("button", { name: "Add an item" }).click();
  await page.getByLabel("label").fill("Feedback");
  await page.getByRole("button", { name: /^Install/ }).click();
  const installs = (await calls(request)).filter((c) => c.method === "POST" && c.path === "/orgs/acme/assistants");
  expect(installs.at(-1)?.body).toMatchObject({
    plugin: "coaching",
    assistant: "one-on-one-coach",
    config: {
      company_name: "Globex",
      hr_contact: "hr@acme.test",
      embed_origins: ["https://scorecard.example.org", "https://hr.example.org"],
      tone: "direct",
      review_days: 14,
      topics: [{ label: "Feedback" }],
    },
  });
});

test("an owner reveals a task's event door, reads the events journal and seals a Teams webhook", async ({ page, request }) => {
  await page.goto("/assistants/manage/coach");
  await page.getByLabel("Current organisation").selectOption({ label: "Acme" });
  await expect(page.getByText("The key below is a secret")).toBeVisible();
  await page.getByLabel("Task", { exact: true }).fill("new_ticket");
  await page.getByRole("button", { name: "Show the URL and key" }).click();
  await expect(page.getByTestId("event-key")).toContainText("chev_e2e_secret");
  await expect(page.getByTestId("event-key")).toContainText("/tasks/new_ticket/events");
  await expect(page.getByText("run started for ada")).toBeVisible();

  await expect(page.getByText("Not configured")).toBeVisible();
  await page.getByLabel("Teams webhook URL").fill("https://example.webhook.office.com/secret-path");
  await page.getByRole("button", { name: "Seal the URL" }).click();
  await expect(page.getByText(/version 1/)).toBeVisible();
  await expect(page.locator("body")).not.toContainText("secret-path");
  const put = (await calls(request)).filter((c) => c.method === "PUT" && c.path.endsWith("/delivery/teams"));
  expect(put.at(-1)?.body).toEqual({ webhook_url: "https://example.webhook.office.com/secret-path" });
  await page.getByRole("button", { name: "Remove" }).click();
  await page.getByRole("button", { name: "Remove" }).last().click();
  await expect(page.getByText("Not configured")).toBeVisible();
});
