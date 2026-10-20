// SPDX-License-Identifier: Apache-2.0
/**
 * The project's network access, written into its policy YAML (S41-22). The API stores a project's
 * network as `sandbox.network` of its policy and takes the policy as YAML only, so the network form
 * edits that one block in the text: every other line, comments included, is sent back as it was typed.
 * The YAML tree is CodeMirror's own parser (`@lezer/yaml`), already shipped with the policy editor.
 * The server stays the judge: it validates the whole policy on save and clamps it under the ceiling.
 */
import { parser } from "@lezer/yaml";

type SyntaxNode = ReturnType<typeof parser.parse>["topNode"];

/** A project's `sandbox.network`, as `policy.schema.json` declares it. */
export type SandboxNetwork = {
  allow_domains?: string[];
  deny_domains?: string[];
  per_host_per_minute?: number | null;
  per_run_per_minute?: number | null;
  builtin_web_tools?: "allow" | "deny";
  deny_by_default?: boolean;
};

export type Written = { ok: true; yaml: string } | { ok: false; reason: string };

const ORDER = ["allow_domains", "deny_domains", "per_host_per_minute", "per_run_per_minute", "builtin_web_tools", "deny_by_default"] as const;
const PUNCTUATION = new Set([":", "-", "[", "]", "{", "}", ",", "Comment"]);
const PLAIN = /^[a-z0-9][a-z0-9_.-]*$/;
const RESERVED = /^(true|false|yes|no|on|off|null|y|n|~)$/i;

function unquote(raw: string): string {
  if (raw.startsWith('"')) return JSON.parse(raw) as string;
  if (raw.startsWith("'")) return raw.slice(1, -1).replaceAll("''", "'");
  return raw;
}

function scalar(raw: string): unknown {
  if (/^(null|~)?$/.test(raw)) return null;
  if (raw === "true" || raw === "false") return raw === "true";
  if (/^-?\d+$/.test(raw)) return Number(raw);
  return raw;
}

/** The node a pair maps its key to, or `null` when the key has no value (`sandbox:` alone). */
function pairValue(pair: SyntaxNode): SyntaxNode | null {
  let node = pair.getChild(":")?.nextSibling ?? null;
  while (node && node.name === "Comment") node = node.nextSibling;
  return node;
}

function keyOf(pair: SyntaxNode, text: string): string {
  const key = pair.getChild("Key");
  return key ? unquote(text.slice(key.from, key.to).trim()) : "";
}

function pairs(mapping: SyntaxNode): SyntaxNode[] {
  return mapping.getChildren("Pair");
}

/** A YAML node as a value: mappings, sequences and plain or quoted scalars, block or flow. */
function valueOf(node: SyntaxNode | null, text: string): unknown {
  if (!node) return null;
  const raw = text.slice(node.from, node.to);
  switch (node.name) {
    case "BlockMapping":
    case "FlowMapping":
      return Object.fromEntries(pairs(node).map((pair) => [keyOf(pair, text), valueOf(pairValue(pair), text)]));
    case "BlockSequence":
    case "FlowSequence": {
      const items: unknown[] = [];
      for (let child = node.firstChild; child; child = child.nextSibling) {
        if (!PUNCTUATION.has(child.name)) items.push(valueOf(child, text));
      }
      return items;
    }
    case "Item": {
      let child = node.firstChild;
      while (child && PUNCTUATION.has(child.name)) child = child.nextSibling;
      return valueOf(child, text);
    }
    case "QuotedLiteral":
      return unquote(raw);
    default:
      return scalar(raw.trim());
  }
}

function parse(text: string): { mapping: SyntaxNode | null; problem: string | null } {
  const tree = parser.parse(text);
  let broken = false;
  tree.iterate({
    enter: (node) => {
      if (node.type.isError) broken = true;
    },
  });
  if (broken) return { mapping: null, problem: "the policy YAML does not parse: fix it in the Policy editor first" };
  const documents = tree.topNode.getChildren("Document");
  if (documents.length > 1) return { mapping: null, problem: "the policy holds several YAML documents: edit it in the Policy editor" };
  const document = documents[0];
  if (!document) return { mapping: null, problem: null };
  const mapping = document.getChild("BlockMapping");
  if (!mapping) {
    const other = document.firstChild && !PUNCTUATION.has(document.firstChild.name);
    return { mapping: null, problem: other ? "the policy is not a YAML mapping: edit it in the Policy editor" : null };
  }
  return { mapping, problem: null };
}

/** The project's `sandbox.network` read from its policy YAML, `{}` when the policy sets none. */
export function readSandboxNetwork(text: string): SandboxNetwork | null {
  const { mapping, problem } = parse(text);
  if (problem) return null;
  if (!mapping) return {};
  const policy = valueOf(mapping, text) as { sandbox?: { network?: SandboxNetwork | null } | null };
  return policy.sandbox?.network ?? {};
}

function quoted(host: string): string {
  return PLAIN.test(host) && !RESERVED.test(host) ? host : JSON.stringify(host);
}

/** The lines of the `network:` value at `indent` spaces, each preceded by its newline. */
function block(network: SandboxNetwork, indent: number): string {
  const pad = " ".repeat(indent);
  const lines: string[] = [];
  for (const key of ORDER) {
    const value = network[key];
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      if (value.length === 0) lines.push(`${pad}${key}: []`);
      else lines.push(`${pad}${key}:`, ...value.map((host) => `${pad}  - ${quoted(host)}`));
    } else {
      lines.push(`${pad}${key}: ${typeof value === "string" ? quoted(value) : String(value)}`);
    }
  }
  return lines.length === 0 ? " {}" : `\n${lines.join("\n")}`;
}

function column(text: string, at: number): number {
  return at - (text.lastIndexOf("\n", at - 1) + 1);
}

function isEmpty(node: SyntaxNode | null, text: string): boolean {
  if (!node) return true;
  if (node.name === "FlowMapping") return pairs(node).length === 0;
  return node.name === "Literal" && /^(null|~)$/.test(text.slice(node.from, node.to).trim());
}

function splice(text: string, from: number, to: number, insert: string): string {
  return text.slice(0, from) + insert + text.slice(to);
}

/** Keeps only what the policy schema reads, so a comparison is not fooled by key order or an empty value. */
function normalised(network: SandboxNetwork): SandboxNetwork {
  return Object.fromEntries(ORDER.filter((key) => network[key] !== undefined && network[key] !== null).map((key) => [key, network[key]]));
}

/**
 * `text` with its `sandbox.network` replaced by `network`. Refused, never guessed, when the policy does
 * not parse, holds several documents, writes `sandbox` in flow style, or when reading the result back
 * would not give `network` exactly.
 */
export function withSandboxNetwork(text: string, network: SandboxNetwork): Written {
  const { mapping, problem } = parse(text);
  if (problem) return { ok: false, reason: problem };
  let yaml: string;
  if (!mapping) {
    yaml = `${text}${text && !text.endsWith("\n") ? "\n" : ""}sandbox:\n  network:${block(network, 4)}\n`;
  } else {
    const first = pairs(mapping)[0];
    const top = first ? column(text, first.from) : 0;
    const sandbox = pairs(mapping).find((pair) => keyOf(pair, text) === "sandbox");
    if (!sandbox) {
      const pad = " ".repeat(top);
      yaml = splice(text, mapping.to, mapping.to, `\n${pad}sandbox:\n${pad}  network:${block(network, top + 4)}`);
    } else {
      const value = pairValue(sandbox);
      const at = column(text, sandbox.from);
      if (isEmpty(value, text)) {
        const pad = " ".repeat(at);
        yaml = splice(text, sandbox.from, sandbox.to, `sandbox:\n${pad}  network:${block(network, at + 4)}`);
      } else if (value?.name !== "BlockMapping") {
        return { ok: false, reason: "`sandbox` is not written as a block mapping: edit it in the Policy editor" };
      } else {
        const inner = pairs(value);
        const own = inner.find((pair) => keyOf(pair, text) === "network");
        const indent = inner[0] ? column(text, inner[0].from) : at + 2;
        if (own) yaml = splice(text, own.from, own.to, `network:${block(network, indent + 2)}`);
        else yaml = splice(text, value.to, value.to, `\n${" ".repeat(indent)}network:${block(network, indent + 2)}`);
      }
    }
  }
  const back = readSandboxNetwork(yaml);
  if (back === null || JSON.stringify(normalised(back)) !== JSON.stringify(normalised(network))) {
    return { ok: false, reason: "the network could not be written into this policy: edit `sandbox.network` in the Policy editor" };
  }
  return { ok: true, yaml };
}
