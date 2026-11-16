// SPDX-License-Identifier: Apache-2.0
import * as vscode from "vscode";

/** The views of the Choregos container (ADR 0155 §2); `package.json` declares the same ids. */
export const VIEWS = {
  live: "choregos.live",
  decisions: "choregos.decisions",
  elsewhere: "choregos.elsewhere",
} as const;

/** A view whose content a later story fills: empty, so the editor shows the view's welcome text. */
export class EmptyTree implements vscode.TreeDataProvider<never> {
  getTreeItem(element: never): vscode.TreeItem {
    return element;
  }

  getChildren(): never[] {
    return [];
  }
}
