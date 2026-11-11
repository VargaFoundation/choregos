// SPDX-License-Identifier: Apache-2.0
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { LearnMore } from "@/components/docs-link";
import { Heading } from "@/components/ui";
import { cn } from "@/lib/cn";
import { HUE_CLASSES, type Hue } from "@/lib/workflow-hue";

/**
 * The head of a page (S55-02): an icon on a tint of its family's hue, the title, an eyebrow above it
 * (where the page is) and one line below it (what it is for). The colour says where one stands before
 * the words do (ADR 0120); the words still say it.
 *
 * A page's own header (`h1`) ends with "Learn more" when the documentation has a page for the screen
 * (S62-11, `DOCS_BY_ROUTE`): one map, so no screen names a page the site does not have.
 */
export function PageHeader({
  icon: Icon,
  hue,
  eyebrow,
  title,
  description,
  actions,
  as = "h1",
  className,
}: {
  icon?: LucideIcon;
  hue?: Hue;
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  as?: "h1" | "h2";
  className?: string;
}) {
  const classes = hue ? HUE_CLASSES[hue] : undefined;
  return (
    <header
      className={cn("flex flex-wrap items-center gap-x-4 gap-y-3", className)}
    >
      {Icon && (
        <span
          aria-hidden
          data-testid="page-header-icon"
          className={cn(
            "flex size-10 shrink-0 items-center justify-center rounded-md border",
            classes
              ? cn(classes.soft, classes.line, classes.ink)
              : "border-line bg-surface-muted text-ink-muted",
          )}
        >
          <Icon className="size-5" strokeWidth={1.75} />
        </span>
      )}
      <div className="min-w-0 flex-1">
        {eyebrow && (
          <div className="text-xs font-medium text-ink-muted">{eyebrow}</div>
        )}
        <Heading as={as} size="xl" className="truncate">
          {title}
        </Heading>
        {description && (
          <p className="mt-0.5 max-w-3xl text-sm text-ink-muted">
            {description}
          </p>
        )}
      </div>
      {(actions || as === "h1") && (
        <div className="flex flex-wrap items-center gap-2">
          {as === "h1" && <LearnMore />}
          {actions}
        </div>
      )}
    </header>
  );
}
