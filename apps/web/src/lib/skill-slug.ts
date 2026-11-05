// SPDX-License-Identifier: Apache-2.0
/**
 * What a new skill may be called (S55-17). A skill's page is `/skills/<slug>`, and the console's own
 * pages under `/skills/` win over it: `library` is the skills library and `catalogue` redirects to it
 * (S55-15), so a skill of either name could never be opened. The API refuses those slugs for a new
 * skill too (`services/skills.py`, `RESERVED_SKILL_SLUGS`); a skill that already has one keeps
 * publishing its next versions.
 */
export const RESERVED_SKILL_SLUGS: readonly string[] = ["library", "catalogue"];

/** Why a new skill cannot take this slug, or `undefined` when it can. */
export function skillSlugError(slug: string): string | undefined {
  if (RESERVED_SKILL_SLUGS.includes(slug)) {
    return `${slug} is reserved: the console has a page at that address — rename the skill in its SKILL.md`;
  }
  return undefined;
}

/**
 * Where a skill opens (S55-18): its own page, `/skills/<slug>`, except for a skill published under a
 * reserved slug before S55-17. That address is a console page, so such a skill opens on the skills
 * list, which shows it in place of the list (`/skills?skill=<slug>`).
 */
export function skillHref(slug: string): string {
  return RESERVED_SKILL_SLUGS.includes(slug) ? `/skills?skill=${encodeURIComponent(slug)}` : `/skills/${slug}`;
}
