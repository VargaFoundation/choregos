// SPDX-License-Identifier: Apache-2.0
/** Formatting: money, tokens, durations, relative dates. Used everywhere, tested. */

export function eur(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR" }).format(value);
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: "USD" }).format(value);
}

export function tokens(value: number | null | undefined): string {
  if (!value) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} M`;
  if (value >= 1_000) return `${Math.round(value / 1_000)} k`;
  return String(value);
}

export function duration(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  const total = Math.round(seconds);
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  if (days) return `${days} d ${hours} h`;
  if (hours) return `${hours} h ${minutes} min`;
  if (minutes) return `${minutes} min`;
  return `${total} s`;
}

export function relative(timestamp: string | null | undefined): string {
  if (!timestamp) return "—";
  const delta = (Date.now() - new Date(timestamp).getTime()) / 1000;
  const format = new Intl.RelativeTimeFormat("en-GB", { numeric: "auto" });
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(delta) >= size) return format.format(-Math.round(delta / size), unit);
  }
  return format.format(-Math.round(delta), "second");
}

export function shortDate(timestamp: string | null | undefined): string {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "short", timeStyle: "short" }).format(
    new Date(timestamp),
  );
}

/**
 * A status code read by a person (S23-11): `rolled_back` becomes "rolled back", `FAILED` becomes
 * "failed". The console showed the API's codes as they were when the status had no label.
 */
export function libelleDeCode(code: string): string {
  return code.replace(/_/g, " ").toLowerCase();
}

/**
 * Sentence case for a label built from a code or a lowercase phrase (ADR 0048): the first letter
 * only, the rest as it is: `release train` becomes "Release train", `ci_green` stays `ci_green`
 * where it is shown as code.
 */
export function toSentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The segments between backticks in an engine sentence (`refiner`, `spec_markdown`): code. */
export function segmentsDeCode(text: string): { code: boolean; text: string }[] {
  return text
    .split("`")
    .map((morceau, index) => ({ code: index % 2 === 1, text: morceau }))
    .filter((segment) => segment.text !== "");
}

/** A ratio shown as a percentage, without false precision. */
export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)} %`;
}
