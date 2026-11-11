import { describe, expect, it, vi } from "vitest";
import { createEmitter, decodeContext, encodeContext, readEvents, targetOrigins } from "@/lib/embed";

describe("the embedded component's opening context (S63-12)", () => {
  it("round-trips a base64url JSON object, accents included", () => {
    const context = { client: "Café Léon", id: 42, nested: { ok: true } }; // lang: fr-ok accents are the point
    const raw = encodeContext(context);
    expect(raw).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(decodeContext(raw)).toEqual(context);
  });

  it("is null when absent and refuses anything that is not a JSON object", () => {
    expect(decodeContext(null)).toBeNull();
    expect(decodeContext("")).toBeNull();
    expect(() => decodeContext("!!!")).toThrow(/base64url/);
    expect(() => decodeContext(encodeContext([1, 2] as unknown as Record<string, unknown>))).toThrow();
  });
});

describe("where the events go (S63-12)", () => {
  const allowed = ["https://app.varga.test", "https://intranet.varga.test"];

  it("goes to the allowed origin that framed the page", () => {
    expect(targetOrigins(allowed, "https://intranet.varga.test", null)).toEqual(["https://intranet.varga.test"]);
    expect(targetOrigins(allowed, undefined, "https://app.varga.test/some/page?x=1")).toEqual([
      "https://app.varga.test",
    ]);
  });

  it("falls back to every allowed origin, never to a wildcard or to an origin that is not allowed", () => {
    expect(targetOrigins(allowed, "https://evil.test", null)).toEqual(allowed);
    expect(targetOrigins(allowed, undefined, "not a url")).toEqual(allowed);
    expect(targetOrigins([], "https://evil.test", null)).toEqual([]);
  });

  it("posts an enveloped event to each target, and nothing when not framed or nowhere to post", () => {
    const host = { postMessage: vi.fn() };
    createEmitter("varga", "coach", ["https://app.varga.test"], host)("action.confirmed", { action_id: "a1" });
    expect(host.postMessage).toHaveBeenCalledExactlyOnceWith(
      { source: "choregos.assistant", type: "action.confirmed", org: "varga", installation: "coach", action_id: "a1" },
      "https://app.varga.test",
    );
    createEmitter("varga", "coach", [], host)("resize");
    createEmitter("varga", "coach", ["https://app.varga.test"], null)("resize");
    expect(host.postMessage).toHaveBeenCalledOnce();
  });
});

describe("a streamed turn", () => {
  it("reads events split across chunks", async () => {
    const encoder = new TextEncoder();
    const chunks = [
      'event: response.created\ndata: {"a":1}\n\nevent: response.output_text.de',
      'lta\r\ndata: {"delta":"Hi"}\r\n\r\n',
    ];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    const seen = [];
    for await (const item of readEvents(body)) seen.push(item);
    expect(seen).toEqual([
      { event: "response.created", data: '{"a":1}' },
      { event: "response.output_text.delta", data: '{"delta":"Hi"}' },
    ]);
  });
});
