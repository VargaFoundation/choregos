import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConnectorCard } from "@/components/project-settings/connector-card";
import { RunIdentity, TrackerCard, WritesAs } from "@/components/who-writes";
import type { ConnectorDto, Run } from "@/lib/types";
import { initiatorInWords, viaInWords } from "@/lib/who-writes";
import { runs } from "@/mocks/data";

const jira: ConnectorDto = {
  id: "c-jira",
  kind: "tracker",
  type: "jira",
  config: { base_url: "https://acme.atlassian.net", email: "bot@acme.example", project_key: "XXX" },
  secret_refs: {},
  status: "ok",
};

describe("who writes, and with which account (S42-06g, ADR 0107)", () => {
  it("a tracker connector's card says the account its writes go out under", () => {
    render(
      <ul>
        <ConnectorCard
          sorte="tracker"
          actuel={jira}
          types={[]}
          onSave={async () => {}}
          onSecret={async () => {}}
          onClearSecret={async () => {}}
          onTest={async () => {}}
        />
      </ul>,
    );
    expect(screen.getByTestId("writes-as")).toHaveTextContent("Choregos writes as bot@acme.example");
  });

  it("names the credential when the configuration names no account, and nothing for the internal tracker", () => {
    const { rerender, container } = render(<WritesAs type="gitlab-issues" config={{}} />);
    expect(screen.getByTestId("writes-as")).toHaveTextContent("the account that owns the connector's GitLab token");
    rerender(<WritesAs type="github-issues" config={{ repo: "acme/app" }} />);
    expect(screen.getByTestId("writes-as")).toHaveTextContent("the Choregos GitHub App");
    rerender(<WritesAs type="internal" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("a work item says where it lives, who started it, and who writes to it", () => {
    render(
      <TrackerCard
        slug="yyy"
        tracker={jira}
        initiatedBy="tracker:jira"
        url="https://acme.atlassian.net/browse/XXX-12"
      />,
    );
    const card = screen.getByTestId("tracker-card");
    expect(within(card).getByRole("link", { name: "Open in Jira" })).toHaveAttribute(
      "href",
      "https://acme.atlassian.net/browse/XXX-12",
    );
    expect(card).toHaveTextContent("Started by a Jira webhook");
    expect(card).toHaveTextContent("Choregos writes to Jira as bot@acme.example");
    expect(within(card).getByRole("link", { name: "tracker connector" })).toHaveAttribute(
      "href",
      "/p/yyy/settings#connections",
    );
  });

  it("a work item on the internal tracker says nothing is written elsewhere", () => {
    render(<TrackerCard slug="yyy" initiatedBy="person:vincent@acme.example" />);
    const card = screen.getByTestId("tracker-card");
    expect(card).toHaveTextContent("Started by vincent@acme.example");
    expect(card).toHaveTextContent("Choregos writes to no external tracker");
  });

  it("a timeline entry names its tracker write", () => {
    expect(viaInWords({ type: "jira", account: "bot@acme.example" })).toBe("via Jira as bot@acme.example");
    expect(viaInWords({ type: "github-issues", account: null })).toBe("via GitHub Issues as the Choregos GitHub App");
  });

  it("an initiator reads in words", () => {
    expect(initiatorInWords("mcp:vincent@acme.example")).toBe("vincent@acme.example, through MCP");
    expect(initiatorInWords("schedule:nightly")).toBe("the schedule nightly");
    expect(initiatorInWords(null)).toBeNull();
  });

  it("a run names its agent, who started it, the chain and the tools its agent may call, linked", () => {
    const run: Run = {
      ...runs[0]!,
      tool_connectors: [
        { name: "atlassian", type: "mcp", auth_scheme: "basic", account: "svc-choregos@acme.example" },
        { name: "supplier", type: "mcp", auth_scheme: "bearer", account: null },
      ],
    };
    const chain = {
      initiated_by: "tracker:jira",
      runs: [
        { run_id: "r0", stage: "triage", inputs_from: [] },
        { run_id: "r1", stage: "refine", inputs_from: ["r0"] },
      ],
    };
    render(<RunIdentity run={run} chain={chain} />);
    const card = screen.getByTestId("run-identity");
    expect(card).toHaveTextContent("developer v3");
    expect(card).toHaveTextContent("Started bya Jira webhook");
    expect(screen.getByTestId("run-chain")).toHaveTextContent("triage → refine");
    const atlassian = screen.getByTestId("tool-connector-atlassian");
    expect(within(atlassian).getByRole("link", { name: "atlassian" })).toHaveAttribute(
      "href",
      "/admin/connectors?connector=atlassian",
    );
    expect(atlassian).toHaveTextContent("as svc-choregos@acme.example");
    expect(screen.getByTestId("tool-connector-supplier")).toHaveTextContent("as the connector's key");
  });

  it("a run whose agent may call nothing says so", () => {
    render(<RunIdentity run={{ ...runs[0]!, tool_connectors: [] }} />);
    expect(screen.getByTestId("run-identity")).toHaveTextContent("None: its version names no organisation connector");
  });
});
