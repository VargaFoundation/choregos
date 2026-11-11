// SPDX-License-Identifier: Apache-2.0
/**
 * The A2A page of Integrations (S62-11), as pure functions: which workflows a project serves over the
 * door, the block that opens one, and the calls to paste — the CLI, `curl` in A2A 0.3.0, and A2A 1.0 —
 * with this instance's values.
 *
 * Every method and command the snippets show is listed in `a2a-names.json`: a test holds the snippets
 * to that list, and a Python test holds the list to the door's method tables and to the CLI's commands,
 * so a snippet cannot name a call that does not exist.
 */

import type { WorkflowDef } from "@/lib/types";
import { origineDe, PLACEHOLDER } from "@/lib/integrations";
import names from "./a2a-names.json";

export const A2A_NAMES = names;

/** One field a skill takes, from the workflow's `metadata.inputs`. */
export interface SkillField {
  name: string;
  required: boolean;
  /** A value that the schema accepts, for the examples. */
  example: string | number | boolean;
}

/** A workflow as the door sees it: served (`a2a:`), or not yet. */
export type Exposure =
  | {
      workflow: string;
      exposed: true;
      skillId: string;
      description: string;
      inputModes: string[];
      fields: SkillField[];
    }
  | { workflow: string; exposed: false; description: string | null };

interface JsonSchemaProperty {
  type?: string | string[];
  enum?: unknown[];
  format?: string;
  default?: unknown;
}

interface InputsSchema {
  required?: string[];
  properties?: Record<string, JsonSchemaProperty>;
}

interface WorkflowJson {
  metadata?: { name?: string; description?: string; inputs?: InputsSchema };
  a2a?: { skill: string; description: string; input_modes?: string[] };
}

function exampleOf(name: string, property: JsonSchemaProperty): SkillField["example"] {
  if (property.enum && property.enum.length > 0) {
    const first = property.enum[0];
    if (typeof first === "string" || typeof first === "number" || typeof first === "boolean") return first;
  }
  const type = Array.isArray(property.type) ? property.type.find((t) => t !== "null") : property.type;
  if (type === "integer" || type === "number") return 1;
  if (type === "boolean") return true;
  if (property.format === "date") return "2026-01-31";
  return `<${name}>`;
}

/** The fields of `metadata.inputs`, the required ones first, in the schema's order otherwise. */
export function skillFields(inputs: InputsSchema | null | undefined): SkillField[] {
  const required = new Set(inputs?.required ?? []);
  const fields = Object.entries(inputs?.properties ?? {}).map(([name, property]) => ({
    name,
    required: required.has(name),
    example: exampleOf(name, property ?? {}),
  }));
  // A required field the schema does not describe is still required.
  for (const name of required) {
    if (!fields.some((field) => field.name === name)) fields.push({ name, required: true, example: `<${name}>` });
  }
  return [...fields.filter((f) => f.required), ...fields.filter((f) => !f.required)];
}

/** What the door serves of one workflow of a project: the skill id is `<project slug>/<a2a.skill>`. */
export function exposureOf(projectSlug: string, definition: Pick<WorkflowDef, "name" | "json">): Exposure {
  const json = (definition.json ?? {}) as WorkflowJson;
  const description = json.metadata?.description ?? null;
  if (!json.a2a) return { workflow: definition.name, exposed: false, description };
  return {
    workflow: definition.name,
    exposed: true,
    skillId: `${projectSlug}/${json.a2a.skill}`,
    description: json.a2a.description,
    inputModes: json.a2a.input_modes ?? ["text/plain"],
    fields: skillFields(json.metadata?.inputs),
  };
}

/** A YAML scalar that needs no quoting is left bare; anything else is double-quoted. */
function yamlScalar(value: string): string {
  return /^[A-Za-z0-9][A-Za-z0-9 ,.()/-]*$/.test(value) && !/:\s/.test(value) ? value : JSON.stringify(value);
}

/**
 * The block that opens a workflow to the door, prefilled with its name: `metadata.inputs` is optional,
 * and goes under the workflow's existing `metadata:`.
 */
export function a2aBlock(workflow: string, description?: string | null): string {
  const what = description?.trim() || `Start the ${workflow} workflow.`;
  return [
    "a2a:",
    `  skill: ${workflow}`,
    `  description: ${yamlScalar(what.slice(0, 1000))}`,
    "  input_modes: [text/plain, application/json]",
    "# Optional, under the existing `metadata:` key: the fields a caller sends, checked by the door.",
    "# metadata:",
    "#   inputs:",
    "#     type: object",
    "#     required: [reference]",
    "#     properties:",
    "#       reference: { type: string }",
  ].join("\n");
}

/** What the snippets are written for: this instance, this organisation, one skill, a token if minted. */
export interface SnippetContext {
  /** The platform's origin, `https://choregos.example`, or the MCP URL it is derived from. */
  origin: string;
  org: string;
  skill: Extract<Exposure, { exposed: true }> | null;
  /** The token just minted, shown once; the placeholder otherwise. */
  token?: string | null;
}

export type SnippetId = "cli" | "curl" | "a2a-1.0";

export interface Snippet {
  id: SnippetId;
  label: string;
  where: string;
  code: string;
}

/** The door's address for an organisation: `<origin>/a2a/orgs/<org>`. */
export function a2aEndpoint(origin: string, org: string): string {
  return `${origineDe(origin).replace(/\/+$/, "")}/a2a/orgs/${encodeURIComponent(org)}`;
}

const MESSAGE_ID = "my-record-0001";
const TITLE = "A request from my application";
const TEXT = "Describe the request in words.";

/** `'…'` for the shell: a single quote inside closes, escapes and reopens. */
function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'\\''`)}'`;
}

function sendsText(skill: SnippetContext["skill"]): boolean {
  return !skill || skill.inputModes.includes("text/plain");
}

function sendsFields(skill: SnippetContext["skill"]): boolean {
  return Boolean(skill && skill.inputModes.includes("application/json") && skill.fields.length > 0);
}

/** A media type the skill takes for a file, when it takes neither text nor fields. */
function fileMode(skill: SnippetContext["skill"]): string | null {
  if (!skill || sendsText(skill) || skill.inputModes.includes("application/json")) return null;
  return skill.inputModes[0] ?? null;
}

function requiredData(skill: SnippetContext["skill"]): Record<string, SkillField["example"]> {
  const fields = skill?.fields.filter((field) => field.required) ?? [];
  return Object.fromEntries((fields.length > 0 ? fields : (skill?.fields.slice(0, 1) ?? [])).map((f) => [f.name, f.example]));
}

function cliSnippet(context: SnippetContext): Snippet {
  const skill = context.skill?.skillId ?? "<project>/<skill>";
  const send = [`choregos a2a send ${skill} --message-id ${MESSAGE_ID} --title ${shellQuote(TITLE)}`];
  if (sendsText(context.skill)) send.push(`--text ${shellQuote(TEXT)}`);
  if (sendsFields(context.skill)) {
    for (const [name, value] of Object.entries(requiredData(context.skill))) {
      send.push(`--field ${shellQuote(`${name}=${String(value)}`)}`);
    }
  }
  const mode = fileMode(context.skill);
  if (mode) send.push(`--file ${shellQuote(`./document;type=${mode}`)}`);
  send.push("--wait --out ./result");
  return {
    id: "cli",
    label: "CLI",
    where: "a terminal, with the choregos CLI installed",
    code: [
      `export CHOREGOS_API_URL=${origineDe(context.origin)} CHOREGOS_ORG=${context.org} CHOREGOS_A2A_TOKEN=${context.token ?? PLACEHOLDER}`,
      "# The workflows this token may start, and their fields (* = required)",
      "choregos a2a skills",
      "# Start one and follow it to its end; the message id is your idempotency key",
      send.join(" \\\n  "),
      "# Later: read it once, follow it again, or stop it",
      "choregos a2a get <task id> --json",
      "choregos a2a follow <task id> --out ./result",
      "choregos a2a cancel <task id>",
    ].join("\n"),
  };
}

type Version = "0.3" | "1.0";

function parts(context: SnippetContext, version: Version): unknown[] {
  const found: unknown[] = [];
  if (sendsText(context.skill)) found.push(version === "0.3" ? { kind: "text", text: TEXT } : { text: TEXT });
  if (sendsFields(context.skill)) {
    const data = requiredData(context.skill);
    found.push(version === "0.3" ? { kind: "data", data } : { data });
  }
  const mode = fileMode(context.skill);
  if (mode) {
    found.push(
      version === "0.3"
        ? { kind: "file", file: { name: "document", mimeType: mode, bytes: "<base64>" } }
        : { raw: "<base64>", mediaType: mode, filename: "document" },
    );
  }
  return found;
}

function call(method: string, params: unknown, id: number, version: Version, stream = false): string {
  const body = JSON.stringify({ jsonrpc: "2.0", id, method, params }, null, 2);
  const header = version === "1.0" ? ` -H 'A2A-Version: 1.0'` : "";
  return `curl -s${stream ? "N" : ""} "$A2A" -H "Authorization: Bearer $TOKEN"${header} -H 'Content-Type: application/json' -d ${shellQuote(body)}`;
}

function curlSnippet(context: SnippetContext, version: Version): Snippet {
  const skill = context.skill?.skillId ?? "<project>/<skill>";
  const message =
    version === "0.3"
      ? { kind: "message", role: "user", messageId: MESSAGE_ID, metadata: { skill, title: TITLE }, parts: parts(context, version) }
      : { role: "ROLE_USER", messageId: MESSAGE_ID, metadata: { skill, title: TITLE }, parts: parts(context, version) };
  const [send, get, follow, cancel] =
    version === "0.3"
      ? ["message/send", "tasks/get", "tasks/resubscribe", "tasks/cancel"]
      : ["SendMessage", "GetTask", "SubscribeToTask", "CancelTask"];
  const task = { id: "<task id>" };
  return {
    id: version === "0.3" ? "curl" : "a2a-1.0",
    label: version === "0.3" ? "curl (A2A 0.3.0)" : "A2A 1.0",
    where:
      version === "0.3"
        ? "a terminal, or any HTTP client: JSON-RPC over HTTP"
        : "a terminal, or an A2A 1.0 client: the same URL, the 1.0 names (the header is optional)",
    code: [
      `export A2A=${a2aEndpoint(context.origin, context.org)}`,
      `export TOKEN=${context.token ?? PLACEHOLDER}`,
      "# The agent card: the skills this token may start",
      `curl -s "$A2A/.well-known/agent-card.json" -H "Authorization: Bearer $TOKEN"`,
      "# Start the workflow; the same messageId sent again finds the same task",
      call(send!, { message }, 1, version),
      "# Read the task, or follow it as server-sent events",
      call(get!, task, 2, version),
      call(follow!, task, 3, version, true),
      "# Stop it",
      call(cancel!, task, 4, version),
    ].join("\n"),
  };
}

/** The three ways to call the door, with this instance's values. */
export function a2aSnippets(context: SnippetContext): Snippet[] {
  return [cliSnippet(context), curlSnippet(context, "0.3"), curlSnippet(context, "1.0")];
}

/** The commands that give an application its account and token, for one project (`org:slug`). */
export function machineCommands(project: string, application = "partner-app"): string {
  return [
    `choregos machines create ${project} ${application}`,
    `choregos machines token ${project} ${application} --name production --expires-in-days 90`,
  ].join("\n");
}

/** A token that acts as you, to try the door yourself. */
export function personalTokenCommand(project: string | null): string {
  return `choregos tokens create --name a2a-try --scope a2a${project ? ` --project ${project}` : ""} --expires-in-days 30`;
}

/** How an application's token is revoked from a terminal. */
export function revokeCommand(project: string, application: string): string {
  return `choregos machines revoke ${project} ${application} <token id>`;
}
