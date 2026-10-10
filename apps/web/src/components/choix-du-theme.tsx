// SPDX-License-Identifier: Apache-2.0
"use client";

import { useId } from "react";
import { cn } from "@/lib/cn";
import { PREFERENCES_DE_THEME, type PreferenceDeTheme } from "@/lib/preferences";
import { useTheme } from "@/lib/theme";

const LIBELLES: Record<PreferenceDeTheme, string> = { dark: "dark", light: "light", system: "system" };

/**
 * Le choix du thème : sombre, clair, ou celui du système (ADR 0043). Trois boutons radio dans un
 * groupe nommé — au clavier, les flèches passent d'un choix à l'autre.
 *
 * Chaque exemplaire a son propre groupe (`useId`) : l'en-tête et le panneau du menu en portent un
 * chacun, et deux groupes du même nom n'en feraient qu'un — cocher l'un décocherait l'autre.
 */
export function ChoixDuTheme({ className }: { className?: string }) {
  const { preference, choisir } = useTheme();
  const groupe = useId();
  return (
    <fieldset className={cn("flex items-center gap-1", className)}>
      <legend className="sr-only">theme</legend>
      {PREFERENCES_DE_THEME.map((valeur) => (
        <label
          key={valeur}
          className={cn(
            "inline-flex cursor-pointer items-center px-2 py-1 font-mono text-[11px] pointer-coarse:min-h-11",
            // Le bouton radio est caché : c'est son libellé qui montre le focus clavier.
            "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus",
            preference === valeur ? "bg-accent-soft text-accent-strong" : "text-ink-muted hover:text-ink",
          )}
        >
          <input
            type="radio"
            name={groupe}
            value={valeur}
            checked={preference === valeur}
            onChange={() => choisir(valeur)}
            className="sr-only"
          />
          {LIBELLES[valeur]}
        </label>
      ))}
    </fieldset>
  );
}
