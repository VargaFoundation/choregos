import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DiffView } from "@/components/diff-view";
import { viewerOf, filenameOf } from "@/lib/artifacts";
import { parseUnifiedDiff } from "@/lib/unified-diff";

const DIFF = `diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -10,3 +10,3 @@ def f():
 keep
-old <img src=x onerror=alert(1)>
+new
diff --git a/new.py b/new.py
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+x
diff --git a/old.py b/moved.py
rename from old.py
rename to moved.py
--- a/old.py
+++ b/moved.py
# no textual patch from the SCM for this file (binary, or too large)
`;

describe("a branch's diff (ADR 0129)", () => {
  it("reads files, statuses, line numbers and counters", () => {
    const [a, added, moved] = parseUnifiedDiff(DIFF);
    expect(a).toMatchObject({ path: "src/a.py", status: "modified", additions: 1, deletions: 1, note: null });
    expect(a!.hunks[0]!.lines).toEqual([
      { kind: "ctx", text: "keep", oldNumber: 10, newNumber: 10 },
      { kind: "del", text: "old <img src=x onerror=alert(1)>", oldNumber: 11, newNumber: null },
      { kind: "add", text: "new", oldNumber: null, newNumber: 11 },
    ]);
    expect(added).toMatchObject({ path: "new.py", status: "added", additions: 1 });
    expect(moved).toMatchObject({ path: "moved.py", oldPath: "old.py", status: "renamed", note: "no-patch", hunks: [] });
  });

  it("gets its own viewer and file name", () => {
    expect(viewerOf({ type: "diff", kind: "diff", media_type: "text/x-diff" })).toBe("diff");
    expect(filenameOf({ name: "diff", type: "diff" })).toBe("diff.diff");
  });

  it("draws what a patch holds as text, never as markup", () => {
    const { container } = render(<DiffView text={DIFF} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("old <img src=x onerror=alert(1)>");
    expect(screen.getByTestId("diff-summary")).toHaveTextContent("3 files+2−1");
  });

  it("marks what is out of the ticket's scope, and only that", () => {
    render(<DiffView text={DIFF} isOutOfScope={(path) => path === "new.py"} />);
    const [first, second] = screen.getAllByTestId("diff-file");
    expect(within(first!).queryByTestId("diff-out-of-scope")).toBeNull();
    expect(within(second!).getByTestId("diff-out-of-scope")).toHaveTextContent("Out of scope");
  });

  it("says when the SCM gave no text for a file", () => {
    render(<DiffView text={DIFF} />);
    expect(screen.getAllByTestId("diff-file")[2]).toHaveTextContent("binary, or too large");
  });

  it("says a patch was withheld and which shape was found, never a value", () => {
    const text = "diff --git a/k b/k\n--- a/k\n+++ b/k\n# patch withheld: a secret was detected in this file\n";
    render(<DiffView text={text} stat={{ files: 1, additions: 1, deletions: 0, truncated: false, withheld: ["AWS key"] }} />);
    expect(screen.getByTestId("diff-withheld")).toHaveTextContent("AWS key");
    expect(screen.getByTestId("diff-file")).toHaveTextContent("Withheld");
  });

  it("says when the diff was cut", () => {
    render(<DiffView text={DIFF} stat={{ files: 3, additions: 2, deletions: 1, truncated: true, withheld: [] }} />);
    expect(screen.getByTestId("diff-truncated")).toBeInTheDocument();
  });

  it("opens a small diff and leaves a large one folded", () => {
    const many = Array.from({ length: 9 }, (_, i) => `diff --git a/f${i} b/f${i}\n@@ -1 +1 @@\n+x\n`).join("");
    const { rerender } = render(<DiffView text={DIFF} />);
    expect((screen.getAllByTestId("diff-file")[0] as HTMLDetailsElement).open).toBe(true);
    rerender(<DiffView text={many} />);
    expect((screen.getAllByTestId("diff-file")[0] as HTMLDetailsElement).open).toBe(false);
  });

  it("draws a bounded number of lines per file and says the rest is in the download", () => {
    const body = Array.from({ length: 1600 }, () => "+x").join("\n");
    render(<DiffView text={`diff --git a/f b/f\n@@ -0,0 +1,1600 @@\n${body}\n`} />);
    expect(screen.getByTestId("diff-lines").querySelectorAll("[data-kind=add]")).toHaveLength(1500);
    expect(screen.getByTestId("diff-file-cut")).toBeInTheDocument();
  });

  it("reads a path that holds ' b/' from the lines that name it", () => {
    const [file] = parseUnifiedDiff("diff --git a/Plan b/notes.md b/Plan b/notes.md\n--- a/Plan b/notes.md\n+++ b/Plan b/notes.md\n@@ -1 +1 @@\n-a\n+b\n");
    expect(file).toMatchObject({ path: "Plan b/notes.md", oldPath: "Plan b/notes.md", additions: 1, deletions: 1 });
  });

  it("keeps a removed file's name", () => {
    const [file] = parseUnifiedDiff("diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n");
    expect(file).toMatchObject({ path: "gone.py", status: "removed" });
  });

  it("shows the counters of a file whose patch is not kept", () => {
    const text = "diff --git a/k b/k\n--- a/k\n+++ b/k\n# patch withheld: a secret was detected in this file\n# counts: +5 -2\n";
    const [file] = parseUnifiedDiff(text);
    expect(file).toMatchObject({ additions: 5, deletions: 2, note: "withheld", hunks: [] });
  });

  it("says nothing is missing from a pure rename", () => {
    const [file] = parseUnifiedDiff("diff --git a/o.py b/n.py\nrename from o.py\nrename to n.py\n--- a/o.py\n+++ b/n.py\n");
    expect(file).toMatchObject({ path: "n.py", oldPath: "o.py", status: "renamed", note: null });
  });
});
