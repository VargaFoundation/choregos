import { describe, expect, it } from "vitest";
import { aLInstant, debutDePhase, gare, instants, modeler, raconter } from "@/components/parcours/modele";
import { parcoursEnAttente, parcoursEnCours, parcoursTermine } from "@/mocks/parcours";

// Une horloge fixe : deux lectures du même parcours donnent la même carte.
const MAINTENANT = Date.parse("2026-10-08T12:00:00Z");
const enCours = parcoursEnCours(MAINTENANT);
const termine = parcoursTermine(MAINTENANT);
const modele = modeler(enCours);

describe("le modèle d'un parcours (S22-02)", () => {
  it("le chemin de dev-complex : quinze étapes, chacune portée par qui la fait avancer", () => {
    expect(modele.etapes.map((e) => e.id)).toEqual([
      "t-triage",
      "t-specify",
      "t-approve-spec",
      "t-plan",
      "t-implement",
      "t-test",
      "t-review",
      "t-security-review",
      "t-release-notes",
      "t-open-pr",
      "t-approve-pr",
      "t-merge",
      "t-stage",
      "t-release",
      "t-verify-prod",
    ]);
    const par = (id: string) => modele.parId.get(id)!;
    expect([par("t-implement").genre, par("t-implement").libelle, par("t-implement").action]).toEqual([
      "agent",
      "Developer",
      "writes the change",
    ]);
    expect([par("t-approve-spec").genre, par("t-approve-spec").libelle]).toEqual(["human", "Product owners"]);
    expect([par("t-open-pr").genre, par("t-open-pr").action]).toEqual(["platform", "opens the pull request"]);
    expect([par("t-merge").action, par("t-merge").gates]).toEqual(["merges the pull request", ["ci_green"]]);
    expect([par("t-release").genre, par("t-release").action]).toEqual(["train", "deploys to prod"]);
    expect(par("t-implement").maxTours).toBe(3);
    expect(par("t-test").phrase).toMatch(/tester/);
  });

  it("les détours d'où l'on revient, et les arcs qui y mènent", () => {
    expect(modele.detours.map((d) => [d.etat, d.etape.id, d.sources, d.retour])).toEqual([
      ["addressing_review", "t-address-review", ["t-review", "t-security-review", "t-approve-pr"], "implemented"],
      ["fixing_ci", "t-fix-ci", ["t-merge"], "awaiting_pr_approval"],
    ]);
    expect(modele.arcs.map((a) => `${a.genre} ${a.depuis}: ${a.de} → ${a.vers}`)).toEqual([
      "renvoi t-approve-spec: awaiting_spec_approval → triaged",
      "renvoi t-test: implemented → planned",
      "detour t-review: tested → addressing_review",
      "detour t-security-review: reviewed → addressing_review",
      "detour t-approve-pr: awaiting_pr_approval → addressing_review",
      "detour t-merge: pr_approved → fixing_ci",
      "retour t-address-review: addressing_review → implemented",
      "retour t-fix-ci: fixing_ci → awaiting_pr_approval",
    ]);
    expect(modele.exceptions.map((e) => e.id)).toEqual(["needs_human", "abandoned"]);
  });

  it("les phases suivent les colonnes du tracker", () => {
    expect(modele.phases.map((p) => `${p.libelle} (${p.etapes.length})`)).toEqual([
      "Triaged (1)",
      "Needs review (1)",
      "Ready (2)",
      "In progress (5)",
      "In review (2)",
      "Merged (1)",
      "Staging (1)",
      "Deployed (1)",
      "Done (1)",
    ]);
  });

  it("en direct : la revue de sécurité tourne, les tours se comptent, les renvois aussi", () => {
    const maintenant = aLInstant(enCours, modele, Infinity);
    const statut = (id: string) => maintenant.etapes.get(id)!.statut;
    expect(maintenant.etat).toBe("reviewed");
    expect(statut("t-security-review")).toBe("en_cours");
    expect(["t-triage", "t-specify", "t-approve-spec", "t-plan", "t-implement", "t-test", "t-review"].map(statut)).toEqual(
      Array(7).fill("fait"),
    );
    expect(["t-release-notes", "t-open-pr", "t-merge", "t-release"].map(statut)).toEqual(Array(4).fill("a_venir"));
    expect(maintenant.etapes.get("t-test")!.tours).toBe(3);
    expect(maintenant.etapes.get("t-review")!.verdict).toBe("approve");
    expect(maintenant.etapes.get("t-approve-spec")!.tours).toBe(2);
    expect(maintenant.arcs.get("implemented->planned")).toEqual({ fois: 1, dernier: false });
    expect(maintenant.arcs.get("tested->addressing_review")!.fois).toBe(1);
    expect(maintenant.arcs.get("addressing_review->implemented")!.fois).toBe(1);
    expect(maintenant.arcs.get("pr_approved->fixing_ci")!.fois).toBe(0);
    expect(modele.phases[maintenant.phase!]!.libelle).toBe("In progress");
  });

  it("en relecture : à chaque instant, la case qui bouge est celle que l'histoire dit", () => {
    const images = instants(enCours);
    expect(images).toHaveLength(30);
    expect(images).toEqual([...images].sort((a, b) => a - b));
    // Juste après le refus de la spécification : la personne a renvoyé, le rédacteur repart.
    const refus = Date.parse(enCours.moves.find((m) => m.kind === "reject")!.at);
    const apresLeRefus = aLInstant(enCours, modele, refus);
    expect(apresLeRefus.etat).toBe("triaged");
    expect(apresLeRefus.etapes.get("t-approve-spec")!.statut).toBe("renvoye");
    expect(apresLeRefus.etapes.get("t-specify")!.statut).toBe("en_cours");
    expect(apresLeRefus.arcs.get("awaiting_spec_approval->triaged")).toEqual({ fois: 1, dernier: true });
    expect(raconter(modele, apresLeRefus.dernier)).toBe("Product owners sent it back to Triaged.");
    // Pendant la demande de changements : les tests repasseront après le détour.
    const demande = Date.parse(enCours.moves.find((m) => m.kind === "changes_requested")!.at);
    const pendantLeDetour = aLInstant(enCours, modele, demande);
    expect(pendantLeDetour.etapes.get("t-review")!.statut).toBe("renvoye");
    expect(pendantLeDetour.etapes.get("t-address-review")!.statut).toBe("en_cours");
    expect(raconter(modele, pendantLeDetour.dernier)).toBe("Reviewer asked for changes: back to Addressing the review.");
    const retour = aLInstant(enCours, modele, demande + 10 * 60_000);
    expect(retour.etat).toBe("implemented");
    expect(retour.etapes.get("t-test")!.statut).toBe("en_cours");
  });

  it("une personne qu'on attend se voit, un ticket fini n'a plus rien de prochain", () => {
    const attente = aLInstant(parcoursEnAttente(MAINTENANT), modele, Infinity);
    expect(attente.etapes.get("t-approve-spec")!.statut).toBe("attend");
    const fin = aLInstant(termine, modeler(termine), Infinity);
    expect([...fin.etapes.values()].filter((e) => e.statut !== "fait").length).toBe(0);
    expect(fin.arcs.get("pr_approved->fixing_ci")!.fois).toBe(1);
    expect(fin.etapes.get("t-approve-pr")!.tours).toBe(2);
    expect(fin.etapes.get("t-release")!.tentatives[0]!.decided_by).toBe("leo@varga.dev");
  });

  it("sauter à une phase : le premier instant où l'une de ses étapes a bougé", () => {
    const enRevue = modele.phases.find((p) => p.libelle === "In progress")!;
    const t = debutDePhase(enCours, modele, enRevue)!;
    expect(aLInstant(enCours, modele, t).etat).toBe("planned");
    expect(
      debutDePhase(
        enCours,
        modele,
        modele.phases.find((p) => p.libelle === "Deployed")!,
      ),
    ).toBeNull();
  });
});

describe("un ticket garé se reprend (#313)", () => {
  const dernier = (kind: string) => ({
    ...enCours,
    moves: [...enCours.moves, { at: "2026-10-08T11:59:00Z", from: "triaged", to: "needs_human", kind, transition_id: "t-implement", reason: null }],
  });

  it("escaladé, ou arrêté par une question : « Replay the stage » est offert", () => {
    expect(gare(dernier("escalate") as typeof enCours)).toBe(true);
    expect(gare(dernier("default") as typeof enCours)).toBe(true);
  });

  it("sur sa route, ou clos : rien à reprendre", () => {
    expect(gare(dernier("nominal") as typeof enCours)).toBe(false);
    expect(gare({ ...dernier("escalate"), closed: true } as typeof enCours)).toBe(false);
    expect(gare(undefined)).toBe(false);
  });
});
