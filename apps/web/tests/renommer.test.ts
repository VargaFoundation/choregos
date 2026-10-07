import { describe, expect, it } from "vitest";
import { renommer } from "@/components/workflows/renommer";

describe("un workflow neuf, depuis un gabarit", () => {
  it("prend son nom dans `metadata.name`, en flow comme en bloc, sans toucher au reste", () => {
    expect(renommer("metadata: { name: default-simple, version: 1 }\nstates: {}\n", "offboarding")).toBe(
      "metadata: { name: offboarding, version: 1 }\nstates: {}\n",
    );
    expect(renommer("metadata:\n  name: default-simple\n  version: 1\n", "offboarding")).toBe(
      "metadata:\n  name: offboarding\n  version: 1\n",
    );
  });
});
