import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  GroupMembersLine,
  openItemsByState,
  stepActivitySentence,
  waitInWords,
  waitingSentence,
} from "@/components/workflows/step-activity";
import { actorLabel } from "@/components/workflows/panneaux";
import { timingSentence } from "@/components/workflows/timing";
import { api } from "@/lib/api";
import type { WorkflowActivity } from "@/lib/types";

/**
 * The map's side panels after the review of 2026-10-28: each sentence comes from the interpreter's
 * own rules or from the API, and says what the bare ids, the empty boxes and the two delays did not.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, groupMembers: vi.fn() } };
});

describe("how long a step waits", () => {
  it("says the reminder, the timeout and where a person step goes without a decision", () => {
    expect(timingSentence({ actorType: "human", slaHours: 24, timeoutHours: 72, onReject: "To triage" })).toBe(
      "The group is reminded after 24 h. With no decision after 72 h, the item goes back to “To triage” as if rejected.",
    );
  });

  it("names the defaults when the workflow sets none: 24 h, then three times the SLA", () => {
    expect(timingSentence({ actorType: "human", from: "Spec to approve" })).toBe(
      "The group is reminded after 24 h (no SLA set: 24 h). With no decision after 72 h (no limit set: 3 × the SLA), the item stays in “Spec to approve” for a person to pick up.",
    );
    expect(timingSentence({ actorType: "human", slaHours: 8, onTimeout: "Needs a human" })).toContain("goes to “Needs a human”");
  });

  it("says how long the CI or a review is waited for, and nothing when nothing waits", () => {
    expect(timingSentence({ actorType: "system", asyncGates: ["ci_green", "review_approved"] })).toBe(
      "It waits for ci green, review approved up to 24 h (no limit set).",
    );
    expect(timingSentence({ actorType: "agent" })).toBeNull();
  });
});

describe("who moves a transition", () => {
  it("says what each actor is, not only its id", () => {
    expect(actorLabel("owner", { type: "human", group: "product-owners" })).toBe("owner — person · product-owners");
    expect(actorLabel("dev", { type: "agent", role: "implement" })).toBe("dev — built-in agent · implement");
    expect(actorLabel("dev", { type: "agent", role: "implement", agent: "developer" })).toBe("dev — agent developer · implement");
    expect(actorLabel("platform", { type: "system" })).toBe("platform — the platform");
  });
});

describe("the workflow's activity", () => {
  const activity: WorkflowActivity = {
    workflow: "default-simple",
    days: 30,
    states: [{ state: "ready", open_items: 2 }],
    steps: [{ transition: "t-implement", runs: 4, succeeded: 3, failed: 1, cost_usd: 6.4, median_minutes: 18 }],
  };

  it("counts the open items by state and sums up a step's runs", () => {
    expect(openItemsByState(activity).get("ready")).toBe(2);
    expect(stepActivitySentence(activity, "t-implement")).toBe(
      "Last 30 days: 4 runs · 3 succeeded · 1 failed · US$6.40 · median 18 min.",
    );
    expect(stepActivitySentence(activity, "t-approve-spec")).toBe("No run in the last 30 days.");
    expect(stepActivitySentence(undefined, "t-implement")).toBeNull();
  });
});

describe("how long work items wait for a step", () => {
  const now = Date.parse("2026-10-29T12:00:00Z");
  const activity: WorkflowActivity = {
    workflow: "default-simple",
    days: 30,
    states: [{ state: "spec_review", open_items: 2, waiting_since: "2026-10-26T12:00:00Z" }],
    steps: [],
  };

  it("counts the items in the state the step leaves and says the longest wait since it entered", () => {
    expect(waitingSentence(activity, "spec_review", "Spec to approve", now)).toBe(
      "2 work items wait in “Spec to approve”, the longest for 3 d.",
    );
    expect(waitingSentence(activity, "ready", "Ready", now)).toBeNull();
  });

  it("speaks in minutes, hours, then days", () => {
    expect(waitInWords("2026-10-29T11:20:00Z", now)).toBe("40 min");
    expect(waitInWords("2026-10-29T07:00:00Z", now)).toBe("5 h");
    expect(waitInWords("2026-10-26T12:00:00Z", now)).toBe("3 d");
  });
});

describe("who a person step waits for", () => {
  function renderLine() {
    return render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <GroupMembersLine projectSlug="acme" group="product-owners" />
      </QueryClientProvider>,
    );
  }

  it("lists the group and strikes those whose role here cannot answer", async () => {
    vi.mocked(api.groupMembers).mockResolvedValue({
      group: "product-owners",
      members: [
        { email: "lea@acme.dev", display_name: "Léa", can_decide: true },
        { email: "max@acme.dev", display_name: "Max", can_decide: false },
      ],
    });
    renderLine();
    const line = await screen.findByTestId("group-members");
    expect(within(line).getByText("Max")).toHaveClass("line-through");
    expect(line).toHaveTextContent("struck through: in the group, but their role here cannot answer");
  });

  it("says plainly when nobody carries the group", async () => {
    vi.mocked(api.groupMembers).mockResolvedValue({ group: "product-owners", members: [] });
    renderLine();
    expect(await screen.findByTestId("group-members")).toHaveTextContent("waits for an organisation administrator");
  });
});
