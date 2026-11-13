// SPDX-License-Identifier: Apache-2.0
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ActionPage from "@/app/p/[slug]/actions/[id]/page";
import { SettleInDoubt, Settled, parseResult } from "@/components/actions/settle";
import { Status } from "@/components/actions/status";
import { ApiError, api } from "@/lib/api";
import type { Action } from "@/lib/types";

/**
 * A person settles an action in doubt (S65-26, ADR 0149 §5): on the action's page, they say what they found in the
 * system the write was for (made, or not made), with a note, and with `made` the result they read there if they give
 * one. The API's refusals are shown as it words them; once settled, the page shows the record instead of the form.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, projectAction: vi.fn(), settleAction: vi.fn() } };
});

afterEach(() => vi.mocked(api.settleAction).mockReset());

const inDoubt = {
  id: "a1",
  origin: "tool",
  kind: "tracker.update_issue",
  title: "changes an issue (tracker)",
  status: "in_doubt",
  error: "tracker/update_issue : claimed and never answered",
  effects: [{ effect: "connector.call", with: { connector: "tracker", operation: "update_issue" } }],
  approval: { step_up_minutes: 10 },
  decisions: [],
  result: { compensations: [], in_doubt: { position: 0, sent: [{ outpost: "factory-floor" }] } },
  journal: [
    { position: 0, key: "a1:0", effect: "connector.call", status: "in_doubt", attempts: 1, settlement: null },
  ],
  settlement: null,
} as unknown as Action;

const RECORD = {
  position: 0,
  outcome: "made",
  by: "ops@varga.dev",
  at: "2026-11-14T10:05:00Z",
  note: "OPS-8 is in the tracker",
  result: { key: "OPS-8" },
  in_doubt: [{ outpost: "factory-floor" }],
};

function form() {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <SettleInDoubt projet="billing-api" action={inDoubt} />
    </QueryClientProvider>,
  );
  const button = screen.getByRole("button", { name: "Record what I found" });
  const [note] = screen.getAllByRole("textbox");
  return { button, note: note! };
}

describe("settling an action in doubt (S65-26)", () => {
  it("is offered only for an action in doubt that nobody settled", () => {
    const client = new QueryClient();
    const { container } = render(
      <QueryClientProvider client={client}>
        <SettleInDoubt projet="p" action={{ ...inDoubt, status: "failed" } as Action} />
        <SettleInDoubt projet="p" action={{ ...inDoubt, settlement: RECORD } as unknown as Action} />
      </QueryClientProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("asks for an outcome and a note before anything is sent, and says who may settle", () => {
    const { button, note } = form();
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /The write was made/ }));
    expect(button).toBeDisabled();
    fireEvent.change(note, { target: { value: "   " } });
    expect(button).toBeDisabled();
    fireEvent.change(note, { target: { value: "OPS-8 is in the tracker" } });
    expect(button).toBeEnabled();
    const rules = screen.getByTestId("settle-rules");
    expect(rules).toHaveTextContent("within 10 min");
    expect(rules).toHaveTextContent("Whoever proposed the action, or owns the agent that did, cannot settle it");
  });

  it("made sends the note and the result the person read, as an object", async () => {
    vi.mocked(api.settleAction).mockResolvedValue({ ...inDoubt, status: "approved" } as Action);
    const { button, note } = form();
    fireEvent.click(screen.getByRole("radio", { name: /The write was made/ }));
    fireEvent.change(note, { target: { value: "  OPS-8 is in the tracker " } });
    const result = screen.getAllByRole("textbox")[1]!;
    fireEvent.change(result, { target: { value: "[1, 2]" } });
    expect(screen.getByText("Not a JSON object.")).toBeInTheDocument();
    expect(button).toBeDisabled();
    fireEvent.change(result, { target: { value: '{"key": "OPS-8"}' } });
    expect(button).toBeEnabled();
    await act(async () => fireEvent.click(button));
    expect(api.settleAction).toHaveBeenCalledWith("billing-api", "a1", {
      outcome: "made",
      note: "OPS-8 is in the tracker",
      result: { key: "OPS-8" },
    });
  });

  it("not made sends no result, even one typed before the outcome changed", async () => {
    vi.mocked(api.settleAction).mockResolvedValue({ ...inDoubt, status: "approved" } as Action);
    const { button, note } = form();
    fireEvent.click(screen.getByRole("radio", { name: /The write was made/ }));
    fireEvent.change(screen.getAllByRole("textbox")[1]!, { target: { value: '{"key": "OPS-8"}' } });
    fireEvent.click(screen.getByRole("radio", { name: /The write was not made/ }));
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    fireEvent.change(note, { target: { value: "nothing in the tracker" } });
    await act(async () => fireEvent.click(button));
    expect(api.settleAction).toHaveBeenCalledWith("billing-api", "a1", { outcome: "not_made", note: "nothing in the tracker" });
  });

  it("shows the API's refusal as it words it, with its code", async () => {
    vi.mocked(api.settleAction).mockRejectedValue(
      new ApiError(409, {
        title: "State conflict",
        detail: "this action was already settled `made` by ops@varga.dev",
        code: "action_already_settled",
      }),
    );
    const { button, note } = form();
    fireEvent.click(screen.getByRole("radio", { name: /The write was not made/ }));
    fireEvent.change(note, { target: { value: "nothing there" } });
    await act(async () => fireEvent.click(button));
    expect(
      await screen.findByText("this action was already settled `made` by ops@varga.dev (action_already_settled)"),
    ).toBeInTheDocument();
  });

  it("the record is shown once settled: who, when, the note and the result stated", () => {
    render(<Settled action={{ ...inDoubt, status: "approved", settlement: RECORD } as unknown as Action} />);
    const shown = screen.getByTestId("settled");
    expect(shown).toHaveTextContent("Recorded made by ops@varga.dev");
    expect(shown).toHaveTextContent("OPS-8 is in the tracker");
    expect(shown).toHaveTextContent('{"key":"OPS-8"}');
    const { container } = render(<Settled action={inDoubt} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("an effect recorded not made is said in words, with the neutral tone of what did nothing", () => {
    const { container } = render(<Status statut="not_made" />);
    expect(container).toHaveTextContent("not made");
  });

  it("parses a stated result: an object or nothing", () => {
    expect(parseResult("")).toEqual({ ok: true, value: null });
    expect(parseResult('{"a": 1}')).toEqual({ ok: true, value: { a: 1 } });
    expect(parseResult("null")).toEqual({ ok: false });
    expect(parseResult("{")).toEqual({ ok: false });
  });

  it("the action's page offers the form while in doubt, and shows the record once settled", async () => {
    vi.mocked(api.projectAction).mockResolvedValueOnce(inDoubt);
    let view: ReturnType<typeof render> | undefined;
    await act(async () => {
      view = render(
        <QueryClientProvider client={new QueryClient()}>
          <Suspense fallback="…">
            <ActionPage params={Promise.resolve({ slug: "billing-api", id: "a1" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    expect(await screen.findByTestId("settle")).toBeInTheDocument();
    expect(screen.getByTestId("in-doubt")).toHaveTextContent("record what you found below");
    view!.unmount();
    vi.mocked(api.projectAction).mockResolvedValueOnce({ ...inDoubt, status: "approved", settlement: RECORD } as unknown as Action);
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Suspense fallback="…">
            <ActionPage params={Promise.resolve({ slug: "billing-api", id: "a1" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    expect(await screen.findByTestId("settled")).toHaveTextContent("Recorded made");
    expect(screen.queryByTestId("settle")).not.toBeInTheDocument();
  });
});
