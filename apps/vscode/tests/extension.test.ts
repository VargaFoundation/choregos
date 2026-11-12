// SPDX-License-Identifier: Apache-2.0
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { activate, COMMANDS } from "../src/extension";
import { redact } from "../src/log";
import { VIEWS } from "../src/views";
import { extensionContext, fake } from "./fakes/vscode";
import type * as vscode from "vscode";

interface Manifest {
  contributes: { views: Record<string, { id: string }[]>; commands: { command: string }[] };
  activationEvents: string[];
}

const manifest = JSON.parse(
  readFileSync(resolve(import.meta.dirname, "../package.json"), "utf-8"),
) as Manifest;

function start(): ReturnType<typeof extensionContext> {
  const context = extensionContext("1.2.3");
  activate(context as unknown as vscode.ExtensionContext);
  return context;
}

beforeEach(() => {
  fake.reset();
});

describe("activation", () => {
  it("registers a provider for every view the manifest declares, and only those", () => {
    start();
    const declared = (manifest.contributes.views.choregos ?? []).map((v) => v.id).sort();
    expect([...fake.treeViews.keys()].sort()).toEqual(declared);
    expect(declared).toEqual(Object.values(VIEWS).sort());
  });

  it("registers every command the manifest declares", () => {
    start();
    const declared = manifest.contributes.commands.map((c) => c.command).sort();
    expect([...fake.commands.keys()].sort()).toEqual(declared);
    expect(declared).toEqual(Object.values(COMMANDS).sort());
  });

  it("disposes what it registered with the context", () => {
    const context = start();
    for (const item of context.subscriptions) item.dispose();
    expect(fake.treeViews.size).toBe(0);
    expect(fake.commands.size).toBe(0);
  });

  it("writes to its own output channel, which the log command shows", async () => {
    start();
    const channel = fake.outputs.get("Choregos");
    expect(channel?.lines).toEqual(["Choregos 1.2.3 activated"]);
    await fake.commands.get(COMMANDS.showLog)?.();
    expect(channel?.shown).toBe(true);
  });
});

describe("the log", () => {
  it.each([
    ["Authorization: Bearer chg_abc-DEF_123", "Authorization: Bearer ***"],
    ["bearer eyJhbGciOi.payload.sig refused", "bearer *** refused"],
    ["token chg_abc-DEF_123 expired", "token *** expired"],
    ["GET /api/v1/me → 200", "GET /api/v1/me → 200"],
  ])("masks credentials: %j", (line, expected) => {
    expect(redact(line)).toBe(expected);
  });
});
