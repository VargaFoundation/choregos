import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Glossaire } from "@/components/glossaire";

describe("le glossaire des quatre notions (S21-03)", () => {
  it("nomme agent, skill, connector et client, et renvoie à leur page", () => {
    render(<Glossaire ici="agents" ouvert />);
    const glossaire = screen.getByTestId("glossaire");
    expect(within(glossaire).getByRole("link", { name: "Skill" })).toHaveAttribute("href", "/skills");
    expect(within(glossaire).getByRole("link", { name: "Connector" })).toHaveAttribute("href", "/admin/connectors");
    expect(within(glossaire).getByRole("link", { name: "Client" })).toHaveAttribute("href", "/integrations");
  });

  it("la notion de la page courante n'est pas un lien et porte aria-current", () => {
    render(<Glossaire ici="connectors" />);
    const glossaire = screen.getByTestId("glossaire");
    expect(within(glossaire).queryByRole("link", { name: "Connector" })).toBeNull();
    expect(within(glossaire).getByText("Connector")).toHaveAttribute("aria-current", "page");
  });

  it("dit qu'un agent appelé en MCP chez un fournisseur est un connecteur", () => {
    render(<Glossaire ici="clients" />);
    expect(screen.getByTestId("glossaire")).toHaveTextContent(/supplier's agent that you call over MCP is a connector/);
  });

  it("dit le chemin d'une demande, du client à la personne qui approuve", () => {
    render(<Glossaire ici="skills" />);
    const etapes = within(screen.getByRole("list", { name: "how a request flows" })).getAllByRole("listitem");
    expect(etapes.filter((li) => li.textContent !== "→").map((li) => li.textContent)).toEqual([
      "a client or a tracker opens a work item",
      "its workflow hands each step to an agent",
      "the agent calls connectors under the policy",
      "a person approves what the policy asks",
    ]);
  });
});
