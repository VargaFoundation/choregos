// SPDX-License-Identifier: Apache-2.0
/**
 * What a project connector's state means, in words (S40-05).
 *
 * The API stores `unknown | ok | error | degraded`. "Unknown" is not a state a person can act on: it
 * only said that nobody had pressed *Test*. Saving a connector now tests it, so the word is rare
 * (rows saved before that), and it reads "Not tested yet". Shared by the connector card, the
 * getting-started list and the readiness panel, so the three say the same thing.
 */
import type { Tone } from "@/components/ui";
import type { ConnectorDto, ProjectRequirement } from "@/lib/types";

export interface ConnectorState {
  label: string;
  tone: Tone;
  /** A sentence under the label: when it was tested, why it fails, what simulates what. */
  detail: string;
  /** What the person can do about it. */
  action?: "test" | "test-again" | "connect";
  /** A stand-in (`demo`, `fake`): nothing real is behind it. */
  standIn: boolean;
}

/** The types that simulate a system instead of reaching it. */
export function isStandIn(type: string | undefined): boolean {
  return type === "demo" || type === "fake";
}

/** "3 min ago", "2 h ago", "4 d ago": a tested-at read at a glance. */
export function relativeTime(timestamp: string | null | undefined, now: number = Date.now()): string {
  if (!timestamp) return "";
  const seconds = Math.max(0, Math.round((now - new Date(timestamp).getTime()) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

const MAX_ERROR = 200;

export function connectorState({
  connector,
  requirement,
  required,
}: {
  connector?: ConnectorDto | null;
  requirement?: ProjectRequirement | null;
  required: boolean;
}): ConnectorState | null {
  if (connector) {
    const standIn = isStandIn(connector.type);
    const error = (connector.last_error ?? "").slice(0, MAX_ERROR);
    const note = standIn ? `simulates ${connector.kind}; nothing real is deployed` : "";
    switch (connector.status) {
      case "ok": {
        const when = relativeTime(connector.last_check_at);
        return {
          label: "Connected",
          tone: "ok",
          detail: [when ? `tested ${when}` : "", note].filter(Boolean).join(" · "),
          standIn,
        };
      }
      case "error":
        return { label: "Failing", tone: "danger", detail: error || "the last test failed", action: "test-again", standIn };
      case "degraded":
        return { label: "Degraded", tone: "warn", detail: error || "the last test was degraded", action: "test-again", standIn };
      default:
        return { label: "Not tested yet", tone: "warn", detail: note, action: "test", standIn };
    }
  }
  if (requirement?.default_type) {
    return {
      label: "Provided by the platform",
      tone: "neutral",
      detail: requirement.default_type,
      standIn: false,
    };
  }
  if (required) {
    return {
      label: "Not connected",
      tone: "danger",
      detail: requirement?.reasons[0] ?? "a workflow needs it",
      action: "connect",
      standIn: false,
    };
  }
  return null;
}
