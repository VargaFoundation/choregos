import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NextStepBanner } from "@/components/ticket/next-step-banner";
import { FOLD_AFTER, LongText, RequestCard } from "@/components/ticket/request-card";
import { fieldTitles } from "@/components/workflows/fields";
import { nextStep } from "@/lib/next-step";
import type { JourneyStep, WorkItemDto, WorkItemJourney } from "@/lib/types";

/**
 * A ticket says first what it waits for (S55-05): who is expected, by when, and the gesture that moves
 * it on. Its page showed a title, a badge and a journey map, and left the reader to work it out.
 */
const item = (fields: Partial<WorkItemDto> = {}) =>
  ({
    id: "w2",
    project_slug: "fon",
    tracker_key: "FON-270",
    title: "Arrival on recrutauto",
    state: "awaiting_decision",
    state_display: "Access change to apply",
    created_at: "2026-11-02T09:00:00Z",
    totals: {},
    ...fields,
  }) as WorkItemDto;

const journey = (fields: Partial<WorkItemJourney> = {}) =>
  ({ work_item_id: "w2", state: "awaiting_decision", closed: false, graph: { nodes: [], edges: [] }, process: [], moves: [], steps: [], ...fields }) as WorkItemJourney;

const step = (fields: Partial<JourneyStep>) => ({ id: "s", kind: "agent", attempt: 1, status: "running", cost_usd: 0, evidence: {}, ...fields }) as JourneyStep;

describe("nextStep", () => {
  it("names the decision, who is expected and by when, and leads to it", () => {
    const next = nextStep(
      item({
        pending_request: {
          id: "hr1",
          kind: "approval",
          payload: { title: "the access change" },
          requested_at: "2026-11-02T09:00:00Z",
          due_at: "2026-11-04T09:00:00Z",
        },
      }),
      journey({ steps: [step({ kind: "human", status: "waiting", actor: "fondation-admins" })] }),
    );
    expect(next).toMatchObject({
      tone: "warn",
      title: "Approve or send back the access change",
      detail: "A person is expected · expected from fondation-admins.",
      dueAt: "2026-11-04T09:00:00Z",
      action: { kind: "decide", label: "Decide" },
    });
  });

  it("says a task is a task and a question a question", () => {
    const task = nextStep(item({ pending_request: { id: "t", kind: "task", payload: { title: "badge" }, requested_at: "x" } }));
    expect(task.title).toBe("Complete the task: badge");
    expect(task.action).toEqual({ kind: "decide", label: "Do the task" });
    const question = nextStep(item({ pending_request: { id: "q", kind: "question", payload: {}, requested_at: "x" } }));
    expect(question.title).toBe("Answer the question about this ticket");
  });

  it("puts an error before anything else", () => {
    const next = nextStep(
      item({ failure: { activity: "run_stage", message: "boom" } as WorkItemDto["failure"], pending_request: { id: "a", kind: "approval", requested_at: "x" } }),
    );
    expect(next.tone).toBe("danger");
    expect(next.title).toBe("Stopped by an error");
    expect(next.detail).toContain("run_stage");
  });

  it("offers to replay a stage that escalated", () => {
    const next = nextStep(item(), journey({ moves: [{ at: "x", to: "needs_human", kind: "escalate", reason: "3 failed attempts" }] }));
    expect(next).toMatchObject({ tone: "danger", action: { kind: "replay" } });
    expect(next.detail).toContain("3 failed attempts");
  });

  it("says an agent is working and that nothing is expected from the reader", () => {
    const next = nextStep(item(), journey({ steps: [step({ role: "implement", attempt: 2 })] }));
    expect(next).toMatchObject({ tone: "info", title: "An agent is working on it" });
    expect(next.detail).toBe("Nothing is expected from you for now (implement, attempt 2).");
  });

  it("says a paused ticket is paused, and a closed one done or closed", () => {
    expect(nextStep(item({ paused: true })).action).toEqual({ kind: "resume" });
    expect(nextStep(item({ closed_at: "y", state_display: "Applied" })).title).toBe("Done");
    expect(nextStep(item({ closed_at: "y", state_outcome: "declined" })).title).toBe("Closed");
  });
});

describe("NextStepBanner", () => {
  it("reads as a labelled region, in the colour of what it means, with its gesture", () => {
    render(
      <NextStepBanner
        step={{ tone: "warn", title: "Approve or send back the spec", detail: "A person is expected." }}
        action={<a href="#decision">Decide</a>}
      />,
    );
    const banner = screen.getByRole("region", { name: "Next step" });
    expect(banner).toHaveAttribute("data-tone", "warn");
    expect(banner.className).toContain("bg-waiting-soft");
    expect(within(banner).getByRole("link", { name: "Decide" })).toHaveAttribute("href", "#decision");
  });
});

describe("RequestCard", () => {
  it("shows what was asked: its fields by name and its text as Markdown", () => {
    render(
      <RequestCard
        body={"## Context\n\nThe new hire starts **Monday**."}
        fields={{ start_date: "2026-11-09", needs_laptop: true, empty: "" }}
      />,
    );
    const fields = screen.getByLabelText("Fields of the request");
    expect(fields).toHaveTextContent("Start date");
    expect(fields).toHaveTextContent("2026-11-09");
    expect(fields).toHaveTextContent("Needs laptop");
    expect(fields).toHaveTextContent("Yes");
    expect(fields).not.toHaveTextContent("Empty");
    expect(screen.getByRole("heading", { name: "Context" })).toBeInTheDocument();
    expect(screen.getByText("Monday").tagName).toBe("STRONG");
  });

  it("labels a field with the title its workflow gives it, else with its name (S55-14)", () => {
    render(
      <RequestCard
        body={null}
        fields={{ start_date: "2026-11-09", needs_laptop: true }}
        titles={{ start_date: "first day at work" }}
      />,
    );
    const fields = screen.getByLabelText("Fields of the request");
    expect(within(fields).getByText("First day at work")).toBeInTheDocument();
    expect(fields).not.toHaveTextContent("Start date");
    expect(within(fields).getByText("Needs laptop")).toBeInTheDocument();
  });

  it("says when the request was a title only", () => {
    render(<RequestCard body={null} fields={{}} />);
    expect(screen.getByTestId("request-card")).toHaveTextContent("The request was filed with a title only.");
  });

  it("folds a long text behind Show all", () => {
    render(<LongText text={"word ".repeat(FOLD_AFTER)} />);
    expect(screen.getByText("Show all")).toBeInTheDocument();
  });
});

describe("fieldTitles (S55-14)", () => {
  it("reads the titles of `metadata.inputs`, leaving out a field without one", () => {
    expect(
      fieldTitles({
        type: "object",
        properties: {
          incident_id: { type: "string", title: " incident id " },
          severity: { type: "string", enum: ["sev1"] },
          blank: { type: "string", title: "  " },
        },
      }),
    ).toEqual({ incident_id: "incident id" });
  });

  it("reads nothing from a workflow without inputs", () => {
    expect(fieldTitles(undefined)).toEqual({});
    expect(fieldTitles({ type: "object" })).toEqual({});
    expect(fieldTitles({ properties: null })).toEqual({});
  });
});
