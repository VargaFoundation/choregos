// SPDX-License-Identifier: Apache-2.0
/**
 * A scriptable fake of the Choregos API (ADR 0005), served over real HTTP on the loopback so the client's
 * headers, redirects, problem documents and server-sent events go through a real `fetch`.
 *
 *   const api = await FakeApi.start();
 *   api.json("GET", "/api/v1/me", { email: "ada@example.com", … });
 *   const live = api.stream("/api/v1/orgs/acme/activity/stream");
 *   live.send("activity.upsert", entry);
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

export interface RecordedRequest {
  method: string;
  path: string;
  query: URLSearchParams;
  headers: IncomingMessage["headers"];
  body: string;
}

type Handler = (request: RecordedRequest, response: ServerResponse) => void;

export class StreamController {
  private response: ServerResponse | undefined;
  private readonly backlog: string[] = [];
  private ended = false;
  private openedResolve: (() => void) | undefined;
  /** Resolves once a client is connected to the stream. */
  readonly opened: Promise<void>;
  connections = 0;

  constructor() {
    this.opened = new Promise((resolve) => (this.openedResolve = resolve));
  }

  attach(response: ServerResponse): void {
    this.connections += 1;
    this.response = response;
    response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
    // The platform's stream answers at once (sse-starlette): headers go before the first event.
    response.flushHeaders();
    for (const chunk of this.backlog.splice(0)) response.write(chunk);
    this.openedResolve?.();
    if (this.ended) this.close();
  }

  raw(chunk: string): void {
    if (this.response) this.response.write(chunk);
    else this.backlog.push(chunk);
  }

  send(event: string, data: unknown, id?: string): void {
    const lines = [`event: ${event}`];
    if (id !== undefined) lines.push(`id: ${id}`);
    for (const line of JSON.stringify(data).split("\n")) lines.push(`data: ${line}`);
    this.raw(lines.join("\n") + "\n\n");
  }

  /** The platform ends the stream (a restart, a proxy's timeout). */
  close(): void {
    this.ended = this.response === undefined;
    this.response?.end();
    this.response = undefined;
  }
}

export class FakeApi {
  readonly requests: RecordedRequest[] = [];
  private readonly routes = new Map<string, Handler>();
  private readonly streams = new Map<string, StreamController>();

  private constructor(
    private readonly server: Server,
    readonly url: string,
  ) {}

  static async start(): Promise<FakeApi> {
    const holder: { api?: FakeApi } = {};
    const server = createServer((request, response) => {
      holder.api?.serve(request, response);
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const { port } = server.address() as AddressInfo;
    holder.api = new FakeApi(server, `http://127.0.0.1:${port}`);
    return holder.api;
  }

  async stop(): Promise<void> {
    for (const stream of this.streams.values()) stream.close();
    this.server.closeAllConnections();
    await new Promise<void>((resolve) => {
      this.server.close(() => {
        resolve();
      });
    });
  }

  handle(method: string, path: string, handler: Handler): void {
    this.routes.set(`${method} ${path}`, handler);
  }

  json(method: string, path: string, body: unknown, status = 200): void {
    this.handle(method, path, (_request, response) => {
      response.writeHead(status, { "content-type": "application/json" });
      response.end(JSON.stringify(body));
    });
  }

  /** An RFC 9457 problem, as `errors.py` writes one. */
  problem(method: string, path: string, status: number, title: string, detail = "", code?: string): void {
    const problem: Record<string, unknown> = { type: "about:blank", title, status };
    if (detail) problem.detail = detail;
    if (code) problem.errors = [{ error: code }];
    this.handle(method, path, (_request, response) => {
      response.writeHead(status, { "content-type": "application/problem+json" });
      response.end(JSON.stringify(problem));
    });
  }

  redirect(method: string, path: string, location: string): void {
    this.handle(method, path, (_request, response) => {
      response.writeHead(307, { location });
      response.end();
    });
  }

  stream(path: string): StreamController {
    const controller = new StreamController();
    this.streams.set(path, controller);
    this.handle("GET", path, (_request, response) => {
      controller.attach(response);
    });
    return controller;
  }

  requestsTo(method: string, path: string): RecordedRequest[] {
    return this.requests.filter((r) => r.method === method && r.path === path);
  }

  private serve(request: IncomingMessage, response: ServerResponse): void {
    const chunks: Buffer[] = [];
    request.on("data", (chunk: Buffer) => chunks.push(chunk));
    request.on("end", () => {
      const url = new URL(request.url ?? "/", this.url);
      const recorded: RecordedRequest = {
        method: request.method ?? "GET",
        path: url.pathname,
        query: url.searchParams,
        headers: request.headers,
        body: Buffer.concat(chunks).toString("utf-8"),
      };
      this.requests.push(recorded);
      const handler = this.routes.get(`${recorded.method} ${recorded.path}`);
      if (!handler) {
        response.writeHead(404, { "content-type": "application/problem+json" });
        response.end(JSON.stringify({ type: "about:blank", title: "Not found", status: 404 }));
        return;
      }
      handler(recorded, response);
    });
  }
}
