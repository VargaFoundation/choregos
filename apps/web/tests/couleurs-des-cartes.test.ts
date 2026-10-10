// @vitest-environment node
/**
 * Les couleurs des cartes (S24-04, ADR 0043). Ce qui dessine — la barre d'une case, le contour d'une
 * forme, un renvoi pris — doit se voir (3:1, WCAG 1.4.11), ce qui écrit doit se lire (4,5:1), sur la
 * page comme sur une carte, dans les deux thèmes. Axe ne mesure ni un trait SVG ni une bordure.
 */
import { describe, expect, it } from "vitest";
import { COULEUR_DU_RENVOI, COULEUR_DU_STATUT } from "@/components/parcours/couleurs";
import { COULEUR_DU_GENRE } from "@/components/workflows/carte/disposition";
import { contraste, jeton, type Mode } from "./jetons";

const FONDS = ["--varga-surface", "--choregos-raised"];

function nomDe(valeur: string): string {
  const nom = /^var\((--[a-z0-9-]+)\)$/.exec(valeur)?.[1];
  if (!nom) throw new Error(`pas un jeton : ${valeur}`);
  return nom;
}

describe.each(["dark", "light"] as Mode[])("les couleurs des cartes, en %s", (mode) => {
  const rapport = (couleur: string, fond: string) => contraste(jeton(nomDe(couleur), mode), jeton(fond, mode));

  it("chaque genre d'acteur : un bord qui se voit (3:1), un texte qui se lit (4,5:1)", () => {
    for (const [genre, { bord, texte }] of Object.entries(COULEUR_DU_GENRE)) {
      for (const fond of FONDS) {
        expect(rapport(bord, fond), `${genre}, bord sur ${fond}`).toBeGreaterThanOrEqual(3);
        expect(rapport(texte, fond), `${genre}, texte sur ${fond}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("chaque statut du parcours, et un renvoi pris, se voient (3:1)", () => {
    for (const [statut, trait] of Object.entries({ ...COULEUR_DU_STATUT, renvoi: COULEUR_DU_RENVOI })) {
      for (const fond of FONDS) {
        expect(rapport(trait, fond), `${statut} sur ${fond}`).toBeGreaterThanOrEqual(3);
      }
    }
  });
});

it("chaque genre et chaque statut vivant a sa couleur : « in progress » et « done » ne se confondent plus (S23-12)", () => {
  const genres = Object.values(COULEUR_DU_GENRE).map((c) => c.bord);
  expect(new Set(genres.slice(0, 5)).size).toBe(5);
  const { en_cours, attend, fait, renvoye, echoue } = COULEUR_DU_STATUT;
  expect(new Set([en_cours, attend, fait, renvoye, echoue]).size).toBe(5);
});

it("chaque statut du parcours prend le ton de l'état qu'il est, et chaque genre le sien (ADR 0043)", () => {
  // « waiting for a person » était l'encre — un anneau blanc pur en sombre — et « in progress » le
  // turquoise de l'accent, celui des liens et du focus.
  expect(COULEUR_DU_STATUT).toEqual({
    a_venir: "var(--choregos-neutral-solid)",
    en_cours: "var(--choregos-running-ink)",
    attend: "var(--choregos-waiting-ink)",
    fait: "var(--choregos-succeeded-ink)",
    renvoye: "var(--choregos-retrying-ink)",
    echoue: "var(--choregos-failed-ink)",
  });
  expect(COULEUR_DU_RENVOI).toBe("var(--choregos-retrying-ink)");
  expect(COULEUR_DU_GENRE.agent.bord).toBe("var(--choregos-agent-ink)");
  expect(COULEUR_DU_GENRE.human.bord).toBe("var(--choregos-running-ink)");
});
