// SPDX-License-Identifier: Apache-2.0
import { Badge } from "@/components/ui";

const TON: Record<string, "neutral" | "accent" | "ok" | "warn" | "danger"> = {
  pending_approval: "warn",
  approved: "accent",
  running: "accent",
  awaiting_evidence: "warn",
  succeeded: "ok",
  rejected: "neutral",
  failed: "danger",
  done: "ok",
  started: "accent",
  compensated: "warn",
  compensation_failed: "danger",
};

/** The status of an action or an effect, said with its tone. */
export function Status({ statut }: { statut: string }) {
  return <Badge tone={TON[statut] ?? "neutral"}>{statut.replaceAll("_", " ")}</Badge>;
}

/** Who proposed: a person, or an agent from the registry (`agent:<slug>`). */
export function propose(par: Record<string, unknown> | undefined): string {
  const id = String(par?.id ?? "—");
  return par?.kind === "agent" ? `${id.replace(/^agent:/, "the agent ")}` : id;
}
