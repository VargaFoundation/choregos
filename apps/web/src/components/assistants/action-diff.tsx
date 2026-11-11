// SPDX-License-Identifier: Apache-2.0

/**
 * What the source says the write would change (the `diff` its `<operation>_preview` returned at the
 * proposal), shown before the person confirms. Nothing when the source serves no preview.
 */
export function ActionDiff({ diff }: { diff?: Record<string, unknown> | null }) {
  if (!diff || Object.keys(diff).length === 0) return null;
  return (
    <pre aria-label="What would change" data-testid="action-diff" className="mt-2 overflow-x-auto rounded bg-surface-muted p-2 text-xs">
      {JSON.stringify(diff, null, 2)}
    </pre>
  );
}
