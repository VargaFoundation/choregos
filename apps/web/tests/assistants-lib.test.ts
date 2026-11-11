import { afterEach, describe, expect, it, vi } from "vitest";
import { citationsOf, connectLinksOf, isProposed } from "@/lib/assistants";
import { parseEvents, streamTurn } from "@/lib/assistants-api";

function sse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("reading an assistant's answer", () => {
  it("finds the citations, once each", () => {
    const text = "Rates rose [Investment policy, 3] and again [Investment policy, v3]; see [Glossary, version 2].";
    expect(citationsOf(text)).toEqual([
      { title: "Investment policy", version: "3" },
      { title: "Glossary", version: "2" },
    ]);
    expect(citationsOf("no citation [a link](x)")).toEqual([]);
  });

  it("turns the connect URL of account_not_connected into a console link", () => {
    const text = "you have not connected your `bank` account: open https://console.example/me/connected-accounts/bank/connect?org=varga, then ask again.";
    expect(connectLinksOf(text)).toEqual([
      { connector: "bank", org: "varga", href: "/me/connected-accounts/bank/connect?org=varga" },
    ]);
    const extra = connectLinksOf("", [{ connect_url: "https://x/me/connected-accounts/broker/connect?org=varga" }, {}]);
    expect(extra.map((l) => l.connector)).toEqual(["broker"]);
    expect(connectLinksOf("", [{ connect_url: "https://x/elsewhere" }])).toEqual([]);
  });

  it("knows a proposal", () => {
    expect(isProposed("proposed")).toBe(true);
    expect(isProposed("confirmed")).toBe(false);
  });
});

describe("the streamed turn", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("splits server-sent events and keeps the unfinished one", () => {
    const parsed = parseEvents('event: a\ndata: {"x":1}\n\nevent: b\ndata: {"y"');
    expect(parsed.events).toEqual([{ event: "a", data: '{"x":1}' }]);
    expect(parsed.rest).toBe('event: b\ndata: {"y"');
  });

  it("hands over the deltas in order, then the end", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sse([
          'event: response.created\ndata: {"id":"r"}\n\nevent: response.output_text.delta\ndata: {"delta":"Hel"}\n\n',
          'event: response.output_text.delta\ndata: {"delta":"lo"}\n\nevent: response.completed\ndata: {"status":"completed","output_text":"Hello"}\n\n',
          "event: response.output_text.delta\ndata: not json\n\n",
        ]),
      ),
    );
    const deltas: string[] = [];
    const done = vi.fn();
    await streamTurn("c1", "hi", { onDelta: (t) => deltas.push(t), onDone: done, onError: vi.fn() });
    expect(deltas).toEqual(["Hel", "lo"]);
    expect(done).toHaveBeenCalledWith({ status: "completed", output_text: "Hello" });
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
    expect(url).toBe("/api/v1/me/conversations/c1/messages");
    expect(JSON.parse(init.body)).toEqual({ input: "hi", stream: true });
  });

  it("reports an error event and a refusal before the stream", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(sse(['event: error\ndata: {"status":409,"detail":"closed"}\n\n'])));
    const onError = vi.fn();
    await streamTurn("c1", "hi", { onDelta: vi.fn(), onDone: vi.fn(), onError });
    expect(onError).toHaveBeenCalledWith("closed");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "not yours" }), { status: 403, headers: { "Content-Type": "application/json" } })),
    );
    await expect(streamTurn("c1", "hi", { onDelta: vi.fn(), onDone: vi.fn(), onError })).rejects.toThrow();
  });
});
