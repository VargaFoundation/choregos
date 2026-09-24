import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Acces } from "@/components/acces";
import { Garanties } from "@/components/garanties";
import { LiveLog } from "@/components/live-log";
import { Preuves } from "@/components/preuves";
import { ActorIcon, CostChip, StateBadge } from "@/components/ui";
import type { RunEventDto } from "@/lib/types";

describe("composants transverses", () => {
  it("affiche le libellé du projet, pas l'identifiant d'état", () => {
    render(<StateBadge state="awaiting_spec_approval" display="Spec à valider" kind="wait" />);
    expect(screen.getByText("Spec à valider")).toBeInTheDocument();
  });

  it("marque le dépassement de budget", () => {
    const { container } = render(<CostChip costEur={30} budgetEur={25} />);
    expect(container.querySelector(".text-danger")).not.toBeNull();
  });

  it("nomme le type d'acteur pour les lecteurs d'écran", () => {
    render(<ActorIcon kind="agent" name="implement" />);
    expect(screen.getByText("agent")).toBeInTheDocument();
  });
});

describe("journal ACP", () => {
  const events: RunEventDto[] = Array.from({ length: 5000 }, (_, index) => ({
    seq: index + 1,
    type: index % 500 === 0 ? "session/request_permission" : "session/update",
    ts: new Date().toISOString(),
    payload: index % 500 === 0 ? { allowed: false, target: "src/hors-perimetre.py", reason: "hors périmètre" } : { text: `ligne ${index}` },
  }));

  it("virtualise : seules quelques lignes sont montées", () => {
    const { container } = render(<LiveLog events={events} height={300} />);
    const rows = container.querySelectorAll('[data-testid="live-log"] > div > div');
    expect(rows.length).toBeLessThan(60);
    expect(screen.getByText(/5000 événements/)).toBeInTheDocument();
  });

  it("met en évidence les permissions refusées", () => {
    const { container } = render(<LiveLog events={events.slice(0, 3)} />);
    expect(container.querySelector(".text-danger")).not.toBeNull();
  });
});

describe("preuves d'une étape", () => {
  it("affiche les faits nommés par le métier plutôt que des tests absents", () => {
    // Un dossier instruit n'a ni tests ni couverture : afficher « tests : ✗ · lint : — »
    // pour lui était faux, et la faute se voyait à l'écran avant de se voir au contrat.
    render(<Preuves evidence={{ facts: { profils_retenus: 2, besoin_complet: true } }} />);
    expect(screen.getByText(/profils retenus/)).toBeInTheDocument();
    expect(screen.getByText(/✓/)).toBeInTheDocument();
    expect(screen.queryByText(/tests/)).toBeNull();
  });

  it("garde les mesures du logiciel quand ce sont elles qui existent", () => {
    render(<Preuves evidence={{ tests_run: 6, tests_passed: true, lint: "ok" }} />);
    expect(screen.getByText(/tests : ✓ 6/)).toBeInTheDocument();
    expect(screen.getByText(/lint : ok/)).toBeInTheDocument();
  });

  it("ne montre pas une grille de tirets quand il n'y a aucune preuve", () => {
    render(<Preuves evidence={{}} />);
    expect(screen.getByText(/aucune preuve consignée/)).toBeInTheDocument();
  });
});

describe("fiche d'accès", () => {
  it("met les refus devant, avec leur motif", () => {
    // C'est ce qu'on vient chercher dans un audit : « denied » ne se lit pas,
    // « fichier sensible » se lit.
    render(
      <Acces
        acces={{
          evenements: 220,
          refus: 1,
          acces: [
            { nature: "write", cible: "/workspace/.env", demandes: 1, refus: 1, motifs: ["fichier sensible"] },
            { nature: "read", cible: "/workspace/src/panier.py", demandes: 4, refus: 0 },
          ],
        }}
      />,
    );
    expect(screen.getAllByText(/refus/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/fichier sensible/)).toBeInTheDocument();
    expect(screen.getByText(/220 événements/)).toBeInTheDocument();
  });

  it("ne prétend rien quand il n'y a rien", () => {
    render(<Acces />);
    expect(screen.getByText(/aucun accès enregistré/)).toBeInTheDocument();
  });
});

describe("garanties", () => {
  const events: RunEventDto[] = [
    { seq: 1, type: "session/update", ts: "2026-09-24T10:00:00Z", payload: { text: "prose" } },
    {
      seq: 2,
      type: "gate.outcome",
      ts: "2026-09-24T10:01:00Z",
      payload: { name: "scope_respected", passed: true, pending: false, detail: "dans le périmètre" },
    },
    {
      seq: 3,
      type: "gate.outcome",
      ts: "2026-09-24T10:01:00Z",
      payload: { name: "evidence_present", passed: false, pending: false, detail: "aucun test exécuté" },
    },
  ];

  it("montre chaque garantie évaluée et son verdict, les refus d'abord", () => {
    render(<Garanties events={events} />);
    expect(screen.getByText("2 évaluée(s) · 1 refus")).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items[0]?.textContent).toContain("evidence_present");
    expect(items[0]?.textContent).toContain("aucun test exécuté");
    expect(screen.getByLabelText("refusée")).toBeInTheDocument();
  });

  it("dit quand aucune garantie n'a été évaluée, plutôt qu'un ✓ à vide", () => {
    render(<Garanties events={[events[0]!]} />);
    expect(screen.getByText(/aucune garantie évaluée/)).toBeInTheDocument();
  });
});
