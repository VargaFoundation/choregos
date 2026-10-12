// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { ProjectDto } from "./types";

/**
 * Trains exist only when an active workflow of the project has a `via: release_train` transition
 * (S30-07): the engine starts none otherwise, and an HR project has nothing to ship. The API says
 * so in `has_release_train`; the console then hides the trains tab and the trains counters.
 */
export function hasReleaseTrain(project: Pick<ProjectDto, "has_release_train"> | undefined): boolean {
  return project?.has_release_train === true;
}

/** The same answer for a project read by its slug, sharing the project query of the layout. */
export function useHasReleaseTrain(slug: string): boolean {
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  return hasReleaseTrain(project.data);
}
