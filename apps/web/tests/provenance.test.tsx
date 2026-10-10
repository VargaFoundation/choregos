import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Preuves } from "@/components/preuves";
import { Provenance } from "@/components/provenance";
import { finDeclaree, provenanceDePreuve } from "@/lib/provenance";

describe("d'où vient une preuve (ADR 0045, S25-03)", () => {
  it("constatée si la plateforme l'a mesurée, déclarée sinon ; inconnue quand le runner ne le dit pas", () => {
    const evidence = { measured: ["tests_passed", "facts.smoke_ok"] };
    expect(provenanceDePreuve(evidence, "tests_passed")).toBe("observed");
    expect(provenanceDePreuve(evidence, "facts.smoke_ok")).toBe("observed");
    expect(provenanceDePreuve(evidence, "coverage_delta")).toBe("declared");
    expect(provenanceDePreuve({ measured: [] }, "tests_passed")).toBe("declared");
    expect(provenanceDePreuve({ measured: null }, "tests_passed")).toBe("unknown");
    expect(provenanceDePreuve(undefined, "tests_passed")).toBe("unknown");
  });

  it("un run que l'agent dit fini mais dont une garantie échoue est « déclaré fini, non constaté »", () => {
    const refusee = { passed: false, pending: false };
    const passee = { passed: true, pending: false };
    expect(finDeclaree("done", [passee, refusee])).toBe("declared done, not observed");
    expect(finDeclaree("done", [])).toBe("declared done, not verified yet");
    expect(finDeclaree("done", [passee, { passed: false, pending: true }])).toBe("declared done, not verified yet");
    expect(finDeclaree("done", [passee])).toBeNull();
    // L'agent ne se dit pas fini : rien à confronter.
    expect(finDeclaree("failed", [refusee])).toBeNull();
    expect(finDeclaree(undefined, [refusee])).toBeNull();
  });
});

describe("les preuves d'un run", () => {
  it("chaque ligne dit si la plateforme l'a constatée ou si l'agent l'a déclarée", () => {
    render(
      <Preuves
        evidence={{ tests_passed: true, tests_run: 412, coverage_delta: 1.2, lint: "ok", measured: ["lint", "tests_passed", "tests_run"] }}
      />,
    );
    const lignes = screen.getAllByRole("listitem");
    const provenance = (texte: string) =>
      lignes.find((l) => l.textContent?.startsWith(texte))!.querySelector("[data-provenance]")!.getAttribute("data-provenance");
    expect(provenance("tests")).toBe("observed");
    expect(provenance("lint")).toBe("observed");
    expect(provenance("coverage")).toBe("declared");
  });

  it("un fait métier mesuré est constaté, celui que l'agent écrit est déclaré", () => {
    render(<Preuves evidence={{ facts: { smoke_ok: true, rapport_joint: true }, measured: ["facts.smoke_ok"] }} />);
    const [smoke, rapport] = screen.getAllByRole("listitem");
    expect(within(smoke!).getByText("observed")).toBeInTheDocument();
    expect(within(rapport!).getByText("declared")).toBeInTheDocument();
  });

  it("l'étiquette se lit : un mot, et ce qu'il veut dire pour un lecteur d'écran", () => {
    render(<Provenance de="declared" />);
    expect(screen.getByText("declared").closest("[data-provenance]")).toHaveTextContent(
      "declared — said by the agent, not checked by the platform",
    );
  });
});
