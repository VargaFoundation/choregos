// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { SkillView } from "@/components/skills/skill-view";

/** A skill's page: its versions, the files of each, and the agents that carry it. */
export default function SkillPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return <SkillView slug={slug} />;
}
