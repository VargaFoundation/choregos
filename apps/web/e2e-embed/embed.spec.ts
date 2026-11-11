import { expect, test } from "@playwright/test";
import { encodeContext } from "../src/lib/embed";

const CONSOLE = `http://127.0.0.1:${process.env.EMBED_CONSOLE_PORT ?? "3300"}`;
const EMBED = "/embed/assistants/varga/coach";
const ALLOWED_HOST = "http://localhost:3100";
const OTHER_HOST = "http://127.0.0.1:3200";
const context = { client_id: "C-9" };
// `signin=required`: the identity provider needed the person (a frame under another site holds no cookie), so the
// frame waits for the sign-in popup's token instead of navigating away.
const embedUrl = `${CONSOLE}${EMBED}?context=${encodeContext(context)}&signin=required`;
const FAKE_API = "http://127.0.0.1:4100/api/v1";

type Call = { method: string; path: string; body: unknown; token: string | null };
const callsOf = async (request: import("@playwright/test").APIRequestContext) =>
  (await (await request.get(`${FAKE_API}/__calls`)).json()) as Call[];

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
  test("on an allowed origin: no cookie opens nothing, the frame token does; the host receives the events", async ({
    page,
  }) => {
    await page.goto(`${ALLOWED_HOST}/?src=${encodeURIComponent(embedUrl)}`);
    const frame = page.frameLocator("#assistant");
    // The frame holds no session: the fake API refused its `/me` (no token), and nothing was opened.
    await expect(frame.getByRole("button", { name: "Sign in" })).toBeVisible();
    let calls = await callsOf(page.request);
    expect(calls.some((c) => c.path === "/me" && c.token === null)).toBe(true);
    expect(calls.some((c) => c.path.endsWith("/conversations") && c.method === "POST")).toBe(false);

    // What the sign-in popup does: the first-party session mints the frame token and hands it to the frame
    // (a message from the console's own origin; here the test plays the popup).
    const minted = await page.request.post(`${FAKE_API}/me/assistants/varga/coach/embed-token`, {
      headers: { "X-Fake-Session": "ada" },
    });
    expect(minted.ok()).toBe(true);
    const { token, expires_in } = (await minted.json()) as { token: string; expires_in: number };
    const inner = page.frames().find((f) => f.url().includes("/embed/assistants/varga/coach"));
    expect(inner).toBeTruthy();
    await inner!.evaluate(
      ([t, e]) => window.postMessage({ type: "choregos.embed.signed-in", token: t, expires_in: e }, window.location.origin),
      [token, expires_in] as const,
    );

    await expect(frame.getByLabel("Message")).toBeVisible();
    await frame.getByLabel("Message").fill("Hello");
    await frame.getByRole("button", { name: "Send" }).click();
    await expect(frame.getByText("Bonjour")).toBeVisible();

    const events = await page.evaluate(() => (window as unknown as { __events: { origin: string; data: { type: string; spaces?: string[] } }[] }).__events);
    const types = events.map((e) => e.data.type);
    expect(types).toContain("conversation.started");
    expect(types).toContain("resize");
    // The turn reported that it wrote in a memory space: the host hears `memory.updated`.
    expect(events.find((e) => e.data.type === "memory.updated")?.data.spaces).toEqual(["notes"]);
    for (const event of events) expect(event.origin).toBe(CONSOLE);

    calls = await callsOf(page.request);
    expect(calls.find((c) => c.path === "/me/assistants/varga/coach/conversations")?.body).toEqual({ context });
    // Every call after the hand-over carried the token, the host's page never saw it.
    const after = calls.slice(calls.findIndex((c) => c.token === token));
    expect(after.filter((c) => c.path.startsWith("/me/conversations") || c.path.endsWith("/conversations")).every((c) => c.token === token)).toBe(true);
    expect(JSON.stringify(events)).not.toContain(token);
  });

  test("on another origin: the browser refuses to frame it and no conversation opens", async ({ page }) => {
    await page.goto(`${OTHER_HOST}/?src=${encodeURIComponent(embedUrl)}`);
    await page.waitForTimeout(2000);
    await expect(page.frameLocator("#assistant").getByLabel("Message")).toHaveCount(0);
    const events = await page.evaluate(() => (window as unknown as { __events: unknown[] }).__events);
    expect(events).toEqual([]);
  });
});
