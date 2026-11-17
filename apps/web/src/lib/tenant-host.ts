// SPDX-License-Identifier: Apache-2.0
/**
 * The host a visitor reached the console on (ADR 0175). An organisation may own a custom host
 * (`agentic.client.com`): the API says which one (`GET /api/v1/theme`, header `X-Choregos-Host-Org`)
 * and serves that organisation's theme, so the first paint is already its own.
 *
 * `Host` and `X-Forwarded-Host` are client-controlled. The console only FORWARDS the host to the API,
 * which decides whether it is a verified tenant host; this check keeps what is not a DNS name (an
 * address, a path, a long string) from being sent or kept as a memo key.
 */
const LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const HOST = new RegExp(`^(?:${LABEL}\\.)+${LABEL}$`);

/** The DNS name in a `Host` / `X-Forwarded-Host` value (first entry, no port), or null. */
export function validHost(value: string | null | undefined): string | null {
  if (!value) return null;
  let host = value.split(",", 1)[0]!.trim().toLowerCase();
  if ((host.match(/:/g) ?? []).length === 1) host = host.slice(0, host.indexOf(":"));
  host = host.replace(/\.+$/, "");
  if (host.length === 0 || host.length > 253 || /^[0-9.]+$/.test(host)) return null;
  return HOST.test(host) ? host : null;
}
