// SPDX-License-Identifier: Apache-2.0
"use client";

import type { ReactNode } from "react";

/**
 * Markdown written by an agent (or shipped with the platform: the setup procedures), rendered by
 * building React elements — never by injecting HTML: the text is not the console's, and the console
 * must not run it (ADR 0049). Headings, paragraphs, bullet and numbered lists (an item may wrap onto
 * indented lines and carry a nested list), fenced code, `code`, **bold** and *emphasis*; anything
 * else stays as text. Headings start at level 3, under the card's own title.
 *
 * Links are text unless the caller allows them: with `links`, a `[label](url)` whose url is `https:`
 * and in that list becomes a link that opens in a new tab; any other stays as its label, never as a
 * link. Without `links` nothing is ever linked and the text is shown as written.
 */
export function MarkdownView({ text, links }: { text: string; links?: readonly string[] }) {
  return (
    <div className="space-y-2 text-sm" data-testid="markdown-view">
      {blocks(text, links)}
    </div>
  );
}

function isAllowed(url: string, links: readonly string[]): boolean {
  if (!links.includes(url)) return false;
  try {
    return new URL(url).protocol === "https:";
  } catch {
    return false;
  }
}

function inline(text: string, links?: readonly string[]): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = links
    ? /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)\s]+\))/g
    : /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) parts.push(<code key={match.index} className="font-mono text-[0.92em]">{token.slice(1, -1)}</code>);
    else if (token.startsWith("**")) parts.push(<strong key={match.index}>{token.slice(2, -2)}</strong>);
    else if (token.startsWith("[")) {
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token)!;
      const label = link[1]!;
      const url = link[2]!;
      if (links && isAllowed(url, links)) {
        parts.push(
          <a key={match.index} href={url} target="_blank" rel="noopener noreferrer" className="underline">
            {label}
          </a>,
        );
      } else parts.push(label);
    } else parts.push(<em key={match.index}>{token.slice(1, -1)}</em>);
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

const MARKER = /^(\s*)([-*]|\d+\.)\s+(.*)$/;

type Item = { text: string; children: ReactNode[] };

/** One list at the indentation of `lines[from]`, with the lists nested under its items. */
function list(lines: string[], from: number, key: number, links?: readonly string[]): { node: ReactNode; next: number } {
  const first = MARKER.exec(lines[from]!)!;
  const base = first[1]!.length;
  const ordered = /\d/.test(first[2]!);
  const start = ordered ? Number.parseInt(first[2]!, 10) : undefined;
  const items: Item[] = [];
  let index = from;
  while (index < lines.length) {
    const line = lines[index]!;
    if (!line.trim()) {
      // A blank line inside a list: the list goes on only if the next thing is another item of it or deeper.
      let ahead = index + 1;
      while (ahead < lines.length && !lines[ahead]!.trim()) ahead += 1;
      const next = ahead < lines.length ? MARKER.exec(lines[ahead]!) : null;
      if (next && next[1]!.length >= base && (next[1]!.length > base || /\d/.test(next[2]!) === ordered)) {
        index = ahead;
        continue;
      }
      break;
    }
    const marker = MARKER.exec(line);
    const indent = line.length - line.trimStart().length;
    if (marker && indent === base && /\d/.test(marker[2]!) === ordered) {
      items.push({ text: marker[3]!, children: [] });
      index += 1;
    } else if (marker && indent > base && items.length > 0) {
      const nested = list(lines, index, items[items.length - 1]!.children.length, links);
      items[items.length - 1]!.children.push(nested.node);
      index = nested.next;
    } else if (!marker && indent > base && items.length > 0) {
      items[items.length - 1]!.text += ` ${line.trim()}`;
      index += 1;
    } else break;
  }
  const List = ordered ? "ol" : "ul";
  const node = (
    <List key={key} start={start && start !== 1 ? start : undefined} className={ordered ? "list-decimal pl-5" : "list-disc pl-5"}>
      {items.map((item, n) => (
        <li key={n}>
          {inline(item.text, links)}
          {item.children}
        </li>
      ))}
    </List>
  );
  return { node, next: index };
}

function blocks(text: string, links?: readonly string[]): ReactNode[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index]!;
    const key = out.length;
    if (line.startsWith("```")) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index]!.startsWith("```")) code.push(lines[index++]!);
      index += 1;
      out.push(
        <pre key={key} className="overflow-x-auto border border-line p-2 font-mono text-xs">
          {code.join("\n")}
        </pre>,
      );
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const level = Math.min(6, heading[1]!.length + 2);
      const Tag = `h${level}` as "h3" | "h4" | "h5" | "h6";
      out.push(
        <Tag key={key} className="font-semibold text-ink">
          {inline(heading[2]!, links)}
        </Tag>,
      );
      index += 1;
      continue;
    }
    if (MARKER.test(line)) {
      const result = list(lines, index, key, links);
      out.push(result.node);
      index = result.next;
      continue;
    }
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const paragraph: string[] = [];
    while (
      index < lines.length &&
      lines[index]!.trim() &&
      !/^(#{1,6}\s|```|\s*([-*]|\d+\.)\s+)/.test(lines[index]!)
    ) {
      paragraph.push(lines[index++]!);
    }
    out.push(<p key={key}>{inline(paragraph.join(" "), links)}</p>);
  }
  return out;
}
