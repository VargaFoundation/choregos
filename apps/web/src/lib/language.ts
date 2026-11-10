// SPDX-License-Identifier: Apache-2.0
/**
 * The language a person reads an agent's output in, as the console says it. The author of the agent
 * chooses it; the platform's own words and the machine fields of a result stay in English.
 */
import type { AgentSpec } from "./types";

/** The languages the builder offers, by tag; the API accepts any tag of the same shape. */
export const LANGUAGES: [string, string][] = [
  ["en", "English"],
  ["fr", "French"],
  ["de", "German"],
  ["es", "Spanish"],
  ["it", "Italian"],
  ["nl", "Dutch"],
  ["pt", "Portuguese"],
  ["pt-BR", "Brazilian Portuguese"],
  ["pl", "Polish"],
  ["ja", "Japanese"],
  ["zh", "Chinese"],
];

const NAMES = new Map(LANGUAGES);

/** `French (fr)`, or the bare tag when the console does not know its name. */
export function languageLabel(tag: string): string {
  const name = NAMES.get(tag);
  return name ? `${name} (${tag})` : tag;
}

/** What a run of this version writes in: the declared tag, `en` when the version leaves it unsaid. */
export function effectiveLanguage(spec: AgentSpec | null | undefined): string {
  return spec?.language || "en";
}

/** One line: the language, "(default)" when the version leaves it unsaid. */
export function languageLine(spec: AgentSpec | null | undefined): string {
  return `${languageLabel(effectiveLanguage(spec))}${spec?.language ? "" : " (default)"}`;
}
