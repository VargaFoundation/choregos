import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  areteDeLaCarte,
  cheminNominal,
  COULEUR_DU_GENRE,
  defaults,
  disposerLaCarte,
  genreDActeur,
  libelleSecondaire,
  navigation,
} from "@/components/workflows/carte/disposition";
import { releaseFullValidation } from "@/mocks/data";

const n = (id: string, lane = "agent", terminal = false) => ({ id, display: id.toUpperCase(), lane, terminal });
const nom = (id: string, from: string, to: string, extra: object = {}) => ({ id, from, to, kind: "nominal", ...extra });
const sec = (from: string, to: string, kind: string, label: string) => ({ id: `${from}->${to}:${kind}:${label}`, from, to, kind, label });

/** default-simple, réduit : un chemin, une escalade, un rejet, un abandon, des défauts. */
const simple = {
  nodes: [n("inbox"), n("spec", "human"), n("dev"), n("pr", "system"), n("prod", "terminal", true), n("needs_human", "human"), n("refining"), n("abandoned", "terminal", true)],
  edges: [
    nom("t-refine", "inbox", "spec", { actor: "refiner" }),
    nom("t-approve", "spec", "dev", { actor: "owner" }),
    sec("spec", "refining", "reject", "rejet"),
    nom("t-again", "refining", "spec", { actor: "refiner" }),
    nom("t-dev", "dev", "pr", { actor: "dev", gates: ["scope_respected"] }),
    sec("dev", "dev", "retry", "échec (≤2)"),
    sec("dev", "needs_human", "escalate", "échecs épuisés"),
    nom("t-pr", "pr", "prod", { actor: "ci" }),
    sec("inbox", "needs_human", "default", "question"),
    sec("dev", "needs_human", "default", "question"),
    sec("needs_human", "abandoned", "default", "abandon"),
  ],
};

describe("la carte à plat : la disposition (S21-07)", () => {
  it("le chemin nominal part de l'état initial déclaré et suit la plus longue suite de transitions nominales", () => {
    expect(cheminNominal(simple, "inbox")).toEqual(["inbox", "spec", "dev", "pr", "prod"]);
    // Un raccourci (spec → pr) ne vole pas le chemin : la plus longue suite l'emporte.
    const raccourci = { ...simple, edges: [...simple.edges, nom("t-skip", "spec", "pr")] };
    expect(cheminNominal(raccourci, "inbox")).toEqual(["inbox", "spec", "dev", "pr", "prod"]);
  });

  it("sans état initial déclaré, il part de l'état que rien n'atteint", () => {
    const melange = { ...simple, nodes: [simple.nodes[3]!, ...simple.nodes.filter((x) => x.id !== "pr")] };
    expect(cheminNominal(melange)[0]).toBe("inbox");
  });

  it("un état atteint seulement par un rejet, une reprise, une escalade ou un défaut va dans la colonne d'à côté", () => {
    const { chemin, cotes } = disposerLaCarte(simple, "inbox");
    expect(chemin).not.toContain("needs_human");
    expect(cotes.map((c) => c.id)).toEqual(["refining", "needs_human", "abandoned"]);
    expect(cotes.find((c) => c.id === "refining")!.depuis).toEqual([{ id: "spec", comment: "if rejected" }]);
  });

  it("un état de côté se range à hauteur de l'état d'où l'on y tombe ; l'abandon après l'intervention humaine", () => {
    const ordre = disposerLaCarte(simple, "inbox").cotes.map((c) => c.id);
    expect(ordre.indexOf("refining")).toBeLessThan(ordre.indexOf("needs_human"));
    expect(ordre.indexOf("needs_human")).toBeLessThan(ordre.indexOf("abandoned"));
  });

  it("une transition depuis n'importe quel état d'agent ne fait pas le chemin et s'édite sous son propre identifiant", () => {
    const avecJoker = {
      ...simple,
      edges: [
        ...simple.edges,
        { ...nom("t-ask:inbox", "inbox", "needs_human"), wildcard: true, actor: "ci" },
        { ...nom("t-ask:dev", "dev", "needs_human"), wildcard: true, actor: "ci" },
      ],
    };
    const disposition = disposerLaCarte(avecJoker, "inbox");
    expect(disposition.chemin).toEqual(["inbox", "spec", "dev", "pr", "prod"]);
    expect(disposition.jokers).toEqual([{ cle: "t-ask", to: "needs_human", actor: "ci" }]);
    // Choisir la transition joker ouvre `t-ask`, pas `t-ask:inbox` qu'aucun YAML ne connaît.
    expect(areteDeLaCarte(avecJoker, "t-ask")?.id).toBe("t-ask");
  });

  it("ne boucle pas sur un workflow cyclique, ne perd aucun état, même isolé, et reste déterministe", () => {
    const cyclique = {
      nodes: [n("a"), n("b"), n("c"), n("isole")],
      edges: [nom("ab", "a", "b"), nom("bc", "b", "c"), nom("ca", "c", "a")],
    };
    const premiere = disposerLaCarte(cyclique);
    expect(premiere.chemin).toEqual(["a", "b", "c"]);
    expect(new Set(premiere.ordre)).toEqual(new Set(["a", "b", "c", "isole"]));
    expect(disposerLaCarte(cyclique)).toEqual(premiere);
  });

  it("dix-huit états : treize sur le chemin, cinq à côté", () => {
    const { chemin, cotes } = disposerLaCarte(releaseFullValidation.graph!, "inbox");
    expect(chemin).toHaveLength(13);
    expect(chemin.at(-1)).toBe("verified_prod");
    expect(cotes.map((c) => c.id).sort()).toEqual(["abandoned", "addressing_review", "fixing_ci", "needs_human", "refining"]);
  });

  it("chaque genre d'acteur se peint avec des jetons du design system, et se dit en toutes lettres", () => {
    for (const { bord, fond, texte } of Object.values(COULEUR_DU_GENRE)) {
      expect(texte).toMatch(/^var\(--varga-[a-z-]+\)$/);
      expect(bord).toMatch(/^var\(--varga-[a-z-]+\)$/);
      expect(fond).toMatch(/^var\(--varga-[a-z-]+\)$/);
    }
    expect(["agent", "human", "system", "train", "wait", "terminal"].map(genreDActeur)).toEqual([
      "agent",
      "human",
      "platform",
      "train",
      "waiting",
      "end",
    ]);
    // La carte ne peint qu'avec ces jetons : aucune couleur écrite en dur dans ses fichiers.
    for (const fichier of ["carte-du-workflow.tsx", "disposition.ts"]) {
      const source = readFileSync(join(import.meta.dirname, "../src/components/workflows/carte", fichier), "utf8");
      expect(source, fichier).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(/);
    }
  });

  it("les libellés secondaires du serveur se disent en anglais, qu'ils arrivent en français ou déjà traduits", () => {
    expect(libelleSecondaire({ from: "a", to: "b", kind: "retry", label: "échec (≤2)" })).toBe("on failure (up to 2 retries)");
    expect(libelleSecondaire({ from: "a", to: "b", kind: "escalate", label: "échecs épuisés" })).toBe("when retries run out");
    expect(libelleSecondaire({ from: "a", to: "b", kind: "default", label: "budget dépassé" })).toBe("budget exceeded");
    expect(libelleSecondaire({ from: "a", to: "b", kind: "default", label: "question" })).toBe("question");
    expect(defaults(simple)).toEqual([
      { label: "question", to: "NEEDS_HUMAN", from: ["INBOX", "DEV"] },
      { label: "abandon", to: "ABANDONED", from: ["NEEDS_HUMAN"] },
    ]);
  });
});

describe("la carte à plat : le clavier (S21-07)", () => {
  const nav = navigation(simple, "inbox");

  it("↓ ↑ suivent l'ordre de lecture : le chemin, puis la colonne d'à côté", () => {
    expect(nav.ordre).toEqual(["inbox", "spec", "dev", "pr", "prod", "refining", "needs_human", "abandoned"]);
    expect(nav.deplacer("prod", "ArrowDown")).toBe("refining");
    expect(nav.deplacer("inbox", "ArrowUp")).toBe("inbox");
    expect(nav.deplacer("dev", "End")).toBe("abandoned");
    expect(nav.deplacer("dev", "Home")).toBe("inbox");
  });

  it("→ suit la transition nominale, ← la remonte ; au bout, rien", () => {
    expect(nav.deplacer("spec", "ArrowRight")).toBe("dev");
    expect(nav.deplacer("spec", "ArrowLeft")).toBe("inbox");
    expect(nav.deplacer("prod", "ArrowRight")).toBeNull();
    expect(nav.deplacer("refining", "ArrowRight")).toBe("spec");
  });

  it("décrit l'état sous le curseur : qui agit, vers où, et ses issues", () => {
    expect(nav.decrire("dev")).toBe(
      "DEV (agent): → PR (by dev, gates scope_respected); → DEV on failure (up to 2 retries); → NEEDS_HUMAN when retries run out.",
    );
    expect(nav.decrire("prod")).toBe("PROD (end, terminal): no outgoing transition.");
  });
});
