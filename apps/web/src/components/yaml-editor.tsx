"use client";

import Editor, { type OnMount } from "@monaco-editor/react";
import { useCallback, useEffect, useState } from "react";
import type { WorkflowValidation } from "@/lib/types";

type Issue = NonNullable<WorkflowValidation["errors"]>[number];

/**
 * Éditeur YAML Monaco, avec les erreurs de l'API posées **dans la marge**.
 *
 * La validation reste celle du serveur — le même code que l'orchestrateur : ce qu'on voit
 * ici est ce qui s'appliquera. Monaco n'ajoute pas de règles, il place celles du serveur
 * à la bonne ligne. Le chargement est paresseux : la page reste utilisable sans lui, et
 * un `textarea` prend le relais tant que l'éditeur n'est pas là.
 */
export function YamlEditor({
  value,
  onChange,
  issues,
  warnings,
  label,
}: {
  value: string;
  onChange: (next: string) => void;
  issues: Issue[];
  warnings?: Issue[];
  label: string;
}) {
  const [monaco, setMonaco] = useState<Parameters<OnMount>[1] | null>(null);
  const [editor, setEditor] = useState<Parameters<OnMount>[0] | null>(null);
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => setDark(document.documentElement.classList.contains("dark") || media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  const onMount = useCallback<OnMount>((instance, api) => {
    setEditor(instance);
    setMonaco(api);
  }, []);

  useEffect(() => {
    if (!monaco || !editor) return;
    const model = editor.getModel();
    if (!model) return;
    const markers = [
      ...issues.map((issue) => marker(monaco, issue, "error")),
      ...(warnings ?? []).map((issue) => marker(monaco, issue, "warning")),
    ];
    monaco.editor.setModelMarkers(model, "choregos", markers);
  }, [monaco, editor, issues, warnings]);

  return (
    <div className="h-[28rem] overflow-hidden rounded border border-line" data-testid="yaml-editor">
      <Editor
        height="100%"
        defaultLanguage="yaml"
        value={value}
        onChange={(next) => onChange(next ?? "")}
        theme={dark ? "vs-dark" : "light"}
        options={{
          minimap: { enabled: false },
          fontSize: 12,
          tabSize: 2,
          scrollBeyondLastLine: false,
          renderWhitespace: "boundary",
          ariaLabel: label,
        }}
        loading={
          <textarea
            aria-label={label}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            spellCheck={false}
            className="h-full w-full bg-surface p-3 font-mono text-xs"
          />
        }
        onMount={onMount}
      />
    </div>
  );
}

function marker(
  monaco: NonNullable<Parameters<OnMount>[1]>,
  issue: Issue,
  severity: "error" | "warning",
) {
  const line = issue.line ?? 1;
  const column = issue.column ?? 1;
  return {
    startLineNumber: line,
    endLineNumber: line,
    startColumn: column,
    // Sans fin connue, on souligne jusqu'au bout de la ligne plutôt qu'un seul caractère.
    endColumn: column + 200,
    message: issue.path ? `${issue.message} (${issue.path})` : issue.message,
    severity:
      severity === "error"
        ? monaco.MarkerSeverity.Error
        : monaco.MarkerSeverity.Warning,
    source: issue.code ?? "choregos",
  };
}
