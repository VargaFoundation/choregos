// SPDX-License-Identifier: Apache-2.0
import { Bot, Plug, ShieldCheck, SlidersHorizontal, TriangleAlert, Users, type LucideIcon } from "lucide-react";
import type { Hue } from "@/lib/workflow-hue";

/**
 * A project's settings, in groups (S55-07): twelve sections on one page read as an endless scroll with
 * nothing to tell them apart. Each group is a page of its own, with an icon and a hue (ADR 0120); the
 * settings' first page is an overview of the groups and what the project still lacks.
 */
export interface SettingsGroup {
  id: string;
  label: string;
  /** One line: what the group is for. */
  lead: string;
  icon: LucideIcon;
  /** The group's hue; the danger zone has none, it is red. */
  hue?: Hue;
  /** The sections it shows, by their anchor, in order. */
  sections: { id: string; label: string }[];
}

export const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    id: "general",
    label: "General",
    lead: "The project's name, status, repository and the checks it runs.",
    icon: SlidersHorizontal,
    hue: 1,
    sections: [{ id: "general", label: "General" }],
  },
  {
    id: "connections",
    label: "Connections",
    lead: "The tracker, repository, CD and runtime the project works through, and where it notifies.",
    icon: Plug,
    hue: 5,
    sections: [
      { id: "connections", label: "Connections" },
      { id: "notifications", label: "Notifications" },
    ],
  },
  {
    id: "agents",
    label: "Agents",
    lead: "The backend and models agents run on, the tools they may call, what they know.",
    icon: Bot,
    hue: 8,
    sections: [
      { id: "agents-models", label: "Agents & models" },
      { id: "tools", label: "Tools" },
      { id: "a2a", label: "A2A" },
      { id: "knowledge-memory", label: "Knowledge & memory" },
    ],
  },
  {
    id: "security",
    label: "Guardrails",
    lead: "The limits a run cannot cross and the hosts it may reach.",
    icon: ShieldCheck,
    hue: 6,
    sections: [
      { id: "guardrails", label: "Guardrails" },
      { id: "network", label: "Network" },
    ],
  },
  {
    id: "people",
    label: "People",
    lead: "Who works on the project, and in which role.",
    icon: Users,
    hue: 3,
    sections: [{ id: "people", label: "People" }],
  },
  {
    id: "danger",
    label: "Danger zone",
    lead: "Suspend or archive the project.",
    icon: TriangleAlert,
    sections: [{ id: "danger-zone", label: "Danger zone" }],
  },
];

/** Anchors the API or older links name that are not a section's own id. */
const ALIASES: Record<string, string> = { agents: "agents-models", policy: "guardrails" };

/** The group an anchor belongs to (`#tools` → agents), or undefined for an anchor no section has. */
export function groupOfAnchor(anchor: string): SettingsGroup | undefined {
  const id = ALIASES[anchor] ?? anchor;
  return SETTINGS_GROUPS.find((group) => group.id === id || group.sections.some((section) => section.id === id));
}

/** Where a settings anchor lives now: `/p/<slug>/settings/<group>#<section>`. */
export function settingsHref(slug: string, anchor: string): string {
  const base = `/p/${encodeURIComponent(slug)}/settings`;
  const group = groupOfAnchor(anchor);
  if (!group) return base;
  const id = ALIASES[anchor] ?? anchor;
  return group.sections.some((section) => section.id === id) && group.sections.length > 1
    ? `${base}/${group.id}#${id}`
    : `${base}/${group.id}`;
}

