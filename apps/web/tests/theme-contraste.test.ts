// @vitest-environment node
/**
 * Le budget de contraste de la console (ADR 0043), mode par mode.
 *
 * Le design system donnait au texte 19,8:1 en clair et 19:1 en sombre, et aux filets 1,26:1 : tout
 * le contraste était dans le texte, aucun dans la structure. La console vise l'inverse — un texte
 * qui se lit sans éblouir (7 à 17:1), des bordures de contrôle à 3:1 au moins (WCAG 1.4.11), des
 * filets qui se voient. Axe ne mesure pas le contraste des bordures ni du focus : ce test, si.
 */
import { describe, expect, it } from "vitest";
import { contraste, jeton, luminance, type Mode } from "./jetons";

const MODES: Mode[] = ["dark", "light"];
const FONDS = ["--varga-surface", "--choregos-raised", "--choregos-sidebar", "--varga-surface-muted"];
const STATUTS = ["running", "waiting", "succeeded", "failed", "retrying", "neutral", "agent"];

function rapport(premier: string, second: string, mode: Mode): number {
  return contraste(jeton(premier, mode), jeton(second, mode));
}

describe.each(MODES)("le contraste de la console, en %s", (mode) => {
  it("le texte se lit sans éblouir : 7:1 au moins, 17:1 au plus, sur chaque fond", () => {
    for (const fond of FONDS) {
      const r = rapport("--varga-ink", fond, mode);
      expect(r, `encre sur ${fond}`).toBeGreaterThanOrEqual(7);
      expect(r, `encre sur ${fond} : trop dur`).toBeLessThanOrEqual(17);
    }
  });

  it("le texte secondaire et les indications passent AA (4,5:1) sur chaque fond", () => {
    for (const texte of ["--varga-ink-muted", "--varga-ink-placeholder", "--varga-accent-strong"]) {
      for (const fond of FONDS) {
        expect(rapport(texte, fond, mode), `${texte} sur ${fond}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("le texte sur une action pleine passe AA, au repos comme au survol", () => {
    for (const [texte, fond] of [
      ["--varga-inverse-ink", "--varga-inverse"],
      ["--varga-inverse-ink", "--varga-inverse-hover"],
      ["--varga-accent-ink", "--varga-accent"],
      ["--varga-accent-ink", "--varga-accent-hover"],
    ] as const) {
      expect(rapport(texte, fond, mode), `${texte} sur ${fond}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("chaque état se lit sur la page, une carte, un survol et sa propre pastille", () => {
    for (const texte of ["--varga-ok", "--varga-warn", "--varga-danger"]) {
      for (const fond of FONDS) {
        expect(rapport(texte, fond, mode), `${texte} sur ${fond}`).toBeGreaterThanOrEqual(4.5);
      }
    }
    for (const statut of STATUTS) {
      const encre = `--choregos-${statut}-ink`;
      for (const fond of [...FONDS, `--choregos-${statut}-soft`]) {
        expect(rapport(encre, fond, mode), `${encre} sur ${fond}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("une bordure de contrôle et le focus se voient (3:1), un filet aussi (1,3:1)", () => {
    for (const fond of ["--varga-surface", "--choregos-raised"]) {
      expect(rapport("--varga-line-strong", fond, mode), `bordure sur ${fond}`).toBeGreaterThanOrEqual(3);
      expect(rapport("--varga-line", fond, mode), `filet sur ${fond}`).toBeGreaterThanOrEqual(1.3);
    }
    for (const fond of FONDS) {
      expect(rapport("--varga-focus", fond, mode), `focus sur ${fond}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("la barre latérale est plus sombre que la page", () => {
    expect(luminance(jeton("--choregos-sidebar", mode))).toBeLessThan(luminance(jeton("--varga-surface", mode)));
  });
});
