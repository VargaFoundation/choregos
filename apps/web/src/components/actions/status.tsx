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

/** Who proposed: a person, an agent from the registry (`agent:<slug>`), or a job step of the
 * workflow (`job:<actor>`, ADR 0056), which has no registry agent (#1364). */
export function propose(par: Record<string, unknown> | undefined): string {
  const id = String(par?.id ?? "—");
  if (par?.kind === "agent") return id.replace(/^agent:/, "the agent ");
  if (par?.kind === "job") return id.replace(/^job:/, "the job step ");
  return id;
}
