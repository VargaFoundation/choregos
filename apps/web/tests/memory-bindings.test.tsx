import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { MemoryBindingsEditor, type MemoryOptions } from "@/components/agents/memory-bindings";
import {
  effectiveBindings,
  isLegacyDefault,
  moveBinding,
  summaryOf,
  withWriter,
  writesTo,
  type Binding,
} from "@/lib/memories";
import type { MemoryStore } from "@/lib/types";

/** An agent version binds several memories (S40-11, ADR 0075): the editor, and the TypeScript twin of the core derivation. */

const OPTIONS: MemoryOptions = {
  global: [
    { slug: "security-lessons", display_name: "Security lessons", scope: "global", reach: "org", projects: [], status: "active" },
    { slug: "architecture-decisions", display_name: "Architecture decisions", scope: "global", reach: "projects", projects: ["billing-api"], status: "active" },
    { slug: "old-lessons", display_name: "Old lessons", scope: "global", reach: "org", projects: [], status: "archived" },
  ] as MemoryStore[],
  project: [
    { slug: "decisions", display_name: "Decisions", scope: "project", project: "billing-api", projects: ["billing-api"], status: "active" },
    { slug: "decisions", display_name: "Decisions", scope: "project", project: "checkout-web", projects: ["checkout-web"], status: "active" },
  ] as MemoryStore[],
};

function Harness({ start }: { start: Binding[] }) {
  const [value, setValue] = useState(start);
  return (
    <>
      <MemoryBindingsEditor value={value} onChange={setValue} options={OPTIONS} />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}
const current = (): Binding[] => JSON.parse(screen.getByTestId("value").textContent ?? "[]");
const SHARED_WRITE: Binding = { scope: "project", slug: "shared", mode: "write" };

describe("the core's derivation, in TypeScript", () => {
  // `packages/core/tests/test_memory_bindings.py` pins the same three lists on the Python side.
  it.each([
    ["project", [{ scope: "project", slug: "shared", mode: "write" }]],
    ["agent", [{ scope: "project", slug: "own", mode: "write" }]],
    ["none", []],
  ] as const)("a version with `memory: %s` means its explicit list", (memory, expected) => {
    expect(effectiveBindings({ memory })).toEqual(expected);
    expect(effectiveBindings({})).toEqual([{ scope: "project", slug: "shared", mode: "write" }]);
  });

  it("an explicit list wins over the legacy field, an empty one included", () => {
    expect(effectiveBindings({ memories: [], memory: "project" })).toEqual([]);
    expect(effectiveBindings({ memories: [{ scope: "global", slug: "x-y" }] })).toEqual([{ scope: "global", slug: "x-y", mode: "read" }]);
  });

  it("the legacy default and its explicit equivalent are the same bindings", () => {
    expect(effectiveBindings({ memory: "project" })).toEqual(effectiveBindings({ memories: [SHARED_WRITE] }));
    expect(isLegacyDefault(effectiveBindings({}))).toBe(true);
    expect(isLegacyDefault([{ ...SHARED_WRITE, mode: "read" }])).toBe(false);
  });
});

describe("the rules of the list", () => {
  it("one binding writes at a time", () => {
    const list: Binding[] = [SHARED_WRITE, { scope: "global", slug: "a-b", mode: "read" }];
    expect(withWriter(list, 1).map((b) => b.mode)).toEqual(["read", "write"]);
    expect(withWriter(list, null).map((b) => b.mode)).toEqual(["read", "read"]);
  });

  it("moving swaps neighbours and stops at the ends", () => {
    const list: Binding[] = [
      { scope: "global", slug: "one-a", mode: "read" },
      { scope: "global", slug: "two-b", mode: "read" },
    ];
    expect(moveBinding(list, 1, -1).map((b) => b.slug)).toEqual(["two-b", "one-a"]);
    expect(moveBinding(list, 0, -1)).toBe(list);
  });

  it("says where a run writes, and that a global memory only gets proposals", () => {
    expect(writesTo([SHARED_WRITE])).toBe("writes to the project's shared memory");
    expect(writesTo([{ scope: "project", slug: "own", mode: "write" }])).toBe("writes to its own memory in the project");
    expect(writesTo([{ scope: "project", slug: "decisions", mode: "write" }])).toBe("writes to the project's decisions");
    expect(writesTo([{ scope: "global", slug: "security-lessons", mode: "write" }], OPTIONS.global)).toBe(
      "proposes to Security lessons, which an administrator accepts",
    );
    expect(writesTo([])).toBe("writes nothing");
  });

  it("the folded line counts organisation and project memories", () => {
    const list: Binding[] = [
      { scope: "global", slug: "security-lessons", mode: "read" },
      { scope: "global", slug: "architecture-decisions", mode: "read" },
      { scope: "project", slug: "decisions", mode: "read" },
      SHARED_WRITE,
    ];
    expect(summaryOf(list)).toBe("2 organisation, 2 project · writes to the project's shared memory");
    expect(summaryOf([])).toBe("none · reads and writes nothing");
  });
});

describe("the editor", () => {
  it("adds a memory from its groups, never one already bound or archived", () => {
    render(<Harness start={[SHARED_WRITE]} />);
    const picker = screen.getByLabelText("Add a memory");
    const options = within(picker).getAllByRole("option").map((o) => o.textContent);
    expect(options).toContain("Its own memory in the project");
    expect(options).not.toContain("The project's shared memory");
    expect(options).toContain("decisions — exists in billing-api, checkout-web");
    expect(options).toContain("Security lessons — every project");
    expect(options).toContain("Architecture decisions — projects billing-api");
    expect(options.some((o) => o?.includes("Old lessons"))).toBe(false);
    fireEvent.change(picker, { target: { value: "global:security-lessons" } });
    fireEvent.click(screen.getByRole("button", { name: "Add it" }));
    expect(current()).toEqual([SHARED_WRITE, { scope: "global", slug: "security-lessons", mode: "read" }]);
    expect(within(screen.getByLabelText("Add a memory")).queryByRole("option", { name: /Security lessons/ })).toBeNull();
  });

  it("removes and reorders", () => {
    render(<Harness start={[SHARED_WRITE, { scope: "global", slug: "security-lessons", mode: "read" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Move global:security-lessons up" }));
    expect(current().map((b) => b.slug)).toEqual(["security-lessons", "shared"]);
    fireEvent.click(screen.getByRole("button", { name: "Remove global:security-lessons" }));
    expect(current()).toEqual([SHARED_WRITE]);
    fireEvent.click(screen.getByRole("button", { name: "Remove project:shared" }));
    expect(screen.getByTestId("memory-bindings-empty")).toHaveTextContent("None");
  });

  it("choosing write on a second row demotes the first", () => {
    render(<Harness start={[SHARED_WRITE, { scope: "global", slug: "security-lessons", mode: "read" }]} />);
    const rows = screen.getAllByTestId("memory-binding");
    fireEvent.click(within(rows[1]!).getByLabelText("Read and write"));
    expect(current().map((b) => `${b.slug}:${b.mode}`)).toEqual(["shared:read", "security-lessons:write"]);
    // And back to read: a list may write nothing.
    fireEvent.click(within(screen.getAllByTestId("memory-binding")[1]!).getByLabelText("Read"));
    expect(current().map((b) => b.mode)).toEqual(["read", "read"]);
  });

  it("stops at eight memories", () => {
    const eight: Binding[] = Array.from({ length: 8 }, (_, i) => ({ scope: "global", slug: `memo-${i}`, mode: "read" }));
    render(<Harness start={eight} />);
    expect(screen.getByLabelText("Add a memory")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add it" })).toBeDisabled();
    expect(screen.getByText("At most 8 memories.")).toBeInTheDocument();
  });
});
