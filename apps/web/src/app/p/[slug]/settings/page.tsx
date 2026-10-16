// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { AgentsModelsSection } from "@/components/project-settings/agents-models-section";
import { ConnectionsSection } from "@/components/project-settings/connections-section";
import { GuardrailsSection } from "@/components/project-settings/guardrails-section";
import { SettingsNav } from "@/components/project-settings/settings-nav";
import { ToolsSection } from "@/components/project-settings/tools-section";

/** The sections of the page, in reading order: what the project is connected to first. */
const SECTIONS = [
  { id: "connections", label: "Connections" },
  { id: "agents-models", label: "Agents & models" },
  { id: "guardrails", label: "Guardrails" },
  { id: "tools", label: "Tools" },
];

/**
 * A project's settings, and they are edited here (S40-03b). The page only COMPOSES sections, one file
 * each under `components/project-settings/`, so a later section is added without touching the others:
 * the `readiness` slot below is filled by the readiness panel (S40-06c).
 *
 * Before: one two-column grid of unrelated cards with no heading, no way to tell what is required from
 * what is optional, a raw 380 px YAML editor, and a connector row printing the raw kind, the raw state
 * and no icon.
 */
export default function SettingsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return (
    <div className="gap-8 space-y-4 lg:grid lg:grid-cols-[11rem_minmax(0,1fr)] lg:space-y-0">
      <SettingsNav sections={SECTIONS} />
      <div className="min-w-0 space-y-10">
        <section id="readiness" aria-label="Readiness" data-testid="readiness-slot" className="empty:hidden" />
        <ConnectionsSection slug={slug} />
        <AgentsModelsSection slug={slug} />
        <GuardrailsSection slug={slug} />
        <ToolsSection slug={slug} />
      </div>
    </div>
  );
}
