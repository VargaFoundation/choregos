// SPDX-License-Identifier: Apache-2.0
/** The Live tree view and the status line, drawn from the feed (ADR 0155 §2-3). */
import * as vscode from "vscode";
import type { LiveFeed } from "./feed";
import { figures, money } from "./format";
import { entryLabel, type LiveNode } from "./model";
import { LOOKS, lookOf, type Look } from "./states";

/** "Waiting for you" has no route yet (ADR 0154 G8): it reads `—`, never a count the editor made up. */
export const WAITING_UNKNOWN = "—";

function icon(look: Look): vscode.ThemeIcon {
  return new vscode.ThemeIcon(look.icon, new vscode.ThemeColor(look.color));
}

export class LiveTree implements vscode.TreeDataProvider<LiveNode>, vscode.Disposable {
  private readonly changes = new vscode.EventEmitter<LiveNode | undefined>();
  readonly onDidChangeTreeData = this.changes.event;
  private readonly subscription: { dispose(): void };

  constructor(
    private readonly feed: LiveFeed,
    private readonly now: () => Date = () => new Date(),
  ) {
    this.subscription = feed.onDidChange(() => {
      this.changes.fire(undefined);
    });
  }

  getChildren(node?: LiveNode): LiveNode[] {
    if (!node) return this.feed.model.tree(this.now());
    return node.kind === "entry" ? [] : node.children;
  }

  getTreeItem(node: LiveNode): vscode.TreeItem {
    if (node.kind !== "entry") {
      const item = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.Expanded);
      item.id = node.key;
      const look = node.state ? LOOKS[node.state] : undefined;
      if (node.kind === "project") item.iconPath = new vscode.ThemeIcon("project");
      else if (look) item.iconPath = icon(look);
      item.description = node.detail ?? "";
      item.contextValue = node.kind;
      const words = [node.kind === "project" ? "project" : "work item", node.label];
      if (node.detail) words.push(`workflow ${node.detail}`);
      if (look) words.push(look.word);
      item.accessibilityInformation = { label: words.join(", ") };
      item.tooltip = words.join(" · ");
      return item;
    }
    const entry = node.entry;
    const look = lookOf(entry);
    const label = entryLabel(entry);
    const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
    item.id = node.key;
    item.iconPath = icon(look);
    const numbers = figures(entry, this.now());
    item.description = numbers;
    item.contextValue = `${entry.kind}${entry.can_stop ? ".stoppable" : ""}`;
    item.accessibilityInformation = { label: `${label}, ${look.word}, ${numbers.replaceAll(" · ", ", ")}` };
    const lines = [`${label} — ${look.word}`, numbers];
    if (entry.model) lines.push(`model ${entry.model}`);
    if (entry.last_event) lines.push(`last step: ${entry.last_event} (${entry.steps ?? 0} so far)`);
    if (entry.initiated_by) lines.push(`started by ${entry.initiated_by}`);
    item.tooltip = lines.join("\n");
    return item;
  }

  /** The view's message: whether what it shows is live, and why not. */
  message(): string | undefined {
    const state = this.feed.state;
    switch (state.kind) {
      case "live":
        return undefined;
      case "connecting":
        return `Connecting to ${state.org}…`;
      case "polling":
        return `Not live: ${state.reason}. Listing again every 30 s.`;
      case "no-organisation":
        return state.reason;
      case "signed-out":
        return undefined;
    }
  }

  refresh(): void {
    this.changes.fire(undefined);
  }

  dispose(): void {
    this.subscription.dispose();
    this.changes.dispose();
  }
}

/** `N running · — waiting for you · $X today`, the figures the platform gave and `—` for the others. */
export function statusText(feed: LiveFeed): { text: string; tooltip: string } {
  const state = feed.state;
  if (state.kind === "signed-out") {
    return { text: "$(account) Choregos: sign in", tooltip: "Sign in to your Choregos instance" };
  }
  const live = state.kind === "live";
  const text = [
    `${live ? "$(pulse)" : "$(debug-disconnect)"} ${feed.model.running()} running`,
    `${WAITING_UNKNOWN} waiting for you`,
    `${money(feed.spendToday)} today`,
  ].join(" · ");
  const lines = [
    live ? `Live: ${state.org}` : `Not live (${state.kind === "polling" ? state.reason : state.kind})`,
    "Waiting for you: your instance cannot list it yet.",
  ];
  if (feed.spendToday === undefined) lines.push("Spend today: not readable with your role, or unknown.");
  return { text, tooltip: lines.join("\n") };
}
