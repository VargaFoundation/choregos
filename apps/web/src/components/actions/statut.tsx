// SPDX-License-Identifier: Apache-2.0
import { Badge } from "@varga/design-system";

const TON: Record<string, "neutral" | "accent" | "ok" | "warn" | "danger"> = {
  pending_approval: "warn",
  approved: "accent",
  running: "accent",
  succeeded: "ok",
  rejected: "neutral",
  failed: "danger",
  done: "ok",
  started: "accent",
  compensated: "warn",
  compensation_failed: "danger",
};

/** Le statut d'une action ou d'un effet, dit avec son ton. */
export function Statut({ statut }: { statut: string }) {
  return <Badge tone={TON[statut] ?? "neutral"}>{statut.replaceAll("_", " ")}</Badge>;
}

/** Qui a proposé : une personne, ou un agent du registre (`agent:<slug>`). */
export function propose(par: Record<string, unknown> | undefined): string {
  const id = String(par?.id ?? "—");
  return par?.kind === "agent" ? `${id.replace(/^agent:/, "the agent ")}` : id;
}
