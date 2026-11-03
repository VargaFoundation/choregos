// SPDX-License-Identifier: Apache-2.0
"use client";

import { use, type ReactNode } from "react";
import { SettingsNav } from "@/components/project-settings/settings-nav";

/** The settings' frame (S55-07): the groups on the left, the chosen one on the right. */
export default function SettingsLayout({ children, params }: { children: ReactNode; params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return (
    <div className="gap-8 space-y-4 lg:grid lg:grid-cols-[13rem_minmax(0,1fr)] lg:space-y-0">
      <SettingsNav slug={slug} />
      <div className="min-w-0 space-y-10">{children}</div>
    </div>
  );
}
