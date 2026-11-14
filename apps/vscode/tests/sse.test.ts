// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { readEvents, type ServerSentEvent } from "../src/api/sse";

function body(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function read(chunks: string[]): Promise<ServerSentEvent[]> {
  const events: ServerSentEvent[] = [];
  for await (const event of readEvents(body(chunks))) events.push(event);
  return events;
}

describe("the server-sent event reader", () => {
  it("dispatches on a blank line, with its event, data and id", async () => {
    expect(await read(['event: activity.upsert\nid: 7\ndata: {"a":1}\n\n'])).toEqual([
      { event: "activity.upsert", data: '{"a":1}', id: "7" },
    ]);
  });

  it("reads lines split across chunks, whatever their end", async () => {
    const whole = "event: a\r\ndata: one\r\n\r\nevent: b\rdata: two\r\rdata: three\n\n";
    const expected = await read([whole]);
    const split = await read(whole.split(""));
    expect(split).toEqual(expected);
    expect(expected.map((e) => [e.event, e.data])).toEqual([
      ["a", "one"],
      ["b", "two"],
      ["message", "three"],
    ]);
  });

  it("joins several data lines and keeps the last id for later events", async () => {
    expect(await read(["id: 3\ndata: x\ndata: y\n\ndata: z\n\n"])).toEqual([
      { event: "message", data: "x\ny", id: "3" },
      { event: "message", data: "z", id: "3" },
    ]);
  });

  it("ignores comments and events without data", async () => {
    expect(await read([": ping\n\nevent: empty\n\n", "data:no-space\n\n"])).toEqual([
      { event: "message", data: "no-space", id: undefined },
    ]);
  });

  it("dispatches nothing for an event the stream cut before its blank line", async () => {
    expect(await read(["data: complete\n\n", "data: cut"])).toEqual([
      { event: "message", data: "complete", id: undefined },
    ]);
  });

  it("decodes a character split across chunks", async () => {
    const bytes = new TextEncoder().encode("data: café\n\n");
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 9));
        controller.enqueue(bytes.slice(9));
        controller.close();
      },
    });
    const events: ServerSentEvent[] = [];
    for await (const event of readEvents(stream)) events.push(event);
    expect(events[0]?.data).toBe("café");
  });
});
