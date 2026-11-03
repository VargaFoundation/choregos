import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { courte, lecture, editedAfterTheFact, Passages } from "@/components/passages";
import type { HandOff, HandOffEvent } from "@/lib/types";

const A = `sha256:${"a".repeat(64)}`;
const B = `sha256:${"b".repeat(64)}`;
const C = `sha256:${"c".repeat(64)}`;
const ev = (kind: "produced" | "read", digest: string, run_id: string, stage: string): HandOffEvent => ({
  kind,
  digest,
  run_id,
  stage,
  attempt: 1,
  at: "2026-10-10T14:00:00Z",
});

describe("the hand-off receipts, read (S25-04)", () => {
    it("a read read the revision produced just before it, another one, or one that no step produced", () => {
    const passage: HandOff = {
      output: "spec_markdown",
      current_digest: B,
      events: [
        ev("produced", A, "r1", "refine"),
        ev("read", A, "r2", "implement"),
        ev("produced", B, "r3", "refine"),
        ev("read", A, "r4", "review"),
        ev("read", C, "r5", "review"),
      ],
    };
    expect([1, 3, 4].map((rang) => lecture(passage, rang))).toEqual(["same", "other", "unknown"]);
  });

    it("an output edited after its last production shows; a removed one too", () => {
    const base: HandOff = { output: "spec_markdown", current_digest: A, events: [ev("produced", A, "r1", "refine")] };
    expect(editedAfterTheFact(base)).toBe(false);
    expect(editedAfterTheFact({ ...base, current_digest: B })).toBe(true);
    expect(editedAfterTheFact({ ...base, current_digest: null })).toBe(true);
        // Nothing produced (a read only): nothing to compare.
    expect(editedAfterTheFact({ output: "x", current_digest: A, events: [ev("read", A, "r1", "plan")] })).toBe(false);
    expect(courte(A)).toBe("aaaaaaaaaaaa");
  });

    it("the card names each output, says who produced and read it, and shows an after-the-fact edit", () => {
    render(
      <Passages
        passages={[
          { output: "spec_markdown", current_digest: C, events: [ev("produced", A, "r1", "refine"), ev("read", A, "r2", "implement")] },
          { output: "profils", current_digest: B, events: [ev("produced", B, "r1", "sourcing")] },
        ]}
      />,
    );
    const [spec, profils] = [...screen.getByRole("list", { name: "Hand-offs" }).querySelectorAll<HTMLElement>(":scope > li")];
    expect(spec).toHaveTextContent("Specification");
    expect(within(spec!).getByTestId("modifiee-apres-coup")).toHaveTextContent("Modified after it was produced");
    expect(within(spec!).getByText("The revision produced")).toBeInTheDocument();
    expect(spec).toHaveTextContent("Produced by refine · attempt 1");
    expect(profils).toHaveTextContent("profils");
    expect(within(profils!).queryByTestId("modifiee-apres-coup")).not.toBeInTheDocument();
  });
});
