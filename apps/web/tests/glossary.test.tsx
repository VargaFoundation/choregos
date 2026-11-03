import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Glossary } from "@/components/glossary";

describe("the glossary of the four notions (S21-03)", () => {
  it("names agent, skill, connector and client, and points to their page", () => {
    render(<Glossary ici="agents" ouvert />);
    const glossaire = screen.getByTestId("glossaire");
    expect(within(glossaire).getByRole("link", { name: "Skill" })).toHaveAttribute("href", "/skills");
    expect(within(glossaire).getByRole("link", { name: "Connector" })).toHaveAttribute("href", "/admin/connectors");
    expect(within(glossaire).getByRole("link", { name: "Client" })).toHaveAttribute("href", "/integrations");
  });

  it("the notion of the current page is not a link and carries aria-current", () => {
    render(<Glossary ici="connectors" />);
    const glossaire = screen.getByTestId("glossaire");
    expect(within(glossaire).queryByRole("link", { name: "Connector" })).toBeNull();
    expect(within(glossaire).getByText("Connector")).toHaveAttribute("aria-current", "page");
  });

  it("says that an agent called over MCP at a supplier is a connector", () => {
    render(<Glossary ici="clients" />);
    expect(screen.getByTestId("glossaire")).toHaveTextContent(/supplier's agent that you call over MCP is a connector/);
  });

  it("says a request's path, from the client to the person who approves", () => {
    render(<Glossary ici="skills" />);
    const steps = within(screen.getByRole("list", { name: "How a request flows" })).getAllByRole("listitem");
    expect(steps.filter((li) => li.textContent !== "→").map((li) => li.textContent)).toEqual([
      "A client or a tracker opens a work item",
      "Its workflow hands each step to an agent",
      "The agent calls connectors under the policy",
      "A person approves what the policy asks",
    ]);
  });

  it("does not contradict the registry: a registered client is an external agent (S23-14)", () => {
    render(<Glossary ici="agents" ouvert />);
    const glossaire = screen.getByTestId("glossaire");
    expect(glossaire).toHaveTextContent(/an external agent is an AI client registered here/);
    expect(glossaire).toHaveTextContent(/fewer once registered as an external agent/);
    expect(glossaire).not.toHaveTextContent(/MCP gate/);
  });
});
