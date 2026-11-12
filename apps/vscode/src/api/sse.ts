// SPDX-License-Identifier: Apache-2.0
/**
 * A reader of server-sent events (the WHATWG `text/event-stream` format) over a fetch body.
 *
 * The platform streams what is in flight, a run's journal and a conversation's turn this way. The editor's
 * runtime has `fetch` but no `EventSource` that can send an `Authorization` header, so the extension reads
 * the format itself: lines end in LF, CR or CRLF, possibly split across chunks; `data` lines join with LF;
 * a blank line dispatches; a comment (`:`) is a keep-alive and dispatches nothing.
 */
export interface ServerSentEvent {
  event: string;
  data: string;
  id: string | undefined;
}

export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ServerSentEvent> {
  const decoder = new TextDecoder();
  const reader = body.getReader();
  let buffer = "";
  let event = "";
  let data: string[] = [];
  let id: string | undefined;
  let pendingCr = false;

  function* lines(chunk: string, final: boolean): Generator<string> {
    let text = chunk;
    // A CR at the end of the previous chunk may be the first half of a CRLF.
    if (pendingCr && text.startsWith("\n")) text = text.slice(1);
    pendingCr = false;
    buffer += text;
    for (;;) {
      const match = /\r\n|\n|\r/.exec(buffer);
      if (!match) break;
      if (match[0] === "\r" && match.index === buffer.length - 1 && !final) {
        pendingCr = true;
      }
      yield buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
    }
    if (final && buffer) {
      yield buffer;
      buffer = "";
    }
  }

  function* take(line: string): Generator<ServerSentEvent> {
    if (line === "") {
      if (data.length > 0) yield { event: event || "message", data: data.join("\n"), id };
      event = "";
      data = [];
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    else if (field === "id" && !value.includes("\0")) id = value;
  }

  try {
    for (;;) {
      const { done, value } = await reader.read();
      const chunk = done ? decoder.decode() : decoder.decode(value, { stream: true });
      for (const line of lines(chunk, done)) yield* take(line);
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}
