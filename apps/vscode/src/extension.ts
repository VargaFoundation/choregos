// SPDX-License-Identifier: Apache-2.0
/**
 * The Choregos extension: a developer surface of the platform (ADR 0155). It shows what the platform serves
 * and runs nothing on the person's machine.
 */
import * as vscode from "vscode";
import { ChoregosClient } from "./api/client";
import { AUTH_LABEL, AUTH_TYPE, ChoregosAuthProvider } from "./auth";
import { LiveFeed } from "./live/feed";
import { LiveTree, statusText } from "./live/view";
import { redact } from "./log";
import { SessionStore } from "./session";
import { EmptyTree, VIEWS } from "./views";

export const COMMANDS = {
  showLog: "choregos.showLog",
  signIn: "choregos.signIn",
  signOut: "choregos.signOut",
} as const;

/** How often elapsed times are redrawn while something runs. */
export const TICK_MS = 5_000;

function setting(key: "instanceUrl" | "organisation"): string {
  return vscode.workspace.getConfiguration("choregos").get<string>(key, "");
}

export function instanceUrl(): string {
  return setting("instanceUrl");
}

export function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export interface Extension {
  auth: ChoregosAuthProvider;
  feed: LiveFeed;
  live: LiveTree;
  status: vscode.StatusBarItem;
}

export function activate(context: vscode.ExtensionContext): Extension {
  const log = vscode.window.createOutputChannel("Choregos", { log: true });
  context.subscriptions.push(log);
  const write = (line: string): void => {
    log.info(redact(line));
  };
  const manifest = context.extension.packageJSON as { version?: unknown };
  const version = String(manifest.version);
  const userAgent = `choregos-vscode/${version}`;

  const auth = new ChoregosAuthProvider({
    store: new SessionStore(context.secrets),
    instanceUrl,
    userAgent,
    log: write,
  });
  const feed = new LiveFeed({
    client: async () => {
      const session = await auth.current();
      if (!session) return undefined;
      return new ChoregosClient({
        instanceUrl: session.origin,
        credential: () => auth.current().then((s) => s?.credential),
        userAgent,
        log: write,
      });
    },
    organisation: () => setting("organisation"),
    sleep,
    log: write,
  });
  const live = new LiveTree(feed);
  const liveView = vscode.window.createTreeView(VIEWS.live, { treeDataProvider: live });
  const status = vscode.window.createStatusBarItem("choregos.status", vscode.StatusBarAlignment.Left, 50);
  status.name = "Choregos";
  const draw = (): void => {
    liveView.message = live.message() ?? "";
    const { text, tooltip } = statusText(feed);
    status.text = text;
    status.tooltip = tooltip;
    status.command = feed.state.kind === "signed-out" ? COMMANDS.signIn : "workbench.view.extension.choregos";
    status.show();
  };
  const restart = (): void => {
    void feed.restart().then(draw);
  };
  const tick = setInterval(() => {
    if (feed.model.running() > 0) live.refresh();
  }, TICK_MS);

  context.subscriptions.push(
    auth,
    vscode.authentication.registerAuthenticationProvider(AUTH_TYPE, AUTH_LABEL, auth, {
      supportsMultipleAccounts: false,
    }),
    auth.onDidChangeSessions(restart),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("choregos.instanceUrl")) auth.instanceChanged();
      else if (event.affectsConfiguration("choregos.organisation")) restart();
    }),
    live,
    liveView,
    status,
    feed.onDidChange(draw),
    { dispose: () => void feed.stop() },
    {
      dispose: () => {
        clearInterval(tick);
      },
    },
  );
  for (const id of [VIEWS.decisions, VIEWS.elsewhere]) {
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
  draw();
  restart();
  return { auth, feed, live, status };
}

export function deactivate(): void {
  // Everything is disposed through `context.subscriptions`.
}
