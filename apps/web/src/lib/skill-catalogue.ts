// SPDX-License-Identifier: Apache-2.0
/** The skills catalogue as the console reads it (ADR 0065): filters, and the words that explain an entry. */
import { matchesText, toggled, type CatalogueFilters } from "@/lib/catalogue";
import type { SkillCatalogueEntry } from "@/lib/types";

export function filterSkillEntries(entries: SkillCatalogueEntry[], filters: CatalogueFilters): SkillCatalogueEntry[] {
  return entries.filter(
    (e) =>
      matchesText([e.id, e.title, e.publisher, e.description].join(" "), filters.q) &&
      (!filters.category || e.categories.includes(filters.category)) &&
      (!toggled(filters, "bundled") || e.delivery === "bundled") &&
      (!toggled(filters, "permissive") || e.licence_class === "permissive") &&
      (!toggled(filters, "lowrisk") || e.risk !== "high"),
  );
}

/** Whether the install button can be pressed, and the label it carries. */
export function installAction(entry: SkillCatalogueEntry): { label: string; disabled: boolean } {
  switch (entry.state) {
    case "installed":
      return { label: "Installed", disabled: true };
    case "pending_review":
      return { label: "Waiting for review", disabled: true };
    case "blocked":
      return { label: "Install", disabled: true };
    case "update":
      return { label: "Update", disabled: false };
    default:
      return { label: "Install", disabled: false };
  }
}

/** What the person is told before confirming: what they take on, in the order that matters. */
export function confirmationLines(entry: SkillCatalogueEntry): string[] {
  const lines = [
    `${entry.files} ${entry.files === 1 ? "file" : "files"}, ${(entry.bytes / 1024).toFixed(1)} KiB, from ${entry.repo.replace("https://github.com/", "")} at commit ${entry.sha.slice(0, 12)}.`,
    `Licence ${entry.licence} (${entry.licence_class}).`,
    entry.findings_count === 0
      ? "The scan found nothing."
      : `The scan found ${entry.findings_count} ${entry.findings_count === 1 ? "thing" : "things"} a reviewer should read.`,
  ];
  if (entry.licence_class === "copyleft") {
    lines.push("A copyleft licence: the files are fetched from the repository, not shipped in the image.");
  }
  if (entry.risk === "high") {
    lines.push("High risk: a second administrator will have to approve it before an agent can carry it.");
  }
  if (entry.delivery === "git") lines.push("Fetched from GitHub at install, at this exact commit.");
  return lines;
}
