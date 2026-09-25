/** Formatage FR : argent, tokens, durées, dates relatives. Utilisé partout, testé. */

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

/** Ratio affiché en pourcentage, sans fausse précision. */
export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)} %`;
}
