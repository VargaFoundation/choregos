import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Access } from "@/components/access";
import { EditionBadge, EditionCard, ENTERPRISE_FEATURES } from "@/components/edition";
import { Guarantees } from "@/components/guarantees";
import { LiveLog } from "@/components/live-log";
import { EvidencePanel } from "@/components/evidence";
import { ActorIcon, CostChip, Disclosure, Tabs, StateBadge } from "@/components/ui";
import type { RunEventDto } from "@/lib/types";

describe("composants transverses", () => {
  it("shows the project's label, not the state identifier", () => {
    render(<StateBadge state="awaiting_spec_approval" display="Spec to approve" kind="wait" />);
    expect(screen.getByText("Spec to approve")).toBeInTheDocument();
  });

  it("marks the budget overrun", () => {
    const { container } = render(<CostChip costUsd={30} budgetUsd={25} />);
    expect(container.querySelector(".text-danger")).not.toBeNull();
  });

  it("a tab bar receives the pixel that the active tab overflows: no vertical scroll", () => {
    render(
      <Tabs aria-label="sections" className="mt-2">
        <a href="#a">a</a>
      </Tabs>,
    );
    const barre = screen.getByRole("navigation", { name: "sections" });
    expect(barre).toHaveClass("pb-px", "overflow-x-auto", "mt-2");
  });

  it("names the actor type for screen readers", () => {
    render(<ActorIcon kind="agent" name="implement" />);
    expect(screen.getByText("Agent")).toBeInTheDocument();
  });
});

describe("journal ACP", () => {
  const events: RunEventDto[] = Array.from({ length: 5000 }, (_, index) => ({
    seq: index + 1,
    type: index % 500 === 0 ? "session/request_permission" : "session/update",
    ts: new Date().toISOString(),
    payload: index % 500 === 0 ? { allowed: false, target: "src/out-of-scope.py", reason: "out of scope" } : { text: `line ${index}` },
  }));

  it("virtualises: only a few rows are mounted", () => {
    const { container } = render(<LiveLog events={events} height={300} />);
    const rows = container.querySelectorAll('[data-testid="live-log"] > div > div');
    expect(rows.length).toBeLessThan(60);
    expect(screen.getByText(/5000 events/)).toBeInTheDocument();
  });

  it("highlights refused permissions: a pill, and a rule on the row", () => {
    render(<LiveLog events={events.slice(0, 3)} />);
    const refusal = screen.getByText("Denied");
    expect(refusal).toHaveClass("rounded-full", "text-failed-ink");
    expect(refusal.closest("[data-testid='live-log'] > div > div")).toHaveClass("border-l-failed");
  });

  it("reads a journaled result as its status and summary, not as the whole result (#410)", () => {
    const result: RunEventDto = {
      seq: 1,
      type: "run.result",
      ts: new Date().toISOString(),
      payload: { status: "done", summary: "credit notes deducted", stage_result: { reports: { diff_files: "[...]" } } },
    };
    render(<LiveLog events={[result]} />);
    expect(screen.getByText("done · credit notes deducted")).toBeInTheDocument();
    expect(screen.queryByText(/diff_files/)).toBeNull();
  });
});

describe("a step's evidence", () => {
  it("shows the facts named by the trade rather than absent tests", () => {
    // A processed file has neither tests nor coverage: showing "tests: ✗ · lint: -"
    // for it was wrong, and the mistake showed on screen before showing in the contract.
    render(<EvidencePanel evidence={{ facts: { profils_retenus: 2, besoin_complet: true } }} />);
    expect(screen.getByText(/Profils retenus/)).toBeInTheDocument();
    expect(screen.getByText(/✓/)).toBeInTheDocument();
    expect(screen.queryByText(/tests/)).toBeNull();
  });

  it("keeps the software's measures when those are what exist", () => {
    render(<EvidencePanel evidence={{ tests_run: 6, tests_passed: true, lint: "ok" }} />);
    expect(screen.getByText(/Tests: ✓ 6/)).toBeInTheDocument();
    expect(screen.getByText(/Lint: ok/)).toBeInTheDocument();
  });

  it("does not show a grid of dashes when there is no evidence", () => {
    render(<EvidencePanel evidence={{}} />);
    expect(screen.getByText(/No evidence recorded/)).toBeInTheDocument();
  });
});

describe("access sheet", () => {
  it("puts the refusals first, with their reason", () => {
    // What an audit comes for: "denied" says nothing, "sensitive file" does.
    render(
      <Access
        acces={{
          events: 220,
          refusals: 1,
          access: [
            { nature: "write", target: "/workspace/.env", requests: 1, refusals: 1, reasons: ["sensitive file"] },
            { nature: "read", target: "/workspace/src/panier.py", requests: 4, refusals: 0 },
          ],
        }}
      />,
    );
    expect(screen.getAllByText(/refus/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/sensitive file/)).toBeInTheDocument();
    expect(screen.getByText(/220 events/)).toBeInTheDocument();
  });

  it("claims nothing when there is nothing", () => {
    render(<Access />);
    expect(screen.getByText(/No access recorded/)).toBeInTheDocument();
  });
});

describe("garanties", () => {
  const events: RunEventDto[] = [
    { seq: 1, type: "session/update", ts: "2026-09-24T10:00:00Z", payload: { text: "prose" } },
    {
      seq: 2,
      type: "gate.outcome",
      ts: "2026-09-24T10:01:00Z",
      payload: { name: "scope_respected", passed: true, pending: false, detail: "within scope" },
    },
    {
      seq: 3,
      type: "gate.outcome",
      ts: "2026-09-24T10:01:00Z",
      payload: { name: "evidence_present", passed: false, pending: false, detail: "no test run" },
    },
  ];

  it("shows each evaluated guarantee and its verdict, refusals first", () => {
    render(<Guarantees events={events} />);
    expect(screen.getByText("2 evaluated · 1 refused")).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items[0]?.textContent).toContain("evidence_present");
    expect(items[0]?.textContent).toContain("no test run");
    expect(screen.getByLabelText("Refused")).toBeInTheDocument();
  });

  it("says when no guarantee was evaluated, rather than an empty ✓", () => {
    render(<Guarantees events={[events[0]!]} />);
    expect(screen.getByText(/No gate evaluated/)).toBeInTheDocument();
  });
});

describe("edition (ADR 0024)", () => {
  it("in the community edition, says what the enterprise edition would add", () => {
    render(<EditionCard edition={{ edition: "community", features: [], version: "0.13.1" }} />);
    const list = screen.getByRole("list", { name: "What the enterprise edition adds" });
    expect(list.querySelectorAll("li").length).toBe(ENTERPRISE_FEATURES.length);
    expect(screen.getByText("SCIM provisioning of users and groups")).toBeInTheDocument();
  });

  it("in the enterprise edition, lists what it allows itself, and keeps an unknown key readable", () => {
    render(<EditionCard edition={{ edition: "enterprise", features: ["saml", "nouveaute"], version: "0.14.0" }} />);
    const list = screen.getByRole("list", { name: "Enterprise features" });
    expect(list.textContent).toContain("SAML sign-in, per organisation");
    expect(list.textContent).toContain("nouveaute");
  });

  it("the badge names the edition", () => {
    render(<EditionBadge edition="enterprise" />);
    expect(screen.getByTestId("edition-badge")).toHaveTextContent("Enterprise edition");
  });
});

describe("Disclosure (S39-05)", () => {
  it("is closed by default, says its value, opens on a click and honours defaultOpen", () => {
    const { container } = render(
      <>
        <Disclosure title="Skills" summary="none">
          <p>Add a skill</p>
        </Disclosure>
        <Disclosure title="Memory" summary="its own" defaultOpen>
          <p>Memory scope</p>
        </Disclosure>
      </>,
    );
    const [skills, memory] = Array.from(container.querySelectorAll("details"));
    expect(skills).not.toHaveAttribute("open");
    expect(memory).toHaveAttribute("open");
    // A native `<summary>`: the keyboard and assistive technology need no script.
    expect(skills!.querySelector("summary")).toHaveTextContent("Skillsnone");
    fireEvent.click(screen.getByText("Skills"));
    expect(skills).toHaveAttribute("open");
  });
});
