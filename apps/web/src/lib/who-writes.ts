// SPDX-License-Identifier: Apache-2.0
/**
 * Who writes, and with which account (S42-06g, ADR 0107 story 1), in words.
 *
 * The platform writes a work item's bookkeeping (its state, status comment, fields, questions) to the
 * tracker through the project's tracker connector, under that connector's account; an agent's own
 * actions go through the MCP tools of its version, under each connector's account. Never a secret:
 * an email or a username from the configuration, else the kind of credential.
 */
import type { ToolConnector, TrackerWrite } from "@/lib/types";

const TRACKER_NAMES: Record<string, string> = {
  jira: "Jira",
  github: "GitHub",
  "github-issues": "GitHub Issues",
  "gitlab-issues": "GitLab Issues",
  plugin: "a plugin tracker",
  internal: "the internal tracker",
};

export function trackerName(type: string): string {
  return TRACKER_NAMES[type] ?? type;
}

function text(config: Record<string, unknown> | undefined, key: string): string | null {
  const value = config?.[key];
  return typeof value === "string" && value ? value : null;
}

/** The account a tracker connector writes under; `null` for the internal tracker, which writes nowhere. */
export function trackerAccount(type: string, config?: Record<string, unknown>): string | null {
  switch (type) {
    case "internal":
      return null;
    case "jira":
      return text(config, "email") ?? "the account of the connector's API token";
    case "github":
    case "github-issues":
      return "the Choregos GitHub App";
    case "gitlab-issues":
      return "the account that owns the connector's GitLab token";
    case "plugin": {
      const plugin = text(config, "plugin");
      const server = text(config, "server");
      return plugin && server ? `the plugin server ${plugin}/${server}` : "the plugin's server";
    }
    default:
      return "the connector's credential";
  }
}

/** A timeline entry's tracker write: `via Jira as bot@acme.example`. */
export function viaInWords(via: TrackerWrite): string {
  return `via ${trackerName(via.type)} as ${via.account ?? trackerAccount(via.type) ?? "the platform"}`;
}

/** The account a run's tool connector calls as. */
export function toolAccount(connector: ToolConnector): string {
  if (connector.account) return connector.account;
  return connector.auth_scheme === "bearer" ? "the connector's key" : "the connector's credential";
}

/** Who started a work item or a run, `<kind>:<subject>` (ADR 0077), in words. */
export function initiatorInWords(initiatedBy: string | null | undefined): string | null {
  if (!initiatedBy) return null;
  const [kind, ...rest] = initiatedBy.split(":");
  const subject = rest.join(":");
  switch (kind) {
    case "person":
      return subject;
    case "mcp":
      return `${subject}, through MCP`;
    case "a2a":
      return `the A2A client ${subject}`;
    case "schedule":
      return `the schedule ${subject}`;
    case "tracker":
      return `a ${trackerName(subject)} webhook`;
    case "work_item":
      return `the work item ${subject}`;
    case "finding":
      return `the finding ${subject}`;
    case "trial":
      return `a trial by ${subject}`;
    default:
      return initiatedBy;
  }
}
