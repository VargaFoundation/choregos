// SPDX-License-Identifier: Apache-2.0
/**
 * The Choregos extension: a developer surface of the platform (ADR 0155). It shows what the platform serves
 * and runs nothing on the person's machine.
 */
import * as vscode from "vscode";
import { AUTH_LABEL, AUTH_TYPE, ChoregosAuthProvider } from "./auth";
import { redact } from "./log";
import { SessionStore } from "./session";
import { EmptyTree, VIEWS } from "./views";

export const COMMANDS = {
  showLog: "choregos.showLog",
  signIn: "choregos.signIn",
  signOut: "choregos.signOut",
} as const;

export function instanceUrl(): string {
  return vscode.workspace.getConfiguration("choregos").get<string>("instanceUrl", "");
}

export interface Extension {
  auth: ChoregosAuthProvider;
}

export function activate(context: vscode.ExtensionContext): Extension {
  const log = vscode.window.createOutputChannel("Choregos", { log: true });
  context.subscriptions.push(log);
  const write = (line: string): void => {
    log.info(redact(line));
  };
  const manifest = context.extension.packageJSON as { version?: unknown };
  const version = String(manifest.version);

  const auth = new ChoregosAuthProvider({
    store: new SessionStore(context.secrets),
    instanceUrl,
    userAgent: `choregos-vscode/${version}`,
    log: write,
  });
  context.subscriptions.push(
    auth,
    vscode.authentication.registerAuthenticationProvider(AUTH_TYPE, AUTH_LABEL, auth, {
      supportsMultipleAccounts: false,
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("choregos.instanceUrl")) auth.instanceChanged();
    }),
  );

  for (const id of [VIEWS.live, VIEWS.decisions, VIEWS.elsewhere]) {
    context.subscriptions.push(vscode.window.createTreeView(id, { treeDataProvider: new EmptyTree() }));
  }
  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.showLog, () => {
      log.show(true);
    }),
    vscode.commands.registerCommand(COMMANDS.signIn, async () => {
      if (!instanceUrl()) {
        await vscode.commands.executeCommand("workbench.action.openSettings", "choregos.instanceUrl");
        return;
      }
      await vscode.authentication.getSession(AUTH_TYPE, [], { createIfNone: true });
    }),
    vscode.commands.registerCommand(COMMANDS.signOut, async () => {
      const stored = await auth.current();
      if (stored) await auth.removeSession(stored.id);
    }),
  );
  write(`Choregos ${version} activated`);
  return { auth };
}

export function deactivate(): void {
  // Everything is disposed through `context.subscriptions`.
}
