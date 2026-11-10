import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EmbeddedAssistant } from "@/components/embed/embedded-assistant";
import { encodeContext } from "@/lib/embed";

// The embedded component (S63-12): a conversation of the installation in a host's iframe, and what it tells the host.
const ORIGIN = "https://app.varga.test";
const conversation = {
  id: "c1",
  agent: "coach",
  agent_version: 1,
  status: "open",
  turns: 0,
  cost_usd: 0,
  created_at: "2026-11-10T10:00:00Z",
  last_activity_at: "2026-11-10T10:00:00Z",
  messages: [],
};
const proposed = {
  id: "a1",
  conversation_id: "c1",
  need: "crm",
  operation: "update_contact",
  arguments: {},
  summary: "Update the contact's phone",
  status: "proposed",
  created_at: "2026-11-10T10:00:00Z",
};

type Call = { path: string; method: string; body: unknown };

function sse(...events: [string, unknown][]): Response {
  const text = events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");
  return new Response(text, { headers: { "Content-Type": "text/event-stream" } });
}

function fakeApi(options: { signedIn?: boolean; resumable?: boolean } = {}) {
  const calls: Call[] = [];
  let actions: unknown[] = [];
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path === "/public/assistants/varga/coach/embed") return Response.json({ embed_origins: [ORIGIN] });
    if (path === "/me") return options.signedIn === false ? new Response("{}", { status: 401 }) : Response.json({});
    if (path === "/me/assistants/varga/coach/conversations") return Response.json(conversation, { status: 201 });
    if (path === "/me/conversations/c1" && options.resumable) {
      return Response.json({ ...conversation, messages: [{ role: "user", content: "Earlier" }] });
    }
    if (path === "/me/conversations/c1") return new Response("{}", { status: 404 });
    if (path === "/me/conversations/c1/messages") {
      actions = [proposed];
      return sse(
        ["response.created", {}],
        ["response.output_text.delta", { delta: "Hel" }],
        ["response.output_text.delta", { delta: "lo" }],
        ["response.completed", {}],
      );
    }
    if (path === "/me/conversations/c1/actions") return Response.json({ items: actions });
    if (path === "/me/conversations/c1/actions/a1/confirm") {
      actions = [{ ...proposed, status: "executed" }];
      return Response.json(actions[0]);
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetcher);
  return calls;
}

const host = { postMessage: vi.fn() };
beforeEach(() => {
  host.postMessage.mockClear();
  Object.defineProperty(window, "parent", { value: host, configurable: true });
  Object.defineProperty(document, "referrer", { value: `${ORIGIN}/page`, configurable: true });
});
afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window, "parent", { value: window, configurable: true });
});

const mount = (extra: Partial<Parameters<typeof EmbeddedAssistant>[0]> = {}) =>
  render(
    <EmbeddedAssistant org="varga" installation="coach" rawContext={null} signinRequired={false} memorySpaces={[]} {...extra} />,
  );

describe("the embedded assistant (S63-12)", () => {
  it("opens a conversation with the host's context and tells the host, to its origin only", async () => {
    const calls = fakeApi();
    const context = { client_id: "C-9" };
    mount({ rawContext: encodeContext(context) });
    await screen.findByLabelText("Message");
    const open = calls.find((c) => c.path === "/me/assistants/varga/coach/conversations");
    expect(open?.body).toEqual({ context });
    await waitFor(() =>
      expect(host.postMessage).toHaveBeenCalledWith(
        expect.objectContaining({ source: "choregos.assistant", type: "conversation.started", conversation_id: "c1" }),
        ORIGIN,
      ),
    );
    for (const call of host.postMessage.mock.calls) expect(call[1]).not.toBe("*");
  });

  it("resumes the conversation it opened for the same context", async () => {
    fakeApi();
    const first = mount();
    await screen.findByLabelText("Message");
    first.unmount();
    const calls = fakeApi({ resumable: true });
    mount();
    expect(await screen.findByText("Earlier")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/conversations"))).toBe(false);
  });

  it("streams the answer and lets the person confirm a proposed write, which the host hears of", async () => {
    fakeApi();
    mount();
    fireEvent.change(await screen.findByLabelText("Message"), { target: { value: "Call them" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Hello")).toBeInTheDocument();
    expect(await screen.findByText("Update the contact's phone")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(host.postMessage).toHaveBeenCalledWith(
        expect.objectContaining({ type: "action.confirmed", action_id: "a1", operation: "update_contact" }),
        ORIGIN,
      ),
    );
    await waitFor(() => expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull());
  });

  it("tries the silent sign-in once, then offers a Sign in button", async () => {
    fakeApi({ signedIn: false });
    const assign = vi.fn();
    vi.stubGlobal("location", {
      ...window.location,
      assign,
      pathname: "/embed/assistants/varga/coach",
      search: "?context=e30",
    });
    const first = mount();
    await waitFor(() => expect(assign).toHaveBeenCalledOnce());
    const url = new URL(String(assign.mock.calls[0]?.[0]), "http://localhost:3000");
    expect(url.pathname).toBe("/api/v1/auth/login");
    expect(url.searchParams.get("silent")).toBe("1");
    expect(url.searchParams.get("redirect_to")).toBe("/embed/assistants/varga/coach?context=e30");
    first.unmount();
    mount({ signinRequired: true });
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(assign).toHaveBeenCalledOnce();
  });

  it("says why when the context is not decodable", async () => {
    fakeApi();
    mount({ rawContext: "!!!" });
    expect(await screen.findByText(/not a base64url-encoded JSON object/)).toBeInTheDocument();
  });
});
