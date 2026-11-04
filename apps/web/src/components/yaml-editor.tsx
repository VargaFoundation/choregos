// SPDX-License-Identifier: Apache-2.0
"use client";

import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { yaml } from "@codemirror/lang-yaml";
import { bracketMatching, HighlightStyle, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { type Diagnostic, lintGutter, setDiagnostics } from "@codemirror/lint";
import { Compartment, EditorState, Transaction, type Text } from "@codemirror/state";
import { drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { type Ref, useEffect, useImperativeHandle, useRef } from "react";
import type { WorkflowValidation } from "@/lib/types";

type Issue = NonNullable<WorkflowValidation["errors"]>[number];

export type EditorHandle = { goToLine: (line: number) => void };

/**
 * YAML editor (CodeMirror 6), with the API's errors placed **in the margin**.
 *
 * Validation remains the server's, the same code as the orchestrator: what is seen here is
 * what will apply. The editor adds no rules, it places the server's on the right
 * line. Monaco, before it, was downloaded at run time from a CDN whose CSP blocked the
 * stylesheet and the font: the dev's editor displayed unstyled (review of 7 Oct).
 * CodeMirror is bundled with the console, without a worker, and its styles go through `'unsafe-inline'`.
 */
export function YamlEditor({
  value,
  onChange,
  issues,
  warnings,
  label,
  readOnly = false,
  ref,
}: {
  value: string;
  onChange: (next: string) => void;
  issues: Issue[];
  warnings?: Issue[];
  label: string;
  readOnly?: boolean;
  ref?: Ref<EditorHandle>;
}) {
  const hote = useRef<HTMLDivElement>(null);
  const vue = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const lecture = useRef(new Compartment());
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  // One view per mount; it is destroyed on unmount (StrictMode mounts it twice).
  useEffect(() => {
    if (!hote.current) return;
    const instance = new EditorView({
      parent: hote.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          history(),
          drawSelection(),
          highlightActiveLine(),
          indentOnInput(),
          bracketMatching(),
          lintGutter(),
          yaml(),
          syntaxHighlighting(SURLIGNAGE),
          THEME,
          // In mono (ADR 0048) a long line no longer fits: it wraps, so the scroller never scrolls
          // sideways — a sideways region that holds no focusable element fails axe (read-only view).
          EditorView.lineWrapping,
          // No `indentWithTab`: Tab must leave the editor, otherwise the keyboard stays trapped in it.
          keymap.of([...defaultKeymap, ...historyKeymap]),
          EditorState.tabSize.of(2),
          lecture.current.of(EditorState.readOnly.of(readOnly)),
          EditorView.contentAttributes.of({ "aria-label": label }),
          EditorView.updateListener.of((maj) => {
            // What comes from outside (`value` changed) is marked `remote`: it does not come back through onChange.
            const venuDuDehors = maj.transactions.some((tr) => tr.annotation(Transaction.remote));
            if (maj.docChanged && !venuDuDehors) onChangeRef.current(maj.state.doc.toString());
          }),
        ],
      }),
    });
    vue.current = instance;
    return () => {
      instance.destroy();
      vue.current = null;
    };
    // The initial text only: what follows arrives through the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A text changed from outside replaces the document.
  useEffect(() => {
    const instance = vue.current;
    if (!instance || instance.state.doc.toString() === value) return;
    instance.dispatch({
      changes: { from: 0, to: instance.state.doc.length, insert: value },
      annotations: Transaction.remote.of(true),
    });
  }, [value]);

  useEffect(() => {
    vue.current?.dispatch({ effects: lecture.current.reconfigure(EditorState.readOnly.of(readOnly)) });
  }, [readOnly]);

  // The server's errors and warnings, at their line.
  useEffect(() => {
    const instance = vue.current;
    if (!instance) return;
    const doc = instance.state.doc;
    const diagnostics = [
      ...issues.map((issue) => diagnostic(doc, issue, "error")),
      ...(warnings ?? []).map((issue) => diagnostic(doc, issue, "warning")),
    ];
    instance.dispatch(setDiagnostics(instance.state, diagnostics));
  }, [issues, warnings, value]);

  useImperativeHandle(ref, () => ({
    goToLine(line: number) {
      const instance = vue.current;
      if (!instance) return;
      const target = instance.state.doc.line(Math.min(Math.max(line, 1), instance.state.doc.lines));
      instance.dispatch({ selection: { anchor: target.from }, effects: EditorView.scrollIntoView(target.from, { y: "center" }) });
      instance.focus();
    },
  }));

  return (
    <div className="h-[28rem] overflow-hidden rounded border border-line" data-testid="yaml-editor">
      <div ref={hote} className="h-full" />
    </div>
  );
}

/** A server error (line and column counted from 1), underlined to the end of its line. */
export function diagnostic(doc: Text, issue: Issue, severity: "error" | "warning"): Diagnostic {
  const line = doc.line(Math.min(Math.max(issue.line ?? 1, 1), doc.lines));
  const from = Math.min(line.from + Math.max((issue.column ?? 1) - 1, 0), line.to);
  return {
    from,
    to: Math.max(line.to, from),
    severity,
    message: issue.path ? `${issue.message} (${issue.path})` : issue.message,
    source: issue.code ?? "choregos",
  };
}

/** The colours come from the console's tokens (`--choregos-*`): light and dark follow with no code. */
const THEME = EditorView.theme({
  "&": { height: "100%", fontSize: "12px", color: "var(--choregos-ink)", backgroundColor: "var(--choregos-surface)" },
  ".cm-scroller": { fontFamily: "var(--choregos-font-mono)", lineHeight: "1.6" },
  ".cm-content": { caretColor: "var(--choregos-ink)" },
  // `ink-muted`, not `ink-subtle`: the line numbers must pass the AA contrast (axe measures it).
  ".cm-gutters": { backgroundColor: "var(--choregos-surface-muted)", color: "var(--choregos-ink-muted)", borderRight: "1px solid var(--choregos-line)" },
  ".cm-activeLine": { backgroundColor: "var(--choregos-surface-muted)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--choregos-surface-sunken)", color: "var(--choregos-ink)" },
  "&.cm-focused": { outline: "2px solid var(--choregos-focus)", outlineOffset: "-2px" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "var(--choregos-accent-soft)" },
  ".cm-tooltip": { backgroundColor: "var(--choregos-surface)", border: "1px solid var(--choregos-line-strong)", color: "var(--choregos-ink)" },
});

const SURLIGNAGE = HighlightStyle.define([
  { tag: [tags.propertyName, tags.definition(tags.propertyName)], color: "var(--choregos-accent-strong)" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--choregos-ink)" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--choregos-warn)" },
  { tag: [tags.comment, tags.lineComment], color: "var(--choregos-ink-muted)", fontStyle: "italic" },
  { tag: [tags.meta, tags.punctuation, tags.separator, tags.squareBracket, tags.brace], color: "var(--choregos-ink-muted)" },
]);
