import { expect, test } from "@playwright/test";
import { encodeContext } from "../src/lib/embed";

const CONSOLE = `http://127.0.0.1:${process.env.EMBED_CONSOLE_PORT ?? "3300"}`;
const EMBED = "/embed/assistants/varga/coach";
const ALLOWED_HOST = "http://localhost:3100";
const OTHER_HOST = "http://127.0.0.1:3200";
const context = { client_id: "C-9" };
const embedUrl = `${CONSOLE}${EMBED}?context=${encodeContext(context)}`;

test.describe("frame-ancestors of the embed page (response headers, no browser needed)", () => {
  test("only the installation's origins may frame it; every other page and unknown installation refuse framing", async ({
    request,
  }) => {
    const embed = await request.get(`${EMBED}`);
    expect(embed.headers()["content-security-policy"]).toContain(`frame-ancestors ${ALLOWED_HOST}`);
    expect(embed.headers()["content-security-policy"]).not.toContain(OTHER_HOST);
    const login = await request.get("/login");
    expect(login.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
    const unknown = await request.get("/embed/assistants/varga/nobody");
    expect(unknown.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
  });
});

test.describe("a host application frames the assistant", () => {
  test("on an allowed origin: the conversation opens with the context, and the host receives the events", async ({
    page,
  }) => {
    await page.goto(`${ALLOWED_HOST}/?src=${encodeURIComponent(embedUrl)}`);
    const frame = page.frameLocator("#assistant");
    await expect(frame.getByLabel("Message")).toBeVisible();
    await frame.getByLabel("Message").fill("Hello");
    await frame.getByRole("button", { name: "Send" }).click();
    await expect(frame.getByText("Bonjour")).toBeVisible();

    const events = await page.evaluate(() => (window as unknown as { __events: { origin: string; data: { type: string } }[] }).__events);
    const types = events.map((e) => e.data.type);
    expect(types).toContain("conversation.started");
    expect(types).toContain("resize");
    for (const event of events) expect(event.origin).toBe(CONSOLE);

    const calls = (await (await page.request.get("http://127.0.0.1:4100/api/v1/__calls")).json()) as {
      path: string;
      body: unknown;
    }[];
    expect(calls.find((c) => c.path === "/me/assistants/varga/coach/conversations")?.body).toEqual({ context });
  });

  test("on another origin: the browser refuses to frame it and no conversation opens", async ({ page }) => {
    await page.goto(`${OTHER_HOST}/?src=${encodeURIComponent(embedUrl)}`);
    await page.waitForTimeout(2000);
    await expect(page.frameLocator("#assistant").getByLabel("Message")).toHaveCount(0);
    const events = await page.evaluate(() => (window as unknown as { __events: unknown[] }).__events);
    expect(events).toEqual([]);
  });
});
