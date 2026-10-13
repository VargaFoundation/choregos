// SPDX-License-Identifier: Apache-2.0
"use client";

import type { ReactNode } from "react";

/**
 * Markdown written by an agent, rendered by building React elements — never by injecting HTML: the
 * text is the agent's, and the console must not run it (ADR 0049). Headings, paragraphs, lists,
 * fenced code, `code`, **bold** and *emphasis*; anything else stays as text. Headings start at
 * level 3, under the card's own title.
 */
export function MarkdownView({ text }: { text: string }) {
  return (
    <div className="space-y-2 text-sm" data-testid="markdown-view">
      {blocks(text)}
    </div>
  );
}

function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) parts.push(<code key={match.index} className="font-mono text-[0.92em]">{token.slice(1, -1)}</code>);
    else if (token.startsWith("**")) parts.push(<strong key={match.index}>{token.slice(2, -2)}</strong>);
    else parts.push(<em key={match.index}>{token.slice(1, -1)}</em>);
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function blocks(text: string): ReactNode[] {
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
          {inline(heading[2]!)}
        </Tag>,
      );
      index += 1;
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (index < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[index]!)) {
        items.push(lines[index]!.replace(/^\s*([-*]|\d+\.)\s+/, ""));
        index += 1;
      }
      const List = ordered ? "ol" : "ul";
      out.push(
        <List key={key} className={ordered ? "list-decimal pl-5" : "list-disc pl-5"}>
          {items.map((item, n) => (
            <li key={n}>{inline(item)}</li>
          ))}
        </List>,
      );
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
    out.push(<p key={key}>{inline(paragraph.join(" "))}</p>);
  }
  return out;
}
