// SPDX-License-Identifier: Apache-2.0
/**
 * The frame token of the embedded component (S63-12, API `embed_frame`): the session of a frame that a host on
 * ANOTHER site holds, since the console's cookie is `SameSite=Lax` and is not sent there. It is minted for the
 * person by the sign-in popup (first party), handed to the frame by `postMessage` (same origin), kept in the
 * frame's memory (and its `sessionStorage`, to survive a reload of the frame) and sent as `X-Choregos-Embed`.
 * The host application never sees it.
 */
/** What the sign-in popup says to the frame that opened it. */
export const SIGNED_IN_MESSAGE = "choregos.embed.signed-in";
export const EMBED_HEADER = "X-Choregos-Embed";
const KEY = "choregos.embed.token";

type Held = { token: string; expiresAt: number };
let held: Held | null = null;
/** A token was held in this page: a 401 afterwards is its expiry, not a console session to sign in again. */
let everHeld = false;

function read(): Held | null {
  if (held) return held;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (raw) {
      held = JSON.parse(raw) as Held;
      everHeld = true;
    }
  } catch {
    held = null;
  }
  return held;
}

/** Keeps a token for `expiresIn` seconds. */
export function setEmbedToken(token: string, expiresIn: number, now = Date.now()): void {
  held = { token, expiresAt: now + expiresIn * 1000 };
  everHeld = true;
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(held));
  } catch {
    /* a private window: the token lives in memory only */
  }
}

export function clearEmbedToken(): void {
  held = null;
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    /* nothing to remove */
  }
}

/** The header to add to a request, or nothing when there is no live token (the cookie then speaks, or nobody). */
export function embedHeaders(now = Date.now()): Record<string, string> {
  const current = read();
  if (!current) return {};
  if (current.expiresAt <= now) {
    clearEmbedToken();
    return {};
  }
  return { [EMBED_HEADER]: current.token };
}

/** Did this frame ever hold a token? Then its 401 is answered by asking the person, never by navigating away. */
export function isFrameSession(): boolean {
  read();
  return everHeld;
}
