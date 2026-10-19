// SPDX-License-Identifier: Apache-2.0
"use client";

import { useEffect, useState } from "react";
import { Tabs, tabClasses } from "@/components/ui";
import { cn } from "@/lib/cn";

export interface SettingsSectionLink {
  id: string;
  label: string;
}

/**
 * The in-page navigation: a strip of anchors on small screens (`Tabs`), a sticky list at the left
 * from `lg` up. The active section follows the scroll (`IntersectionObserver`); where it does not
 * exist the links still work, they are plain anchors.
 */
export function SettingsNav({ sections }: { sections: SettingsSectionLink[] }) {
  const [active, setActive] = useState(sections[0]?.id ?? "");
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const visible = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.add(entry.target.id);
          else visible.delete(entry.target.id);
        }
        const first = sections.find((s) => visible.has(s.id));
        if (first) setActive(first.id);
      },
      { rootMargin: "0px 0px -60% 0px" },
    );
    for (const { id } of sections) {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [sections]);
  return (
    <Tabs aria-label="Settings sections" className="lg:sticky lg:top-4 lg:flex-col lg:gap-1 lg:self-start lg:border-b-0">
      {sections.map((section) => (
        <a
          key={section.id}
          href={`#${section.id}`}
          aria-current={active === section.id ? "location" : undefined}
          onClick={() => setActive(section.id)}
          className={cn(tabClasses(active === section.id), "lg:border-b-0 lg:border-l-2 lg:px-3 lg:py-1.5", active === section.id ? "lg:border-accent" : "lg:border-transparent")}
        >
          {section.label}
        </a>
      ))}
    </Tabs>
  );
}
