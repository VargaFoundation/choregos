// SPDX-License-Identifier: Apache-2.0
/**
 * The extension's one client of the platform (ADR 0155 §6): the REST API of the instance the person
 * configured, under their credential, and nothing else.
 *
 * - The credential goes to that origin only: a redirect is never followed (an instance that redirects
 *   elsewhere would receive the bearer on the next hop otherwise), and an address in clear HTTP is refused
 *   unless it is this machine's loopback.
 * - An answer that is not a success becomes an `ApiError` in the platform's own words (RFC 9457).
 * - The log hook receives the method, the path and the status — never a header, a query value or a body.
 */
import { ApiError, ClientRefusal, errorOf } from "./errors";
import { readEvents, type ServerSentEvent } from "./sse";

export const API_PREFIX = "/api/v1";

export type Query = Record<string, string | number | boolean | undefined>;

export interface ClientOptions {
  /** The console's address, as the person opens it; the API is served under `/api/v1` of the same origin. */
  instanceUrl: string;
  /** The person's credential, read when each request is sent (it may change between two). */
  credential: () => Promise<string | undefined>;
  userAgent: string;
  fetch?: typeof fetch;
  log?: (line: string) => void;
}

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** The origin of the instance, or a refusal that says why it cannot be used. */
export function instanceOrigin(instanceUrl: string): URL {
  const raw = instanceUrl.trim();
  if (!raw) throw new ClientRefusal("no Choregos instance is configured (setting `choregos.instanceUrl`)");
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new ClientRefusal(`\`${raw}\` is not an address`);
  }
  if (url.protocol !== "https:" && !(url.protocol === "http:" && LOOPBACK.has(url.hostname))) {
    throw new ClientRefusal(
      `the instance must be reached over HTTPS (or HTTP on this machine's loopback): \`${url.origin}\` would carry your credential in clear`,
    );
  }
  if (url.username || url.password) {
    throw new ClientRefusal("the instance address must not hold a user name or a password");
  }
  return new URL(url.origin);
}

export class ChoregosClient {
  readonly origin: URL;
  private readonly options: ClientOptions;
  private readonly fetcher: typeof fetch;

  constructor(options: ClientOptions) {
    this.origin = instanceOrigin(options.instanceUrl);
    this.options = options;
    this.fetcher = options.fetch ?? fetch;
  }

  url(path: string, query?: Query): URL {
    if (!path.startsWith("/") || path.startsWith("//")) {
      throw new ClientRefusal(`an API path starts with one \`/\`: \`${path}\``);
    }
    const url = new URL(API_PREFIX + path, this.origin);
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
    return url;
  }

  async get<T>(path: string, query?: Query, signal?: AbortSignal): Promise<T> {
    return this.json<T>("GET", path, query, undefined, signal);
  }

  async post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    return this.json<T>("POST", path, undefined, body, signal);
  }

  /** The events of a `text/event-stream` route, until the platform closes it or `signal` aborts. */
  async *stream(
    path: string,
    query?: Query,
    options: { signal?: AbortSignal; lastEventId?: string; opened?: () => void } = {},
  ): AsyncGenerator<ServerSentEvent> {
    const headers: Record<string, string> = { accept: "text/event-stream" };
    if (options.lastEventId) headers["last-event-id"] = options.lastEventId;
    const response = await this.send("GET", path, query, undefined, headers, options.signal);
    if (!response.body) throw new ClientRefusal(`${path} answered without a body`);
    options.opened?.();
    yield* readEvents(response.body);
  }

  private async json<T>(
    method: string,
    path: string,
    query: Query | undefined,
    body: unknown,
    signal: AbortSignal | undefined,
  ): Promise<T> {
    const headers: Record<string, string> = { accept: "application/json" };
    if (body !== undefined) headers["content-type"] = "application/json";
    const response = await this.send(method, path, query, body, headers, signal);
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  private async send(
    method: string,
    path: string,
    query: Query | undefined,
    body: unknown,
    headers: Record<string, string>,
    signal: AbortSignal | undefined,
  ): Promise<Response> {
    const url = this.url(path, query);
    const credential = await this.options.credential();
    const all: Record<string, string> = { ...headers, "user-agent": this.options.userAgent };
    if (credential) all.authorization = `Bearer ${credential}`;
    const init: RequestInit = { method, headers: all, redirect: "manual" };
    if (body !== undefined) init.body = JSON.stringify(body);
    if (signal) init.signal = signal;
    const response = await this.fetcher(url, init);
    this.options.log?.(`${method} ${url.pathname} → ${response.status}`);
    if (response.type === "opaqueredirect" || (response.status >= 300 && response.status < 400)) {
      throw new ClientRefusal(
        `the instance answered ${method} ${url.pathname} with a redirect; the extension does not follow it with your credential — check \`choregos.instanceUrl\``,
      );
    }
    if (!response.ok) throw await errorOf(response);
    return response;
  }
}

export { ApiError, ClientRefusal };
