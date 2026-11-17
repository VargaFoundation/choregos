// SPDX-License-Identifier: Apache-2.0
/**
 * The extension's log is the *Tranibor* output channel on the person's machine (ADR 0155 §8). A line never
 * holds a credential: whatever looks like a bearer token or a Tranibor API token is masked before it is
 * written, so a message quoted from elsewhere cannot leak one either.
 */
const SECRETS = [/(bearer\s+)[^\s"']+/gi, /\bchg_[A-Za-z0-9_-]+/g];

export function redact(line: string): string {
  return SECRETS.reduce(
    (text, pattern) =>
      text.replace(pattern, (_match, prefix: unknown) => `${typeof prefix === "string" ? prefix : ""}***`),
    line,
  );
}
