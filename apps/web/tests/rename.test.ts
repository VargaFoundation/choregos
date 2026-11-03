import { describe, expect, it } from "vitest";
import { rename } from "@/components/workflows/rename";

describe("a new workflow, from a template", () => {
  it("takes its name from `metadata.name`, in flow as in block style, without touching the rest", () => {
    expect(rename("metadata: { name: default-simple, version: 1 }\nstates: {}\n", "offboarding")).toBe(
      "metadata: { name: offboarding, version: 1 }\nstates: {}\n",
    );
    expect(rename("metadata:\n  name: default-simple\n  version: 1\n", "offboarding")).toBe(
      "metadata:\n  name: offboarding\n  version: 1\n",
    );
  });
});
