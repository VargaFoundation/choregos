import { describe, expect, it } from "vitest";
import {
  A2A_NAMES,
  a2aBlock,
  a2aEndpoint,
  a2aSnippets,
  exposureOf,
  machineCommands,
  personalTokenCommand,
  revokeCommand,
  skillFields,
  type Exposure,
  type SnippetContext,
} from "@/lib/a2a-snippets";
import type { WorkflowDef } from "@/lib/types";

type Served = Extract<Exposure, { exposed: true }>;

const def = (name: string, json: Record<string, unknown>) => ({ name, json }) as unknown as Pick<WorkflowDef, "name" | "json">;

const SHIP: Served = {
  workflow: "ship-a-change",
  exposed: true,
  skillId: "billing-api/ship-a-change",
  description: "Ship a change.",
  inputModes: ["text/plain", "application/json"],
  fields: skillFields({
    required: ["ticket", "count"],
    properties: {
      ticket: { type: "string" },
      count: { type: "integer" },
      priority: { type: "string", enum: ["low", "high"] },
    },
  }),
};
const PDF_ONLY: Served = { ...SHIP, skillId: "contracts/review", inputModes: ["application/pdf"], fields: [] };
const CONTEXTS: SnippetContext[] = [
  { origin: "https://choregos.example/mcp", org: "acme", skill: SHIP },
  { origin: "https://choregos.example", org: "acme", skill: SHIP, token: "chg_just_minted" },
  { origin: "https://choregos.example", org: "acme", skill: PDF_ONLY },
  { origin: "http://localhost:3000/mcp", org: "acme", skill: null },
];
const ALL_CODE = CONTEXTS.flatMap((context) => a2aSnippets(context)).map((snippet) => snippet.code);
const EXTRA_COMMANDS = [
  machineCommands("acme:billing-api"),
  personalTokenCommand("acme:billing-api"),
  personalTokenCommand(null),
  revokeCommand("acme:billing-api", "partner-app"),
];

/** Each `choregos <group> <command> …` invocation, with the options it passes (continuation lines joined). */
function invocations(code: string): Array<{ command: string[]; options: string[] }> {
  return code
    .replace(/\\\n\s*/g, " ")
    .split("\n")
    .filter((line) => line.startsWith("choregos "))
    .map((line) => {
      const words = line.split(/\s+/);
      return { command: words.slice(1, 3), options: [...line.matchAll(/(?:^|\s)(--[a-z][a-z-]*)/g)].map((m) => m[1]!) };
    });
}

describe("the A2A snippets name only what the door and the CLI have (a2a-names.json)", () => {
  it("every JSON-RPC method shown is in the list, for its version", () => {
    const shown03 = new Set<string>();
    const shown10 = new Set<string>();
    for (const context of CONTEXTS) {
      for (const snippet of a2aSnippets(context)) {
        const methods = [...snippet.code.matchAll(/"method": "([^"]+)"/g)].map((m) => m[1]!);
        if (snippet.id === "cli") expect(methods).toEqual([]);
        if (snippet.id === "curl") methods.forEach((m) => shown03.add(m));
        if (snippet.id === "a2a-1.0") methods.forEach((m) => shown10.add(m));
      }
    }
    expect([...shown03].sort()).toEqual([...A2A_NAMES.methods_0_3].sort());
    expect([...shown10].sort()).toEqual([...A2A_NAMES.methods_1_0].sort());
  });

  it("every CLI command and option shown is in the list, and every listed one is shown", () => {
    const listed = new Map(A2A_NAMES.commands.map((entry) => [entry.command.join(" "), new Set(entry.options)]));
    const used = new Map<string, Set<string>>();
    for (const code of [...ALL_CODE, ...EXTRA_COMMANDS]) {
      for (const { command, options } of invocations(code)) {
        const key = command.join(" ");
        expect(listed.has(key), `choregos ${key} is not in a2a-names.json`).toBe(true);
        for (const option of options) expect(listed.get(key)!.has(option), `choregos ${key} ${option}`).toBe(true);
        used.set(key, new Set([...(used.get(key) ?? []), ...options]));
      }
    }
    for (const [key, options] of listed) {
      expect(used.has(key), `choregos ${key} is listed but no snippet shows it`).toBe(true);
      for (const option of options) expect(used.get(key)!.has(option), `choregos ${key} ${option} unused`).toBe(true);
    }
  });

  it("every environment variable the CLI snippet sets is in the list", () => {
    const set = new Set(ALL_CODE.flatMap((code) => [...code.matchAll(/\b(CHOREGOS_[A-Z0-9_]+)=/g)].map((m) => m[1]!)));
    expect([...set].sort()).toEqual([...A2A_NAMES.env].sort());
  });
});

describe("the A2A snippets carry the instance's values", () => {
  it("the door's address is beside /mcp, under the organisation", () => {
    expect(a2aEndpoint("https://choregos.example/mcp", "acme")).toBe("https://choregos.example/a2a/orgs/acme");
    expect(a2aEndpoint("https://choregos.example/", "a b")).toBe("https://choregos.example/a2a/orgs/a%20b");
  });

  it("the skill, its required fields with values the schema accepts, and the token placeholder", () => {
    const [cli, curl, v1] = a2aSnippets(CONTEXTS[0]!);
    expect(cli!.code).toContain("CHOREGOS_API_URL=https://choregos.example CHOREGOS_ORG=acme CHOREGOS_A2A_TOKEN=<token>");
    expect(cli!.code).toContain("--field 'ticket=<ticket>'");
    expect(cli!.code).toContain("--field 'count=1'");
    expect(cli!.code).not.toContain("priority");
    expect(curl!.code).toContain('"data": {');
    expect(curl!.code).toContain('"ticket": "<ticket>"');
    expect(curl!.code).toContain('"kind": "text"');
    expect(v1!.code).toContain('"role": "ROLE_USER"');
    expect(v1!.code).not.toContain('"kind"');
  });

  it("a token just minted replaces the placeholder in every tab", () => {
    for (const snippet of a2aSnippets(CONTEXTS[1]!)) {
      expect(snippet.code).toContain("chg_just_minted");
      expect(snippet.code).not.toContain("<token>");
    }
  });

  it("a skill that takes a file only is sent a file, in each version's spelling", () => {
    const [cli, curl, v1] = a2aSnippets(CONTEXTS[2]!);
    expect(cli!.code).toContain("--file './document;type=application/pdf'");
    expect(cli!.code).not.toContain("--text");
    expect(curl!.code).toContain('"mimeType": "application/pdf"');
    expect(v1!.code).toContain('"mediaType": "application/pdf"');
  });

  it("a quote in a value cannot close the shell's quoting", () => {
    const quoted: Served = { ...SHIP, fields: [{ name: "note", required: true, example: "it's" }] };
    const [cli, curl] = a2aSnippets({ origin: "https://x.example", org: "acme", skill: quoted });
    expect(cli!.code).toContain(`--field 'note=it'\\''s'`);
    expect(curl!.code).toContain(`"note": "it'\\''s"`);
  });
});

describe("which workflows the door serves", () => {
  it("a workflow with `a2a:` is a skill named after its project, with its fields required first", () => {
    const served = exposureOf(
      "billing-api",
      def("ship-a-change", {
        metadata: { inputs: { required: ["b"], properties: { a: { type: "boolean" }, b: { type: "string", format: "date" } } } },
        a2a: { skill: "ship", description: "Ship." },
      }),
    );
    expect(served).toEqual({
      workflow: "ship-a-change",
      exposed: true,
      skillId: "billing-api/ship",
      description: "Ship.",
      inputModes: ["text/plain"],
      fields: [
        { name: "b", required: true, example: "2026-01-31" },
        { name: "a", required: false, example: true },
      ],
    });
  });

  it("a required field the schema does not describe stays required", () => {
    expect(skillFields({ required: ["ticket"] })).toEqual([{ name: "ticket", required: true, example: "<ticket>" }]);
    expect(skillFields(undefined)).toEqual([]);
  });

  it("a workflow without it is not served, and its block is prefilled with its name", () => {
    expect(exposureOf("billing-api", def("hotfix", { metadata: { description: "Fix: fast" } }))).toEqual({
      workflow: "hotfix",
      exposed: false,
      description: "Fix: fast",
    });
    const block = a2aBlock("hotfix", "Fix: fast");
    expect(block.split("\n").slice(0, 4)).toEqual([
      "a2a:",
      "  skill: hotfix",
      '  description: "Fix: fast"',
      "  input_modes: [text/plain, application/json]",
    ]);
    expect(a2aBlock("hotfix")).toContain("description: Start the hotfix workflow.");
  });
});
