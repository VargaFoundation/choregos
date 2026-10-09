import { describe, expect, it } from "vitest";
import { libelleDeDemande, resumeDeDemande, ticketsEnAttente } from "@/lib/a-decider";
import type { HumanRequest, WorkItemDto } from "@/lib/types";

const MAINTENANT = Date.parse("2026-10-08T12:00:00Z");
const heure = (h: number) => new Date(MAINTENANT + h * 3_600_000).toISOString();

const ticket = (id: string, demande?: Partial<HumanRequest>) =>
  ({
    id,
    title: id,
    pending_request: demande ? { id: `hr-${id}`, kind: "approval", payload: {}, requested_at: heure(-1), ...demande } : null,
  }) as unknown as WorkItemDto;

describe("ce qui attend une personne (S23-02)", () => {
  it("ne garde que les tickets arrêtés sur une demande que personne n'a encore décidée", () => {
    const tickets = [ticket("libre"), ticket("attend", {}), ticket("decide", { decided_at: heure(-0.5) })];
    expect(ticketsEnAttente(tickets, MAINTENANT).map((a) => a.item.id)).toEqual(["attend"]);
  });

  it("met le retard d'abord, puis l'échéance la plus proche, puis ce qui attend depuis le plus longtemps", () => {
    const tickets = [
      ticket("sans-echeance-recent", { requested_at: heure(-1) }),
      ticket("echeance-lointaine", { due_at: heure(48) }),
      ticket("sans-echeance-ancien", { requested_at: heure(-30) }),
      ticket("en-retard", { due_at: heure(-2) }),
      ticket("echeance-proche", { due_at: heure(3) }),
    ];
    const attentes = ticketsEnAttente(tickets, MAINTENANT);
    expect(attentes.map((a) => a.item.id)).toEqual([
      "en-retard",
      "echeance-proche",
      "echeance-lointaine",
      "sans-echeance-ancien",
      "sans-echeance-recent",
    ]);
    expect(attentes.filter((a) => a.enRetard).map((a) => a.item.id)).toEqual(["en-retard"]);
  });

  it("dit la demande par sa question ou son résumé, sinon par son genre, en mots", () => {
    const base = { id: "h", requested_at: heure(0) } as const;
    expect(resumeDeDemande({ ...base, kind: "question", payload: { question: "Which currency? " } })).toBe(
      "Which currency?",
    );
    expect(resumeDeDemande({ ...base, kind: "approval", payload: { summary: "Approve the spec" } })).toBe(
      "Approve the spec",
    );
    expect(resumeDeDemande({ ...base, kind: "scope_change", payload: {} })).toBe("scope change");
    expect(libelleDeDemande("une_sorte_nouvelle")).toBe("une sorte nouvelle");
  });
});
