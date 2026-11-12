// SPDX-License-Identifier: Apache-2.0
/**
 * The editor's side of the sign-in (ADR 0155 §5): an `AuthenticationProvider` the Accounts menu lists, one
 * account per instance, its credential in `SecretStorage` only.
 */
import * as vscode from "vscode";
import { SessionStore, signInWithToken, SignInRefused, type StoredSession } from "./session";

export const AUTH_TYPE = "choregos";
export const AUTH_LABEL = "Choregos";

export interface AuthDeps {
  store: SessionStore;
  instanceUrl: () => string;
  userAgent: string;
  log: (line: string) => void;
}

function toSession(stored: StoredSession): vscode.AuthenticationSession {
  return {
    id: stored.id,
    accessToken: stored.credential,
    account: { id: stored.email, label: `${stored.displayName} (${new URL(stored.origin).host})` },
    scopes: [],
  };
}

export class ChoregosAuthProvider implements vscode.AuthenticationProvider, vscode.Disposable {
  private readonly changes =
    new vscode.EventEmitter<vscode.AuthenticationProviderAuthenticationSessionsChangeEvent>();
  readonly onDidChangeSessions = this.changes.event;

  constructor(private readonly deps: AuthDeps) {}

  /** The stored session of the configured instance, for the client and for the gestures that need its kind. */
  current(): Promise<StoredSession | undefined> {
    return this.deps.store.get(this.deps.instanceUrl());
  }

  async getSessions(): Promise<vscode.AuthenticationSession[]> {
    const stored = await this.current();
    return stored ? [toSession(stored)] : [];
  }

  async createSession(): Promise<vscode.AuthenticationSession> {
    const pasted = await vscode.window.showInputBox({
      title: "Sign in to Choregos",
      prompt:
        "Your own API token (scope `*`), from the console: My account → API tokens. It reads what you may read; it decides nothing.",
      password: true,
      ignoreFocusOut: true,
    });
    if (pasted === undefined) throw new vscode.CancellationError();
    let stored: StoredSession;
    try {
      stored = await signInWithToken(pasted, {
        instanceUrl: this.deps.instanceUrl(),
        userAgent: this.deps.userAgent,
      });
    } catch (error) {
      const message = error instanceof SignInRefused ? error.message : "the sign-in failed";
      this.deps.log(`sign-in refused: ${message}`);
      void vscode.window.showErrorMessage(`Choregos: ${message}`);
      throw error;
    }
    const previous = await this.current();
    await this.deps.store.put(stored);
    const session = toSession(stored);
    this.changes.fire({
      added: [session],
      removed: previous && previous.id !== stored.id ? [toSession(previous)] : [],
      changed: previous?.id === stored.id ? [session] : [],
    });
    this.deps.log(`signed in to ${stored.origin} as ${stored.email}`);
    return session;
  }

  async removeSession(sessionId: string): Promise<void> {
    const stored = await this.current();
    if (stored?.id !== sessionId) return;
    await this.deps.store.forget(stored.origin);
    this.changes.fire({ added: [], removed: [toSession(stored)], changed: [] });
    this.deps.log(`signed out of ${stored.origin}`);
  }

  /** The instance address changed: the sessions the editor lists are another instance's. */
  instanceChanged(): void {
    this.changes.fire({ added: [], removed: [], changed: [] });
  }

  dispose(): void {
    this.changes.dispose();
  }
}
