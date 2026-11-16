import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import ActionPage from "@/app/p/[slug]/actions/[id]/page";
import { InDoubt, sentInDoubt } from "@/components/actions/in-doubt";
import { Status } from "@/components/actions/status";
import { atInstant, buildModel } from "@/components/journey/model";
import { api } from "@/lib/api";
import type { Action, JourneyStep } from "@/lib/types";
import { journeyInProgress } from "@/mocks/journey";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, projectAction: vi.fn() } };
});

const SENT = {
  outpost: "factory-floor",
  endpoint: "tracker",
  operation: "update_issue",
  call_id: "c0ffee",
  instance_id: "instance-7",
  claimed_at: "2026-11-12T10:00:00Z",
  arguments_sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
};

const inDoubt = {
  id: "a1",
  origin: "tool",
  kind: "tracker.update_issue",
  title: "changes an issue (tracker)",
  status: "in_doubt",
  error: "tracker/update_issue : the outpost `factory-floor` claimed the write and did not answer in time",
  effects: [
    { effect: "connector.call", with: { connector: "tracker", operation: "create_issue" } },
    { effect: "connector.call", with: { connector: "tracker", operation: "update_issue" } },
  ],
  decisions: [],
  result: { compensations: [], in_doubt: { position: 1, sent: [SENT] } },
  journal: [
    { position: 0, key: "a1:0", effect: "connector.call", status: "done", attempts: 1 },
    {
      position: 1,
      key: "a1:1",
      effect: "connector.call",
      status: "in_doubt",
      attempts: 1,
      result: { in_doubt: [SENT], again: false },
      error: "claimed and never answered",
    },
  ],
} as unknown as Action;

describe("an action in doubt is a status the console knows (S65-14)", () => {
  it("the badge says it, with the tone of what waits for a person, not the tone of an unknown code", () => {
    const { container } = render(
      <>
        <Status statut="in_doubt" />
        <Status statut="awaiting_evidence" />
        <Status statut="a_status_nobody_declared" />
      </>,
    );
    const [doubt, waits, unknown] = [...container.children] as HTMLElement[];
    expect(doubt).toHaveTextContent("in doubt");
    expect(doubt!.className).toBe(waits!.className);
    expect(doubt!.className).not.toBe(unknown!.className);
  });

  it("the note says what was sent, that nothing replays or undoes it, and what a person does", () => {
    render(<InDoubt action={inDoubt} />);
    const note = screen.getByTestId("in-doubt");
    expect(note).toHaveTextContent("does not send it again");
    expect(note).toHaveTextContent("has undone nothing");
    expect(note).toHaveTextContent("Check the system behind the outpost");
    expect(note).toHaveTextContent("record what you found");
    const sent = screen.getByTestId("in-doubt-sent");
    for (const shown of ["update_issue", "factory-floor", "tracker", "instance-7", "9f86d081884c"]) {
      expect(sent).toHaveTextContent(shown);
    }
    expect(screen.getByRole("status")).toHaveTextContent("nobody knows whether it was made");
  });

  it("what was sent is read from the journal when the action carries no summary", () => {
    const bare = { ...inDoubt, result: { compensations: [] } } as unknown as Action;
    expect(sentInDoubt(bare)).toEqual([SENT]);
    expect(sentInDoubt({ ...bare, journal: [] } as unknown as Action)).toEqual([]);
  });

  it("an action that is not in doubt has no such note", () => {
    const { container } = render(<InDoubt action={{ ...inDoubt, status: "failed" } as Action} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("the action's page shows the note, the effect in doubt and the one that stays done", async () => {
    vi.mocked(api.projectAction).mockResolvedValue(inDoubt);
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Suspense fallback="…">
            <ActionPage params={Promise.resolve({ slug: "billing-api", id: "a1" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    expect(await screen.findByTestId("in-doubt")).toHaveTextContent("Check the system behind the outpost");
    expect(screen.getByTestId("effect-0")).toHaveTextContent("done");
    expect(screen.getByTestId("effect-1")).toHaveTextContent("in doubt");
    expect(screen.getByTestId("effect-1")).toHaveTextContent("claimed and never answered");
  });
});

describe("a step whose action is in doubt (S65-14)", () => {
  const NOW = Date.parse("2026-10-08T12:00:00Z");
  const running = journeyInProgress(NOW);
  const model = buildModel(running);
  const attempt = (transition: string): JourneyStep =>
    ({
      ...running.steps[0]!,
      id: `action-${transition}`,
      kind: "action",
      transition_id: transition,
      actor: "platform",
      status: "in_doubt",
      started_at: new Date(NOW - 60_000).toISOString(),
      ended_at: new Date(NOW - 30_000).toISOString(),
    }) as JourneyStep;

  it("waits for a person while the work item is still there: nothing is running", () => {
    // The work item is at `reviewed`, where the security review starts; without the action it runs.
    expect(atInstant(running, model, Infinity).steps.get("t-security-review")!.status).toBe("running");
    const others = running.steps.filter((step) => step.transition_id !== "t-security-review");
    const doubted = { ...running, steps: [...others, attempt("t-security-review")] };
    expect(atInstant(doubted, model, Infinity).steps.get("t-security-review")!.status).toBe("waiting");
  });

  it("did not succeed once the work item is elsewhere: it is not shown as still to come", () => {
    expect(atInstant(running, model, Infinity).steps.get("t-open-pr")!.status).toBe("upcoming");
    const doubted = { ...running, steps: [...running.steps, attempt("t-open-pr")] };
    expect(atInstant(doubted, model, Infinity).steps.get("t-open-pr")!.status).toBe("failed");
  });
});
