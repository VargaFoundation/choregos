// SPDX-License-Identifier: Apache-2.0
/**
 * A project's repositories (ADR 0124): the primary `repo` and the named `repositories` beside it. The
 * names are what a work item's `fields.repository` holds; the primary without a `name` is named after
 * its URL's last segment, as the API names it (`ProjectConfig.primary_name`).
 */
import type { ProjectDto } from "@/lib/types";

type Config = ProjectDto["config"];

/** `https://github.com/acme/Infra.git` → `infra`: the name the API gives a primary without one. */
export function nameOfUrl(url: string): string {
  const last = url.trim().replace(/\/$/, "").replace(/\.git$/, "").split("/").pop()?.split(":").pop() ?? "";
  const slug = last
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/g, "");
  return slug || "primary";
}

/** The primary's name, `null` for a project without a repository. */
export function primaryName(config: Config | undefined): string | null {
  const repo = config?.repo;
  if (!repo?.url) return null;
  return repo.name || nameOfUrl(repo.url);
}

/** Every repository's name, the primary first. */
export function repositoryNames(config: Config | undefined): string[] {
  const primary = primaryName(config);
  if (primary === null) return [];
  return [primary, ...(config?.repositories ?? []).map((r) => r.name)];
}
