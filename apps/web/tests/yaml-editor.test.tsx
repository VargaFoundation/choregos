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
  if (!vue) throw new Error("no editor rendered");
  return vue;
}

describe("the YAML editor (CodeMirror, S21-05)", () => {
  it("a server error is placed on its line, from its column to the end of the line", () => {
    const doc = Text.of(YAML.split("\n"));
    const d = diagnostic(doc, { code: "x", message: "unknown key", path: "states.inbox", line: 5, column: 12 }, "error");
    expect(doc.sliceString(d.from, d.to)).toBe("display: Inbox }");
    expect(d.message).toBe("unknown key (states.inbox)");
    // A line outside the text (the text changed since the validation) is brought back to the last one.
    const hors = diagnostic(doc, { code: "x", message: "m", line: 99, column: 1 }, "warning");
    expect(hors.from).toBe(doc.line(doc.lines).from);
  });

  it("the server's errors are placed in the rendered editor's margin", () => {
    const { container } = render(
      <YamlEditor label="YAML of demo" value={YAML} onChange={vi.fn()} issues={[{ code: "x", message: "bad state", line: 4, column: 1 }]} />,
    );
    const vue = vueDe(container);
    const rows: number[] = [];
    forEachDiagnostic(vue.state, (_d, from) => rows.push(vue.state.doc.lineAt(from).number));
    expect(rows).toEqual([4]);
  });

  it("a keystroke goes up through onChange; a text changed from outside replaces the document without coming back", () => {
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

  it("the field carries the name given to screen readers, and read-only is respected", () => {
    const { container, rerender } = render(
      <YamlEditor label="YAML of demo" value={YAML} onChange={vi.fn()} issues={[]} readOnly />,
    );
    expect(screen.getByLabelText("YAML of demo")).toHaveClass("cm-content");
    expect(vueDe(container).state.facet(EditorState.readOnly)).toBe(true);
    rerender(<YamlEditor label="YAML of demo" value={YAML} onChange={vi.fn()} issues={[]} />);
    expect(vueDe(container).state.facet(EditorState.readOnly)).toBe(false);
  });
});
