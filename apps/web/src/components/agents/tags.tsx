// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { Input } from "@/components/ui";
import type { Agent } from "@/lib/types";

/**
 * An agent's tags (S40-10): labels people find it by. They are metadata, outside the versions: the
 * API takes lowercase slugs, at most ten, and the console normalises what is typed and says why a
 * tag is refused instead of leaving the server to answer 422.
 */

export const TAG_PATTERN = /^[a-z0-9][a-z0-9-]{0,31}$/;
export const MAX_TAGS = 10;

/** What a person typed, as a tag: trimmed, lowercase, accents stripped, runs of other characters become one dash. */
export function normalizeTag(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32)
    .replace(/-+$/g, "");
}

/** The sentence that says why a tag cannot be added, or null. */
export function tagProblem(tag: string, existing: readonly string[]): string | null {
  if (tag === "") return "A tag needs at least one letter or digit.";
  if (!TAG_PATTERN.test(tag)) return "A tag is lowercase letters, digits and dashes.";
  if (existing.includes(tag)) return `The tag ${tag} is already there.`;
  if (existing.length >= MAX_TAGS) return `An agent carries at most ${MAX_TAGS} tags.`;
  return null;
}

const CHIP = "inline-flex items-center gap-1 rounded-full border border-line bg-surface-muted px-2 py-0.5 text-xs text-ink-muted";

/** The chips of an agent; with `link`, each opens the registry filtered by that tag. */
export function TagList({ tags, link = false }: { tags?: readonly string[] | null; link?: boolean }) {
  if (!tags || tags.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Tags" data-testid="tags">
      {tags.map((tag) => (
        <li key={tag}>
          {link ? (
            <Link href={`/agents?tag=${encodeURIComponent(tag)}`} className={`${CHIP} hover:bg-accent-soft hover:text-accent-strong`} data-donnee>
              {tag}
            </Link>
          ) : (
            <span className={CHIP} data-donnee>
              {tag}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** An input that turns Enter or a comma into a chip; Backspace on an empty input removes the last one. */
export function TagEditor({
  value,
  onChange,
  suggestions = [],
  label = "Tags",
}: {
  value: string[];
  onChange: (tags: string[]) => void;
  suggestions?: readonly string[];
  label?: string;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  function add(raw: string) {
    const tag = normalizeTag(raw);
    if (tag === "" && raw.trim() === "") return;
    const why = tagProblem(tag, value);
    if (why) {
      setProblem(why);
      return;
    }
    setProblem(null);
    setText("");
    onChange([...value, tag]);
  }
  const hint = `${value.length} of ${MAX_TAGS}. Enter or a comma adds a tag; lowercase letters, digits and dashes.`;
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-xs font-medium text-ink-muted">
        {label}
      </label>
      {value.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Tags on this agent">
          {value.map((tag) => (
            <li key={tag} className={CHIP} data-donnee>
              {tag}
              <button type="button" className="rounded-full px-1 hover:bg-line" onClick={() => onChange(value.filter((t) => t !== tag))}>
                <span aria-hidden="true">×</span>
                <span className="sr-only">Remove {tag}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Input
        id={id}
        value={text}
        list={`${id}-suggestions`}
        aria-describedby={`${id}-hint`}
        aria-invalid={problem ? true : undefined}
        onChange={(e) => {
          const typed = e.target.value;
          if (typed.includes(",")) {
            const [head, ...rest] = typed.split(",");
            add(head ?? "");
            if (rest.join("").trim()) setText(rest.join(""));
            return;
          }
          setProblem(null);
          setText(typed);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(text);
          } else if (e.key === "Backspace" && text === "" && value.length > 0) {
            onChange(value.slice(0, -1));
          }
        }}
      />
      <datalist id={`${id}-suggestions`}>
        {suggestions.filter((s) => !value.includes(s)).map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
      <p id={`${id}-hint`} className={`text-xs ${problem ? "text-danger" : "text-ink-muted"}`} role={problem ? "alert" : undefined}>
        {problem ?? hint}
      </p>
    </div>
  );
}

/** Every tag of the visible agents with its count; several can be selected, an agent must carry all of them. */
export function TagFilter({
  agents,
  value,
  onChange,
}: {
  agents: Pick<Agent, "tags">[];
  value: string[];
  onChange: (tags: string[]) => void;
}) {
  const counts = new Map<string, number>();
  for (const agent of agents) for (const tag of agent.tags ?? []) counts.set(tag, (counts.get(tag) ?? 0) + 1);
  const chip = (active: boolean) =>
    `rounded-full border px-3 py-1 text-xs ${active ? "border-accent-line bg-accent-soft text-accent-strong" : "border-line text-ink-muted hover:bg-surface-muted"}`;
  const all = [...new Set([...counts.keys(), ...value])].sort();
  if (all.length === 0) return null;
  return (
    <div role="group" aria-label="Filter by tag" className="flex flex-wrap gap-2" data-testid="tag-filter">
      {all.map((tag) => {
        const active = value.includes(tag);
        return (
          <button
            key={tag}
            type="button"
            className={chip(active)}
            aria-pressed={active}
            data-donnee
            onClick={() => onChange(active ? value.filter((t) => t !== tag) : [...value, tag])}
          >
            {tag} ({counts.get(tag) ?? 0})
          </button>
        );
      })}
    </div>
  );
}

/** The agents carrying every selected tag. */
export function withTags<T extends Pick<Agent, "tags">>(agents: T[], selected: readonly string[]): T[] {
  return agents.filter((a) => selected.every((t) => (a.tags ?? []).includes(t)));
}
