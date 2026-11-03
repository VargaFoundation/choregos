// SPDX-License-Identifier: Apache-2.0
"use client";

import { notFound } from "next/navigation";
import { use } from "react";
import { SETTINGS_GROUPS } from "@/components/project-settings/groups";
import { SettingsSectionById } from "@/components/project-settings/section-by-id";

/** One group of a project's settings (S55-07): its few sections, one under the other. */
export default function SettingsGroupPage({ params }: { params: Promise<{ slug: string; group: string }> }) {
  const { slug, group: id } = use(params);
  const group = SETTINGS_GROUPS.find((g) => g.id === id);
  if (!group) notFound();
  return (
    <>
      {group.sections.map((section) => (
        <SettingsSectionById key={section.id} id={section.id} slug={slug} />
      ))}
    </>
  );
}
