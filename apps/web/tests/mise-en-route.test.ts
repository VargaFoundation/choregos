import { describe, expect, it } from "vitest";
import { etapesDeMiseEnRoute } from "@/components/mise-en-route";
import type { ConnectorDto, ProjectRequirement, WorkflowSummary, WorkItemDto } from "@/lib/types";

const connecteur = (kind: string, status: string, extra: Partial<ConnectorDto> = {}) =>
  ({ kind, type: `${kind}-type`, config: {}, status, ...extra }) as ConnectorDto;
const exigence = (capability: string, extra: Partial<ProjectRequirement> = {}): ProjectRequirement => ({
  capability,
  reasons: [`a workflow needs ${capability}`],
  connector: null,
  default_type: null,
  ...extra,
});
const workflow = { name: "dev-simple", version: 1, is_default: true, open_items: 0 } as WorkflowSummary;
const ticket = { id: "w1" } as WorkItemDto;

function etats(etapes: ReturnType<typeof etapesDeMiseEnRoute>) {
  return Object.fromEntries(etapes.map((e) => [e.id, e.etat]));
}

describe("la mise en route d'un projet (S23-03)", () => {
  it("un projet qui tourne n'a plus rien à faire : la liste ne s'affiche pas", () => {
    const etapes = etapesDeMiseEnRoute({
      slug: "billing",
      projet: { status: "active" },
      exigences: [exigence("tracker", { connector: connecteur("tracker", "ok") }), exigence("gateway", { default_type: "litellm" })],
      connecteurs: [],
      workflows: [workflow],
      tickets: [ticket],
    });
    expect(etapes.every((e) => e.etat === "fait")).toBe(true);
    // La capacité couverte par la plateforme le dit.
    expect(etapes.find((e) => e.id === "connecteur-gateway")?.detail).toBe("platform default: litellm.");
  });

  it("un projet neuf dit ce qui manque, pourquoi, et où le régler", () => {
    const etapes = etapesDeMiseEnRoute({
      slug: "neuf",
      projet: { status: "provisioning" },
      exigences: [
        exigence("scm"),
        exigence("ci", { connector: connecteur("ci", "unknown") }),
        exigence("cd", { connector: connecteur("cd", "error", { last_error: "401 from Argo CD" }) }),
      ],
      connecteurs: [],
      workflows: [],
      tickets: [],
    });
    expect(etats(etapes)).toEqual({
      provisioning: "a_faire",
      workflow: "a_faire",
      "connecteur-scm": "a_faire",
      "connecteur-ci": "a_verifier",
      "connecteur-cd": "a_faire",
      "premier-ticket": "a_faire",
    });
    const scm = etapes.find((e) => e.id === "connecteur-scm");
    expect(scm?.titre).toBe("source code connected");
    expect(scm?.detail).toBe("not connected — a workflow needs scm.");
    expect(scm?.lien?.href).toBe("/p/neuf/settings");
    expect(etapes.find((e) => e.id === "connecteur-cd")?.detail).toBe("cd-type: its last test failed (401 from Argo CD).");
    expect(etapes.find((e) => e.id === "workflow")?.lien?.href).toBe("/p/neuf/workflows/new");
  });

  it("un connecteur qu'aucun workflow n'exige se dit quand il échoue, et seulement alors", () => {
    const etapes = etapesDeMiseEnRoute({
      slug: "x",
      projet: { status: "active" },
      exigences: [],
      connecteurs: [connecteur("notify", "ok"), connecteur("cd", "degraded")],
      workflows: [workflow],
      tickets: [ticket],
    });
    expect(etapes.map((e) => e.id)).toEqual(["provisioning", "workflow", "connecteur-cd", "premier-ticket"]);
    expect(etapes.find((e) => e.id === "connecteur-cd")?.detail).toBe("cd-type: its last test was degraded.");
  });
});
