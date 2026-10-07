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

export type PoigneeDeLEditeur = { allerALaLigne: (ligne: number) => void };

/**
 * Éditeur YAML (CodeMirror 6), avec les erreurs de l'API posées **dans la marge**.
 *
 * La validation reste celle du serveur — le même code que l'orchestrateur : ce qu'on voit ici est
 * ce qui s'appliquera. L'éditeur n'ajoute pas de règles, il place celles du serveur à la bonne
 * ligne. Monaco, avant lui, se téléchargeait à l'exécution depuis un CDN dont la CSP bloquait la
 * feuille de style et la police : l'éditeur du dev s'affichait sans style (revue du 07/10).
 * CodeMirror est empaqueté avec la console, sans worker, et ses styles passent par `'unsafe-inline'`.
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
  ref?: Ref<PoigneeDeLEditeur>;
}) {
  const hote = useRef<HTMLDivElement>(null);
  const vue = useRef<EditorView | null>(null);
  const surChangement = useRef(onChange);
  const lecture = useRef(new Compartment());
  useEffect(() => {
    surChangement.current = onChange;
  }, [onChange]);

  // Une vue par montage ; elle se détruit au démontage (StrictMode la monte deux fois).
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
          // Pas d'`indentWithTab` : Tab doit sortir de l'éditeur, sinon le clavier y reste piégé.
          keymap.of([...defaultKeymap, ...historyKeymap]),
          EditorState.tabSize.of(2),
          lecture.current.of(EditorState.readOnly.of(readOnly)),
          EditorView.contentAttributes.of({ "aria-label": label }),
          EditorView.updateListener.of((maj) => {
            // Ce qui vient du dehors (`value` changé) est marqué `remote` : il ne revient pas par onChange.
            const venuDuDehors = maj.transactions.some((tr) => tr.annotation(Transaction.remote));
            if (maj.docChanged && !venuDuDehors) surChangement.current(maj.state.doc.toString());
          }),
        ],
      }),
    });
    vue.current = instance;
    return () => {
      instance.destroy();
      vue.current = null;
    };
    // Le texte initial seulement : la suite arrive par l'effet ci-dessous.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Un texte changé au-dehors remplace le document.
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

  // Les erreurs et avertissements du serveur, à leur ligne.
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
    allerALaLigne(ligne: number) {
      const instance = vue.current;
      if (!instance) return;
      const cible = instance.state.doc.line(Math.min(Math.max(ligne, 1), instance.state.doc.lines));
      instance.dispatch({ selection: { anchor: cible.from }, effects: EditorView.scrollIntoView(cible.from, { y: "center" }) });
      instance.focus();
    },
  }));

  return (
    <div className="h-[28rem] overflow-hidden rounded border border-line" data-testid="yaml-editor">
      <div ref={hote} className="h-full" />
    </div>
  );
}

/** Une erreur du serveur (ligne et colonne comptées à partir de 1), soulignée jusqu'au bout de sa ligne. */
export function diagnostic(doc: Text, issue: Issue, severity: "error" | "warning"): Diagnostic {
  const ligne = doc.line(Math.min(Math.max(issue.line ?? 1, 1), doc.lines));
  const from = Math.min(ligne.from + Math.max((issue.column ?? 1) - 1, 0), ligne.to);
  return {
    from,
    to: Math.max(ligne.to, from),
    severity,
    message: issue.path ? `${issue.message} (${issue.path})` : issue.message,
    source: issue.code ?? "choregos",
  };
}

/** Les couleurs viennent des jetons du design system : clair et sombre suivent sans code. */
const THEME = EditorView.theme({
  "&": { height: "100%", fontSize: "12px", color: "var(--varga-ink)", backgroundColor: "var(--varga-surface)" },
  ".cm-scroller": { fontFamily: "var(--varga-font-text)", lineHeight: "1.6" },
  ".cm-content": { caretColor: "var(--varga-ink)" },
  // `ink-muted`, pas `ink-subtle` : les numéros de ligne doivent passer le contraste AA (axe le mesure).
  ".cm-gutters": { backgroundColor: "var(--varga-surface-muted)", color: "var(--varga-ink-muted)", borderRight: "1px solid var(--varga-line)" },
  ".cm-activeLine": { backgroundColor: "var(--varga-surface-muted)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--varga-surface-sunken)", color: "var(--varga-ink)" },
  "&.cm-focused": { outline: "2px solid var(--varga-focus)", outlineOffset: "-2px" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "var(--varga-accent-soft)" },
  ".cm-tooltip": { backgroundColor: "var(--varga-surface)", border: "1px solid var(--varga-line-strong)", color: "var(--varga-ink)" },
});

const SURLIGNAGE = HighlightStyle.define([
  { tag: [tags.propertyName, tags.definition(tags.propertyName)], color: "var(--varga-accent-strong)" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--varga-ink)" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--varga-warn)" },
  { tag: [tags.comment, tags.lineComment], color: "var(--varga-ink-muted)", fontStyle: "italic" },
  { tag: [tags.meta, tags.punctuation, tags.separator, tags.squareBracket, tags.brace], color: "var(--varga-ink-muted)" },
]);
