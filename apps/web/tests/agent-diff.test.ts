// @vitest-environment node
import { describe, expect, it } from "vitest";
import { diffSpecs, lineDiff, MAX_DIFF_LINES } from "@/lib/agent-diff";
import { parseCompare } from "@/lib/agent-versions";
import type { AgentSpec } from "@/lib/types";

/** The structured diff of two specs (S40-12): field-aware, and blind to null, absent and default. */
const base: AgentSpec = {
  instructions: "Write the ADR.\nCite the framework.",
  model: "profile:standard",
  skills: [{ slug: "madr-4", version: 1 }],
  mcp_servers: [{ connector: "github", tools: ["get_*", "search_*"] }],
  evals: [{ name: "has title", must_contain: ["#"] }],
};

describe("diffSpecs", () => {
  it("sees no change between null, absent and default values", () => {
    const explicit: AgentSpec = {
      instructions: "",
      model: null,
      backend: null,
      limits: { max_turns: null, max_minutes: null },
      budget: { run_usd: null, daily_usd: null, monthly_usd: null },
      skills: [],
      mcp_servers: [],
      knowledge: [],
      evals: [],
      memory: "project",
    };
    expect(diffSpecs({}, explicit)).toEqual([]);
    expect(diffSpecs(undefined, null)).toEqual([]);
    expect(diffSpecs(base, structuredClone(base))).toEqual([]);
  });

  it("marks the added and removed lines of the instructions", () => {
    const [change] = diffSpecs(base, { ...base, instructions: "Write the ADR.\nCite the framework.\nEnd with the status." });
    expect(change).toMatchObject({ group: "instructions", kind: "changed" });
    expect(change?.lines?.filter((l) => l.kind === "added").map((l) => l.text)).toEqual(["End with the status."]);
    const [other] = diffSpecs(base, { ...base, instructions: "Write the ADR." });
    expect(other?.lines?.filter((l) => l.kind === "removed").map((l) => l.text)).toEqual(["Cite the framework."]);
  });

  it("names a skill by slug: a version bump is one change, a new skill an addition", () => {
    const changes = diffSpecs(base, { ...base, skills: [{ slug: "madr-4", version: 2 }, { slug: "extra" }] });
    expect(changes.map((c) => [c.label, c.kind, c.before, c.after])).toEqual([
      ["Skill madr-4", "changed", "version 1", "version 2"],
      ["Skill extra", "added", undefined, "latest version"],
    ]);
  });

  it("shows the tool patterns added and removed on a connector", () => {
    const [change] = diffSpecs(base, { ...base, mcp_servers: [{ connector: "github", tools: ["search_*", "list_*"] }] });
    expect(change).toMatchObject({ label: "Tools of github", kind: "changed" });
    expect(change?.detail).toBe("+list_*  −get_*");
  });

  it("treats a renamed eval case as one removed and one added", () => {
    const changes = diffSpecs(base, { ...base, evals: [{ name: "has heading", must_contain: ["#"] }] });
    expect(changes.map((c) => [c.label, c.kind])).toEqual([
      ["Case has title", "removed"],
      ["Case has heading", "added"],
    ]);
  });

  it("reads scalars, limits and memory", () => {
    const changes = diffSpecs(base, { ...base, model: "profile:strong", limits: { max_turns: 30 }, budget: { run_usd: 2 }, memory: "agent" });
    // The legacy `memory` is read as the bindings it means (S40-11): the shared memory leaves, the agent's own comes.
    expect(changes.map((c) => [c.field, c.kind])).toEqual([
      ["model", "changed"],
      ["limits.max_turns", "added"],
      ["budget.run_usd", "added"],
      ["memories", "removed"],
      ["memories", "added"],
    ]);
    expect(changes[2]).toMatchObject({ after: "$2", before: "none" });
  });

  it("sees no change between the legacy memory and the bindings it means", () => {
    expect(diffSpecs({ ...base, memory: "project" }, { ...base, memories: [{ scope: "project", slug: "shared", mode: "write" }] })).toEqual([]);
    expect(diffSpecs({ ...base, memory: "none" }, { ...base, memories: [] })).toEqual([]);
  });

  it("diffs the memories a version binds: one added, a mode that moved, a new order", () => {
    const before: AgentSpec = {
      ...base,
      memories: [
        { scope: "global", slug: "lessons", mode: "read" },
        { scope: "project", slug: "shared", mode: "write" },
      ],
    };
    const added = diffSpecs(before, { ...before, memories: [...before.memories!, { scope: "project", slug: "decisions", mode: "read" }] });
    expect(added.map((c) => [c.label, c.kind, c.after])).toEqual([["Memory decisions (project)", "added", "read"]]);
    const moved = diffSpecs(before, {
      ...before,
      memories: [
        { scope: "global", slug: "lessons", mode: "write" },
        { scope: "project", slug: "shared", mode: "read" },
      ],
    });
    expect(moved.map((c) => [c.label, c.kind, c.before, c.after])).toEqual([
      ["Memory lessons (organisation)", "changed", "read", "read and written"],
      ["Memory The project's shared memory", "changed", "read and written", "read"],
    ]);
    const reordered = diffSpecs(before, { ...before, memories: [...before.memories!].reverse() });
    expect(reordered).toHaveLength(1);
    expect(reordered[0]).toMatchObject({ label: "Memory order", kind: "changed", after: "project:shared, global:lessons" });
  });

  it("shows a field it does not know as JSON before and after", () => {
    const withRuntime = { ...base, runtime: { image: "ghcr.io/x/agent:1" } } as AgentSpec;
    const [added] = diffSpecs(base, withRuntime);
    expect(added).toMatchObject({ group: "other", field: "runtime", kind: "added", after: '{"image":"ghcr.io/x/agent:1"}' });
    const [changed] = diffSpecs(withRuntime, { ...base, runtime: { image: "ghcr.io/x/agent:2" } } as AgentSpec);
    expect(changed).toMatchObject({ kind: "changed", before: '{"image":"ghcr.io/x/agent:1"}', after: '{"image":"ghcr.io/x/agent:2"}' });
  });

  it("does not compare instructions of more than 2,000 lines line by line", () => {
    const long = Array.from({ length: MAX_DIFF_LINES + 1 }, (_, n) => `line ${n}`).join("\n");
    const [change] = diffSpecs({ instructions: long }, { instructions: `${long}\nmore` });
    expect(change).toMatchObject({ tooLong: true, kind: "changed" });
    expect(change?.lines).toBeUndefined();
    expect(change?.after?.endsWith("more")).toBe(true);
  });
});

describe("lineDiff and parseCompare", () => {
  it("keeps the lines both sides share and orders removals before additions", () => {
    expect(lineDiff("a\nb\nc", "a\nx\nc")).toEqual([
      { kind: "same", text: "a" },
      { kind: "removed", text: "b" },
      { kind: "added", text: "x" },
      { kind: "same", text: "c" },
    ]);
    expect(lineDiff("", "a")).toEqual([{ kind: "added", text: "a" }]);
  });

  it("reads ?compare=1..3 only when it is two different numbers", () => {
    expect(parseCompare("1..3")).toEqual([1, 3]);
    expect(parseCompare("3..3")).toBeNull();
    expect(parseCompare("a..b")).toBeNull();
    expect(parseCompare(null)).toBeNull();
  });
});
