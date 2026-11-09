// SPDX-License-Identifier: Apache-2.0
/**
 * A unified diff, read line by line (ADR 0129): the text the platform recorded from the SCM. Parsed
 * into data and drawn as text nodes; nothing in a patch is ever interpreted.
 */

export type DiffLine = { kind: "add" | "del" | "ctx"; text: string; oldNumber: number | null; newNumber: number | null };
export type DiffHunk = { header: string; lines: DiffLine[] };
export type DiffFileNote = "no-patch" | "cut" | "withheld" | null;
export type ParsedDiffFile = {
  path: string;
  oldPath: string;
  status: "added" | "removed" | "renamed" | "modified";
  additions: number;
  deletions: number;
  hunks: DiffHunk[];
  note: DiffFileNote;
};

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;
/** Only says a file begins: a path may hold " b/", so its name is read from the lines that follow. */
const HEADER = /^diff --git a\/(.+) b\/(.+)$/;
const COUNTS = /^# counts: \+(\d+) -(\d+)$/;

function noteOf(line: string): DiffFileNote {
  if (line.startsWith("# no textual patch")) return "no-patch";
  if (line.startsWith("# patch cut")) return "cut";
  if (line.startsWith("# patch withheld")) return "withheld";
  return null;
}

export function parseUnifiedDiff(text: string): ParsedDiffFile[] {
  const files: ParsedDiffFile[] = [];
  let file: ParsedDiffFile | null = null;
  let hunk: DiffHunk | null = null;
  let oldNumber = 0;
  let newNumber = 0;
  for (const line of text.split("\n")) {
    const header = HEADER.exec(line);
    if (header) {
      file = { path: header[2]!, oldPath: header[1]!, status: "modified", additions: 0, deletions: 0, hunks: [], note: null };
      files.push(file);
      hunk = null;
      continue;
    }
    if (!file) continue;
    const opening = HUNK.exec(line);
    if (opening) {
      oldNumber = Number(opening[1]);
      newNumber = Number(opening[2]);
      hunk = { header: line, lines: [] };
      file.hunks.push(hunk);
      continue;
    }
    if (!hunk) {
      const counts = COUNTS.exec(line);
      if (line.startsWith("rename from ")) {
        file.status = "renamed";
        file.oldPath = line.slice("rename from ".length);
      } else if (line.startsWith("rename to ")) {
        file.path = line.slice("rename to ".length);
      } else if (line === "--- /dev/null") {
        file.status = "added";
      } else if (line === "+++ /dev/null") {
        file.status = "removed";
        file.path = file.oldPath;
      } else if (line.startsWith("--- a/")) {
        file.oldPath = line.slice("--- a/".length);
        if (file.status !== "renamed") file.path = file.oldPath;
      } else if (line.startsWith("+++ b/")) {
        file.path = line.slice("+++ b/".length);
        if (file.status === "added") file.oldPath = file.path;
      } else if (counts) {
        file.additions = Number(counts[1]);
        file.deletions = Number(counts[2]);
      } else {
        file.note = noteOf(line) ?? file.note;
      }
      continue;
    }
    if (line.startsWith("+")) {
      hunk.lines.push({ kind: "add", text: line.slice(1), oldNumber: null, newNumber: newNumber++ });
      file.additions += 1;
    } else if (line.startsWith("-")) {
      hunk.lines.push({ kind: "del", text: line.slice(1), oldNumber: oldNumber++, newNumber: null });
      file.deletions += 1;
    } else if (line.startsWith(" ")) {
      hunk.lines.push({ kind: "ctx", text: line.slice(1), oldNumber: oldNumber++, newNumber: newNumber++ });
    }
  }
  return files;
}
