import { EditorState, Text } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { forEachDiagnostic } from "@codemirror/lint";
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { diagnostic, YamlEditor } from "@/components/yaml-editor";

const YAML = "apiVersion: choregos/v1\nkind: Workflow\nmetadata: { name: demo, version: 1 }\nstates:\n  inbox: { display: Inbox }\n";

function vueDe(conteneur: HTMLElement): EditorView {
  const dom = conteneur.querySelector(".cm-editor") as HTMLElement;
  const vue = EditorView.findFromDOM(dom);
  if (!vue) throw new Error("aucun éditeur rendu");
  return vue;
}

describe("l'éditeur YAML (CodeMirror, S21-05)", () => {
  it("une erreur du serveur se pose sur sa ligne, de sa colonne au bout de la ligne", () => {
    const doc = Text.of(YAML.split("\n"));
    const d = diagnostic(doc, { code: "x", message: "unknown key", path: "states.inbox", line: 5, column: 12 }, "error");
    expect(doc.sliceString(d.from, d.to)).toBe("display: Inbox }");
    expect(d.message).toBe("unknown key (states.inbox)");
    // Une ligne hors du texte (le texte a changé depuis la validation) se ramène à la dernière.
    const hors = diagnostic(doc, { code: "x", message: "m", line: 99, column: 1 }, "warning");
    expect(hors.from).toBe(doc.line(doc.lines).from);
  });

  it("les erreurs du serveur se posent dans la marge de l'éditeur rendu", () => {
    const { container } = render(
      <YamlEditor label="YAML of demo" value={YAML} onChange={vi.fn()} issues={[{ code: "x", message: "bad state", line: 4, column: 1 }]} />,
    );
    const vue = vueDe(container);
    const lignes: number[] = [];
    forEachDiagnostic(vue.state, (_d, from) => lignes.push(vue.state.doc.lineAt(from).number));
    expect(lignes).toEqual([4]);
  });

  it("une frappe remonte par onChange ; un texte changé au-dehors remplace le document sans y revenir", () => {
    const onChange = vi.fn();
    const { container, rerender } = render(<YamlEditor label="YAML of demo" value={YAML} onChange={onChange} issues={[]} />);
    const vue = vueDe(container);
    act(() => vue.dispatch({ changes: { from: 0, insert: "# note\n" } }));
    expect(onChange).toHaveBeenLastCalledWith(`# note\n${YAML}`);
    onChange.mockClear();
    rerender(<YamlEditor label="YAML of demo" value={"kind: Workflow\n"} onChange={onChange} issues={[]} />);
    expect(vue.state.doc.toString()).toBe("kind: Workflow\n");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("le champ porte le nom donné aux lecteurs d'écran, et la lecture seule se respecte", () => {
    const { container, rerender } = render(
      <YamlEditor label="YAML of demo" value={YAML} onChange={vi.fn()} issues={[]} readOnly />,
    );
    expect(screen.getByLabelText("YAML of demo")).toHaveClass("cm-content");
    expect(vueDe(container).state.facet(EditorState.readOnly)).toBe(true);
    rerender(<YamlEditor label="YAML of demo" value={YAML} onChange={vi.fn()} issues={[]} />);
    expect(vueDe(container).state.facet(EditorState.readOnly)).toBe(false);
  });
});
