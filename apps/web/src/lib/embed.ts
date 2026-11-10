// SPDX-License-Identifier: Apache-2.0
/**
 * The embedded assistant's plumbing (S63-12, ADR 0139 point 6): the opening context, the events posted to
 * the host application, and the reading of a streamed turn. No React here, so each piece is tested alone.
 */

/** The envelope of every message posted to the host: `{ source: "choregos.assistant", type, ... }`. */
export const EMBED_SOURCE = "choregos.assistant";

export type EmbedEventType = "conversation.started" | "action.confirmed" | "memory.updated" | "resize";

export type EmbedEvent = {
  source: typeof EMBED_SOURCE;
  type: EmbedEventType;
  org: string;
  installation: string;
} & Record<string, unknown>;

/** `?context=<base64url JSON object>`; `null` when absent. Throws a readable error when it is not an object. */
export function decodeContext(raw: string | null | undefined): Record<string, unknown> | null {
  if (!raw) return null;
  try {
    const base64 = raw.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const bytes = Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
    const value: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      return value as Record<string, unknown>;
    }
  } catch {
    /* falls through to the error below */
  }
  throw new Error("The opening context is not a base64url-encoded JSON object.");
}

/** The inverse, for a host (and the tests): what to put in `?context=`. */
export function encodeContext(context: Record<string, unknown>): string {
  const bytes = new TextEncoder().encode(JSON.stringify(context));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Where events go: the allowed origin that framed the page. The browser tells it (`ancestorOrigins`, else
 * the referrer's origin); when that is not one of the installation's origins, or unknown, the event goes to
 * each allowed origin, never to `*`: the browser delivers it only to a host that really has that origin.
 */
export function targetOrigins(
  allowed: readonly string[],
  ancestor: string | null | undefined,
  referrer: string | null | undefined,
): string[] {
  const asOrigin = (value: string | null | undefined): string | null => {
    if (!value) return null;
    try {
      return new URL(value).origin;
    } catch {
      return null;
    }
  };
  const framer = asOrigin(ancestor) ?? asOrigin(referrer);
  return framer !== null && allowed.includes(framer) ? [framer] : [...allowed];
}

/** Posts events to the host. Does nothing when the page is not framed or no origin is allowed. */
export function createEmitter(
  org: string,
  installation: string,
  targets: readonly string[],
  host: { postMessage(message: unknown, targetOrigin: string): void } | null,
) {
  return function emit(type: EmbedEventType, payload: Record<string, unknown> = {}): void {
    if (host === null) return;
    const message: EmbedEvent = { source: EMBED_SOURCE, type, org, installation, ...payload };
    for (const target of targets) host.postMessage(message, target);
  };
}

/** One server-sent event of a streamed turn. */
export type TurnEvent = { event: string; data: string };

/** Reads a `text/event-stream` body, event by event. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<TurnEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    let end = buffer.indexOf("\n\n");
    while (end >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length > 0) yield { event, data: data.join("\n") };
      end = buffer.indexOf("\n\n");
    }
    if (done) return;
  }
}
