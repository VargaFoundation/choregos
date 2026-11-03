// SPDX-License-Identifier: Apache-2.0
"use client";

import { useId } from "react";
import { cn } from "@/lib/cn";
import { PREFERENCES_DE_THEME, type PreferenceDeTheme } from "@/lib/preferences";
import { useTheme } from "@/lib/theme";

const LIBELLES: Record<PreferenceDeTheme, string> = { dark: "Dark", light: "Light", system: "System" };

/**
 * The theme choice: dark, light, or the system's (ADR 0043). Three radio buttons in a
 * named group: with the keyboard, the arrows move from one choice to the other.
 *
 * Each instance has its own group (`useId`): the header and the menu panel each carry one,
 * and two groups of the same name would make only one: checking one would uncheck the other.
 */
export function ThemePicker({ className }: { className?: string }) {
  const { preference, choisir } = useTheme();
  const group = useId();
  return (
    <fieldset className={cn("flex items-center gap-1", className)}>
      <legend className="sr-only">Theme</legend>
      {PREFERENCES_DE_THEME.map((valeur) => (
        <label
          key={valeur}
          className={cn(
            "inline-flex cursor-pointer items-center rounded px-2 py-1 text-[11px] pointer-coarse:min-h-11",
            // The radio button is hidden: its label is what shows the keyboard focus.
            "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus",
            preference === valeur ? "bg-accent-soft text-accent-strong" : "text-ink-muted hover:text-ink",
          )}
        >
          <input
            type="radio"
            name={group}
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
