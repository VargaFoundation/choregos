// SPDX-License-Identifier: Apache-2.0
import type { Me } from "@choregos/contracts/api";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ApiError, ChoregosClient, ClientRefusal, instanceOrigin } from "../src/api/client";
import { FakeApi } from "./fakes/api";

const TOKEN = "chg_the-persons-own-token";
let api: FakeApi;

beforeEach(async () => {
  api = await FakeApi.start();
});

afterEach(async () => {
  await api.stop();
});

function client(credential: string | null = TOKEN, log?: (line: string) => void): ChoregosClient {
  return new ChoregosClient({
    instanceUrl: api.url,
    credential: () => Promise.resolve(credential ?? undefined),
    userAgent: "choregos-vscode/0.0.0-test",
    ...(log ? { log } : {}),
  });
}

const ME: Me = { id: "u1", email: "ada@example.com", display_name: "Ada", memberships: [] };

describe("the client", () => {
  it("calls the API under /api/v1 with the person's bearer and its own user agent", async () => {
    api.json("GET", "/api/v1/me", ME);
    const me = await client().get<Me>("/me");
    expect(me.email).toBe("ada@example.com");
    const [request] = api.requestsTo("GET", "/api/v1/me");
    expect(request?.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(request?.headers["user-agent"]).toBe("choregos-vscode/0.0.0-test");
  });

  it("sends no authorization header when the person is not signed in", async () => {
    api.json("GET", "/api/v1/me", ME);
    await client(null).get("/me");
    expect(api.requestsTo("GET", "/api/v1/me")[0]?.headers.authorization).toBeUndefined();
  });

  it("does not follow a redirect with the credential", async () => {
    const elsewhere = await FakeApi.start();
    try {
      elsewhere.json("GET", "/api/v1/me", ME);
      api.redirect("GET", "/api/v1/me", `${elsewhere.url}/api/v1/me`);
      await expect(client().get("/me")).rejects.toBeInstanceOf(ClientRefusal);
      expect(elsewhere.requests).toHaveLength(0);
    } finally {
      await elsewhere.stop();
    }
  });

  it("reads the platform's problem document", async () => {
    api.problem("POST", "/api/v1/runs/r1/cancel", 409, "Conflict", "the run has already ended", "run_closed");
    const error = await client()
      .post("/runs/r1/cancel", { reason: "wrong branch" })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, detail: "the run has already ended", code: "run_closed" });
    expect(api.requestsTo("POST", "/api/v1/runs/r1/cancel")[0]?.body).toBe('{"reason":"wrong branch"}');
  });

  it("names the status when the answer is not a problem document", async () => {
    api.handle("GET", "/api/v1/me", (_request, response) => {
      response.writeHead(502);
      response.end("<html>bad gateway</html>");
    });
    await expect(client().get("/me")).rejects.toMatchObject({ status: 502, detail: "" });
  });

  it("passes query values and leaves the undefined ones out", async () => {
    api.json("GET", "/api/v1/orgs/acme/activity", { in_flight: [], finished: [] });
    await client().get("/orgs/acme/activity", { project: "shop", kind: undefined });
    const [request] = api.requestsTo("GET", "/api/v1/orgs/acme/activity");
    expect(request?.query.get("project")).toBe("shop");
    expect(request?.query.has("kind")).toBe(false);
  });

  it("answers nothing for a 204", async () => {
    api.handle("POST", "/api/v1/nothing", (_request, response) => {
      response.writeHead(204);
      response.end();
    });
    expect(await client().post("/nothing", {})).toBeUndefined();
  });

  it("logs the method, the path and the status, never the credential or the query", async () => {
    api.json("GET", "/api/v1/me", ME);
    const lines: string[] = [];
    await client(TOKEN, (line) => lines.push(line)).get("/me", { q: "secret-search" });
    expect(lines).toEqual(["GET /api/v1/me → 200"]);
  });

  it("refuses a path that would leave the API prefix", () => {
    expect(() => client().url("//elsewhere.example/x")).toThrow(ClientRefusal);
    expect(() => client().url("me")).toThrow(ClientRefusal);
  });

  it("streams server-sent events with the last event id", async () => {
    const live = api.stream("/api/v1/runs/r1/events");
    live.send("run.progress", { seq: 4 }, "4");
    live.send("run.progress", { seq: 5 }, "5");
    live.close();
    const events = [];
    for await (const event of client().stream("/runs/r1/events", undefined, { lastEventId: "3" })) {
      events.push(event);
    }
    expect(events.map((e) => e.id)).toEqual(["4", "5"]);
    const [request] = api.requestsTo("GET", "/api/v1/runs/r1/events");
    expect(request?.headers["last-event-id"]).toBe("3");
    expect(request?.headers.accept).toBe("text/event-stream");
  });

  it("refuses a stream the platform refuses", async () => {
    api.problem("GET", "/api/v1/orgs/acme/activity/stream", 403, "Forbidden", "not a member");
    const reading = (async () => {
      const events = client().stream("/orgs/acme/activity/stream");
      await events.next();
    })();
    await expect(reading).rejects.toMatchObject({ status: 403, detail: "not a member" });
  });
});

describe("the instance's address", () => {
  it("keeps the origin only", () => {
    expect(instanceOrigin(" https://choregos.example.com/console/ ").href).toBe(
      "https://choregos.example.com/",
    );
  });

  it.each([
    ["", "no Choregos instance is configured"],
    ["not a url", "is not an address"],
    ["http://choregos.example.com", "would carry your credential in clear"],
    ["https://ada:pw@choregos.example.com", "must not hold a user name"],
  ])("refuses %j", (address, reason) => {
    expect(() => instanceOrigin(address)).toThrow(reason);
  });

  it.each(["http://localhost:8000", "http://127.0.0.1:3000", "http://[::1]:8000"])(
    "accepts clear HTTP on the loopback: %s",
    (address) => {
      expect(instanceOrigin(address).protocol).toBe("http:");
    },
  );
});
