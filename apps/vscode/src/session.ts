// SPDX-License-Identifier: Apache-2.0
/**
 * The person's sign-in to one instance (ADR 0155 §5), without the editor's API: what is stored, where, and
 * what a credential may do.
 *
 * Until the platform offers the developer-surface session of ADR 0154 §6 (gap G9), the only credential is the
 * person's own API token of scope `*`, the one `choregos login --token` stores: it reads and stops what the
 * person may, and it decides nothing (ADR 0154 §7). The kind is stored with the credential so that every
 * decision gesture asks `mayDecide` rather than assume.
 */
import type { Me } from "@choregos/contracts/api";
import { ApiError, ChoregosClient, ClientRefusal, instanceOrigin } from "./api/client";

export type CredentialKind = "api-token" | "developer-session";

export interface StoredSession {
  /** Stable for one person on one instance: the editor keys its session list on it. */
  id: string;
  origin: string;
  kind: CredentialKind;
  credential: string;
  email: string;
  displayName: string;
}

/** The editor's `SecretStorage`, the only place a credential is kept. */
export interface Secrets {
  get(key: string): PromiseLike<string | undefined>;
  store(key: string, value: string): PromiseLike<void>;
  delete(key: string): PromiseLike<void>;
}

const KEY_PREFIX = "choregos.session:";

export function secretKey(origin: string): string {
  return KEY_PREFIX + origin;
}

/** A credential under which the platform accepts a decision or a write confirmation (ADR 0154 §7). */
export function mayDecide(session: StoredSession | undefined): boolean {
  return session?.kind === "developer-session";
}

export class SessionStore {
  constructor(private readonly secrets: Secrets) {}

  async get(instanceUrl: string): Promise<StoredSession | undefined> {
    let origin: string;
    try {
      origin = instanceOrigin(instanceUrl).origin;
    } catch {
      return undefined;
    }
    const raw = await this.secrets.get(secretKey(origin));
    if (!raw) return undefined;
    try {
      const session = JSON.parse(raw) as StoredSession;
      return session.origin === origin && session.credential ? session : undefined;
    } catch {
      return undefined;
    }
  }

  async put(session: StoredSession): Promise<void> {
    await this.secrets.store(secretKey(session.origin), JSON.stringify(session));
  }

  async forget(origin: string): Promise<void> {
    await this.secrets.delete(secretKey(origin));
  }
}

/** Why a sign-in did not make a session, in words the person can act on — never the credential itself. */
export class SignInRefused extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SignInRefused";
  }
}

export interface SignInOptions {
  instanceUrl: string;
  userAgent: string;
}

/** Checks a pasted API token with `GET /me` and returns the session it makes; refuses with the API's words. */
export async function signInWithToken(pasted: string, options: SignInOptions): Promise<StoredSession> {
  const credential = pasted.trim().replace(/^bearer\s+/i, "");
  if (!credential) throw new SignInRefused("no token was given");
  if (/\s/.test(credential))
    throw new SignInRefused("a token holds no space: paste it as the console shows it");
  let client: ChoregosClient;
  try {
    client = new ChoregosClient({
      instanceUrl: options.instanceUrl,
      credential: () => Promise.resolve(credential),
      userAgent: options.userAgent,
    });
  } catch (error) {
    throw new SignInRefused(error instanceof Error ? error.message : String(error));
  }
  let me: Me;
  try {
    me = await client.get<Me>("/me");
  } catch (error) {
    throw new SignInRefused(refusalOf(error, client.origin.origin));
  }
  return {
    id: `${client.origin.origin}#${me.id}`,
    origin: client.origin.origin,
    kind: "api-token",
    credential,
    email: me.email,
    displayName: me.display_name || me.email,
  };
}

function refusalOf(error: unknown, origin: string): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return `${origin} does not know this token (${error.detail || error.title})`;
    if (error.status === 403) return `${origin} refused this token: ${error.detail || error.title}`;
    return `${origin} answered ${error.status}: ${error.detail || error.title}`;
  }
  if (error instanceof ClientRefusal) return error.message;
  return `${origin} could not be reached: ${error instanceof Error ? error.message : String(error)}`;
}
