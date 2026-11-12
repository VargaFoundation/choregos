// SPDX-License-Identifier: Apache-2.0
import type { Me } from "@choregos/contracts/api";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type * as vscode from "vscode";
import { AUTH_TYPE } from "../src/auth";
import { activate, COMMANDS, type Extension } from "../src/extension";
import { mayDecide, secretKey, SessionStore, signInWithToken, type StoredSession } from "../src/session";
import { FakeApi } from "./fakes/api";
import { authentication, extensionContext, fake } from "./fakes/vscode";

const TOKEN = "chg_ada-own-token-0123456789";
const ME: Me = { id: "u-ada", email: "ada@example.com", display_name: "Ada Lovelace", memberships: [] };

let api: FakeApi;
let context: ReturnType<typeof extensionContext>;
let extension: Extension;

beforeEach(async () => {
  fake.reset();
  api = await FakeApi.start();
  fake.settings.set("choregos.instanceUrl", api.url);
  context = extensionContext("1.2.3");
  extension = activate(context as unknown as vscode.ExtensionContext);
});

afterEach(async () => {
  for (const item of context.subscriptions) item.dispose();
  await api.stop();
});

/** Everything the person could read or that leaves the secret storage: settings, log, messages. */
function everythingVisible(): string {
  return JSON.stringify({
    settings: [...fake.settings.entries()],
    log: fake.outputs.get("Choregos")?.lines,
    messages: fake.messages,
  });
}

function signIn(pasted: string): Promise<unknown> {
  fake.inputs.push(pasted);
  return Promise.resolve(fake.commands.get(COMMANDS.signIn)?.());
}

describe("signing in with the person's own token", () => {
  it("makes a session named by the person, checked with GET /me under that token", async () => {
    api.json("GET", "/api/v1/me", ME);
    await signIn(`  ${TOKEN}\n`);
    const [session] = (await fake.authProviders
      .get(AUTH_TYPE)
      ?.provider.getSessions()) as vscode.AuthenticationSession[];
    expect(session?.account.label).toBe(`Ada Lovelace (${new URL(api.url).host})`);
    expect(session?.accessToken).toBe(TOKEN);
    expect(api.requestsTo("GET", "/api/v1/me")[0]?.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(fake.inputBoxes[0]).toMatchObject({ password: true });
  });

  it("keeps the credential in secret storage only", async () => {
    api.json("GET", "/api/v1/me", ME);
    await signIn(TOKEN);
    const origin = new URL(api.url).origin;
    expect(JSON.parse(context.stored.get(secretKey(origin)) ?? "{}")).toMatchObject({
      credential: TOKEN,
      kind: "api-token",
    });
    expect([...context.stored.keys()]).toEqual([secretKey(origin)]);
    expect(everythingVisible()).not.toContain(TOKEN);
  });

  it("makes no session when the instance does not know the token, and says why without the token", async () => {
    api.problem("GET", "/api/v1/me", 401, "Unauthorized", "unknown or revoked API token");
    await expect(signIn(TOKEN)).rejects.toThrow("does not know this token (unknown or revoked API token)");
    expect(await extension.auth.getSessions()).toEqual([]);
    expect(context.stored.size).toBe(0);
    expect(fake.messages.map((m) => m.level)).toEqual(["error"]);
    expect(everythingVisible()).not.toContain(TOKEN);
  });

  it("refuses a token of another scope with the API's own words", async () => {
    const refusal =
      "this token is reserved for the MCP door or the A2A door (scope mcp:* or a2a); the REST API needs the `*` scope";
    api.problem("GET", "/api/v1/me", 403, "Forbidden", refusal);
    await expect(signIn(TOKEN)).rejects.toThrow(refusal);
    expect(context.stored.size).toBe(0);
    expect(fake.messages[0]?.text).toContain(refusal);
  });

  it("does nothing when the person presses Escape", async () => {
    await expect(signIn(undefined as unknown as string)).rejects.toThrow("Canceled");
    expect(api.requests).toHaveLength(0);
  });

  it("opens the settings instead when no instance is configured", async () => {
    fake.settings.set("choregos.instanceUrl", "");
    await fake.commands.get(COMMANDS.signIn)?.();
    expect(fake.executed.map((e) => e.id)).toContain("workbench.action.openSettings");
    expect(fake.inputBoxes).toHaveLength(0);
  });

  it("signing out forgets the credential and tells the editor", async () => {
    api.json("GET", "/api/v1/me", ME);
    await signIn(TOKEN);
    const removed: unknown[] = [];
    extension.auth.onDidChangeSessions((event) => removed.push(...(event.removed ?? [])));
    await fake.commands.get(COMMANDS.signOut)?.();
    expect(context.stored.size).toBe(0);
    expect(removed).toHaveLength(1);
    expect(await authentication.getSession(AUTH_TYPE, [])).toBeUndefined();
  });

  it("signing out of a session that is not the current one changes nothing", async () => {
    api.json("GET", "/api/v1/me", ME);
    await signIn(TOKEN);
    await extension.auth.removeSession("someone-else");
    expect(context.stored.size).toBe(1);
  });

  it("lists no session for another instance than the configured one", async () => {
    api.json("GET", "/api/v1/me", ME);
    await signIn(TOKEN);
    const changes: unknown[] = [];
    extension.auth.onDidChangeSessions((event) => changes.push(event));
    fake.set("choregos.instanceUrl", "https://other.example.com");
    expect(await extension.auth.getSessions()).toEqual([]);
    expect(changes).toHaveLength(1);
  });

  it("signing in again as the same person replaces the session rather than adding one", async () => {
    api.json("GET", "/api/v1/me", ME);
    await signIn(TOKEN);
    const events: vscode.AuthenticationProviderAuthenticationSessionsChangeEvent[] = [];
    extension.auth.onDidChangeSessions((event) => events.push(event));
    fake.inputs.push("chg_a-second-token-of-ada");
    await extension.auth.createSession();
    expect(events[0]?.removed).toEqual([]);
    expect(events[0]?.changed).toHaveLength(1);
    expect(await extension.auth.getSessions()).toHaveLength(1);
  });
});

describe("what a credential may do", () => {
  const base: StoredSession = {
    id: "x",
    origin: "https://c.example.com",
    kind: "api-token",
    credential: "chg_x",
    email: "ada@example.com",
    displayName: "Ada",
  };

  it("an API token decides nothing (ADR 0154 §7); only a developer-surface session may", () => {
    expect(mayDecide(base)).toBe(false);
    expect(mayDecide(undefined)).toBe(false);
    expect(mayDecide({ ...base, kind: "developer-session" })).toBe(true);
  });
});

describe("the sign-in, without the editor", () => {
  it.each([
    ["", "no token was given"],
    ["chg_two words", "holds no space"],
  ])("refuses %j before calling anyone", async (pasted, reason) => {
    await expect(signInWithToken(pasted, { instanceUrl: api.url, userAgent: "t" })).rejects.toThrow(reason);
    expect(api.requests).toHaveLength(0);
  });

  it("accepts a token pasted with its Bearer prefix", async () => {
    api.json("GET", "/api/v1/me", ME);
    const session = await signInWithToken(`Bearer ${TOKEN}`, { instanceUrl: api.url, userAgent: "t" });
    expect(session.credential).toBe(TOKEN);
  });

  it("refuses an instance in clear HTTP before sending the token", async () => {
    await expect(
      signInWithToken(TOKEN, { instanceUrl: "http://choregos.example.com", userAgent: "t" }),
    ).rejects.toThrow("would carry your credential in clear");
  });

  it("names any other answer of the instance", async () => {
    api.problem("GET", "/api/v1/me", 503, "Service Unavailable", "the database is starting");
    await expect(signInWithToken(TOKEN, { instanceUrl: api.url, userAgent: "t" })).rejects.toThrow(
      "answered 503: the database is starting",
    );
  });

  it("says when the instance cannot be reached", async () => {
    const down = api.url;
    await api.stop();
    await expect(signInWithToken(TOKEN, { instanceUrl: down, userAgent: "t" })).rejects.toThrow(
      "could not be reached",
    );
    api = await FakeApi.start();
  });

  it("reads back nothing from a damaged or foreign secret", async () => {
    const secrets = new Map<string, string>();
    const store = new SessionStore({
      get: (key) => Promise.resolve(secrets.get(key)),
      store: (key, value) => {
        secrets.set(key, value);
        return Promise.resolve();
      },
      delete: (key) => {
        secrets.delete(key);
        return Promise.resolve();
      },
    });
    secrets.set(secretKey("https://c.example.com"), "{not json");
    expect(await store.get("https://c.example.com")).toBeUndefined();
    secrets.set(
      secretKey("https://c.example.com"),
      JSON.stringify({ origin: "https://evil.example", credential: "x" }),
    );
    expect(await store.get("https://c.example.com")).toBeUndefined();
    expect(await store.get("not an address")).toBeUndefined();
  });
});
