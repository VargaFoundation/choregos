// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";
import { cn } from "@/lib/cn";
import { workflowHueClasses } from "@/lib/workflow-hue";

/**
 * A workflow, named in its own colour (ADR 0120): the same hue on the board, a ticket, the workflow
 * list and the map. The name is data (`data-donnee`), shown as written; the colour never speaks alone.
 */
export function WorkflowChip({
  name,
  version,
  href,
  size = "sm",
  className,
}: {
  name: string;
  version?: number | null;
  /** Where the chip leads: the workflow's page, usually. */
  href?: string;
  size?: "sm" | "md";
  className?: string;
}) {
  const hue = workflowHueClasses(name);
  const content = (
    <>
      <span
        aria-hidden
        className={cn(
          "inline-block shrink-0 rounded-full",
          hue.solid,
          size === "md" ? "size-2.5" : "size-2",
        )}
      />
      <span data-donnee className="truncate">
        {name}
      </span>
      {version != null && (
        <span className="font-mono">v{version}</span>
      )}
    </>
  );
  const classes = cn(
    "inline-flex max-w-full items-center gap-1.5 rounded-full border font-medium whitespace-nowrap",
    hue.pill,
    size === "md" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs leading-4",
    href && "no-underline hover:brightness-110",
    className,
  );
  return href ? (
    <Link href={href} className={classes} title={`Workflow ${name}`}>
      {content}
    </Link>
  ) : (
    <span className={classes} title={`Workflow ${name}`}>
      {content}
    </span>
  );
}
