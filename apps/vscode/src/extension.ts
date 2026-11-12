// SPDX-License-Identifier: Apache-2.0
/**
 * The Choregos extension: a developer surface of the platform (ADR 0155). It shows what the platform serves
 * and runs nothing on the person's machine.
 */
import * as vscode from "vscode";
import { redact } from "./log";
import { EmptyTree, VIEWS } from "./views";

export const COMMANDS = { showLog: "choregos.showLog" } as const;

export function activate(context: vscode.ExtensionContext): void {
  const log = vscode.window.createOutputChannel("Choregos", { log: true });
  context.subscriptions.push(log);
  const write = (line: string): void => {
    log.info(redact(line));
  };
  for (const id of [VIEWS.live, VIEWS.decisions, VIEWS.elsewhere]) {
    context.subscriptions.push(vscode.window.createTreeView(id, { treeDataProvider: new EmptyTree() }));
  }
  context.subscriptions.push(
    vscode.commands.registerCommand(COMMANDS.showLog, () => {
      log.show(true);
    }),
  );
  const manifest = context.extension.packageJSON as { version?: unknown };
  write(`Choregos ${String(manifest.version)} activated`);
}

export function deactivate(): void {
  // Everything is disposed through `context.subscriptions`.
}
