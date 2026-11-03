import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvidencePanel } from "@/components/evidence";
import { Provenance } from "@/components/provenance";
import { finDeclaree, provenanceDePreuve } from "@/lib/provenance";

describe("where evidence comes from (ADR 0045, S25-03)", () => {
  it("observed if the platform measured it, declared otherwise; unknown when the runner does not say", () => {
    const evidence = { measured: ["tests_passed", "facts.smoke_ok"] };
    expect(provenanceDePreuve(evidence, "tests_passed")).toBe("observed");
    expect(provenanceDePreuve(evidence, "facts.smoke_ok")).toBe("observed");
    expect(provenanceDePreuve(evidence, "coverage_delta")).toBe("declared");
    expect(provenanceDePreuve({ measured: [] }, "tests_passed")).toBe("declared");
    expect(provenanceDePreuve({ measured: null }, "tests_passed")).toBe("unknown");
    expect(provenanceDePreuve(undefined, "tests_passed")).toBe("unknown");
  });

  it("a run the agent says is done but with a failing guarantee is \"declared done, not observed\"", () => {
    const refused = { passed: false, pending: false };
    const passee = { passed: true, pending: false };
    expect(finDeclaree("done", [passee, refused])).toBe("declared done, not observed");
    expect(finDeclaree("done", [])).toBe("declared done, not verified yet");
    expect(finDeclaree("done", [passee, { passed: false, pending: true }])).toBe("declared done, not verified yet");
    expect(finDeclaree("done", [passee])).toBeNull();
    // The agent does not say it is done: nothing to confront.
    expect(finDeclaree("failed", [refused])).toBeNull();
    expect(finDeclaree(undefined, [refused])).toBeNull();
  });
});

describe("a run's evidence", () => {
  it("each line says whether the platform observed it or the agent declared it", () => {
    render(
      <EvidencePanel
        evidence={{ tests_passed: true, tests_run: 412, coverage_delta: 1.2, lint: "ok", measured: ["lint", "tests_passed", "tests_run"] }}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    const provenance = (texte: string) =>
      rows.find((l) => l.textContent?.startsWith(texte))!.querySelector("[data-provenance]")!.getAttribute("data-provenance");
    expect(provenance("Tests")).toBe("observed");
    expect(provenance("Lint")).toBe("observed");
    expect(provenance("Coverage")).toBe("declared");
  });

  it("a measured business fact is observed, the one the agent writes is declared", () => {
    render(<EvidencePanel evidence={{ facts: { smoke_ok: true, rapport_joint: true }, measured: ["facts.smoke_ok"] }} />);
    const [smoke, rapport] = screen.getAllByRole("listitem");
    expect(within(smoke!).getByText("Observed")).toBeInTheDocument();
    expect(within(rapport!).getByText("Declared")).toBeInTheDocument();
  });

  it("the label reads well: one word, and what it means for a screen reader", () => {
    render(<Provenance de="declared" />);
    expect(screen.getByText("Declared").closest("[data-provenance]")).toHaveTextContent(
      "Declared — said by the agent, not checked by the platform",
    );
  });
});
