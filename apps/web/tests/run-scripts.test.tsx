import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { Suspense } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RunPage from "@/app/p/[slug]/runs/[id]/page";
import { RunScriptsCard } from "@/components/run-scripts";
import { api } from "@/lib/api";
import { runScriptsOf } from "@/lib/run-scripts";
import type { RunEventDto } from "@/lib/types";
import { runAccess, runDiff, runs } from "@/mocks/data";

/**
 * A run shows its scripts (S57-10, ADR 0122 §4): the calls a script made through the tool bridge,
 * grouped under that script with its digest and its text; a proposal reads as not executed; a script
 * whose text never reached the bridge says so. The model's own calls stay in the activity.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      run: vi.fn(),
      workItem: vi.fn(),
      runEvents: vi.fn(),
      runAccess: vi.fn(),
      runDiff: vi.fn(),
      transcript: vi.fn(),
      runLogs: vi.fn(),
    },
  };
});
vi.mock("@/lib/sse", () => ({ useEventStream: () => ({ events: [], connected: false, error: null }) }));

const A = "a".repeat(64);
const B = "b".repeat(64);
const SCRIPT_A = 'from choregos_tools import pfsense\nrules = pfsense.list_firewall_rules(interface="wan")\nprint(len(rules))';
const ts = (s: number) => new Date(Date.UTC(2026, 10, 5, 9, 0, s)).toISOString();
const event = (seq: number, type: string, payload: Record<string, unknown>, s = seq): RunEventDto => ({ seq, type, ts: ts(s), payload });
const call = (seq: number, tool: string, status_code: number, script_sha256: string | null, extra: Record<string, unknown> = {}) =>
  event(seq, "tools.script.call", { tool, status_code, duration_ms: 420, response_bytes: 2048, script_sha256, ...extra });

const client = event(1, "tools.client", { written: true, tools: 12, modules: ["pfsense", "platform"] });
const textA = event(2, "tools.script", { sha256: A, bytes: SCRIPT_A.length, text: SCRIPT_A });

const show = (events: RunEventDto[]) => render(<RunScriptsCard scripts={runScriptsOf(events)} slug="netops" />);

afterEach(() => vi.clearAllMocks());

describe("a run's scripts, read back from its journal", () => {
  it("shows nothing for a run offered no scripts", () => {
    expect(runScriptsOf([event(1, "run.started", {})])).toBeNull();
    const { container } = show([]);
    expect(container).toBeEmptyDOMElement();
  });

  it("groups each call under the script that made it, in order, whatever the other script did in between", () => {
    const read = runScriptsOf([
      client,
      textA,
      call(3, "pfsense__list_firewall_rules", 200, A),
      call(4, "itsm__get_incident", 200, B),
      call(5, "pfsense__list_aliases", 200, A),
      call(5, "pfsense__list_aliases", 200, A), // the stored journal and the live stream overlap
    ])!;
    expect(read.client).toEqual({ written: true, tools: 12, modules: ["pfsense", "platform"], error: null });
    expect(read.scripts.map((s) => s.sha256)).toEqual([A, B]);
    expect(read.scripts[0]!.text).toBe(SCRIPT_A);
    expect(read.scripts[0]!.calls.map((c) => c.tool)).toEqual(["pfsense__list_firewall_rules", "pfsense__list_aliases"]);
    expect(read.scripts[1]!.text).toBeNull();
  });
});

describe("the run's scripts card", () => {
  it("shows a script's digest, its text collapsed and copyable, and its calls with status, duration and size", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    show([client, textA, call(3, "pfsense__list_firewall_rules", 200, A), call(4, "pfsense__list_aliases", 404, A)]);

    const script = screen.getByRole("region", { name: "Script aaaaaaaaaaaa" });
    expect(within(script).getByText("aaaaaaaaaaaa")).toHaveAttribute("title", `SHA-256 ${A}`);
    // The text is there, monospace, but folded: an auditor opens it.
    const text = within(script).getByTestId("script-text");
    expect(text.tagName).toBe("PRE");
    expect(text).toHaveTextContent("pfsense.list_firewall_rules");
    expect(text.closest("details")).not.toHaveAttribute("open");
    await act(async () => fireEvent.click(within(script).getByRole("button", { name: "Copy the text of the script aaaaaaaaaaaa" })));
    expect(writeText).toHaveBeenCalledWith(SCRIPT_A);

    const rows = within(within(script).getByRole("table", { name: "Calls of Script aaaaaaaaaaaa" })).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.getAttribute("data-call-seq"))).toEqual(["3", "4"]);
    expect(rows[0]).toHaveTextContent("pfsense__list_firewall_rules");
    expect(rows[0]).toHaveTextContent("Done · 200");
    expect(rows[0]).toHaveTextContent("0.4 s");
    expect(rows[0]).toHaveTextContent("2.0 KiB");
    expect(rows[1]).toHaveTextContent("Failed · 404");
    expect(screen.getByTestId("tools-client")).toHaveTextContent("its client lists 12 tools in 2 modules");
    expect(screen.getByTestId("scripts-summary")).toHaveTextContent("1 script · 2 calls through the tool bridge · 1 refused or failed");
  });

  it("a proposal made by a script reads as proposed, not executed, and links to its action when the journal names it", () => {
    show([
      textA,
      call(3, "pfsense__add_firewall_rule", 202, A, { action: "act-42" }),
      call(4, "pfsense__add_firewall_rule", 202, A),
    ]);
    const [named, unnamed] = screen.getAllByRole("row").filter((row) => row.getAttribute("data-call-seq"));
    for (const row of [named!, unnamed!]) {
      expect(row).toHaveAttribute("data-outcome", "proposed");
      expect(row).toHaveTextContent("Proposed — not executed · 202");
      expect(row).not.toHaveTextContent("Done");
    }
    expect(within(named!).getByRole("link", { name: "Open the proposal" })).toHaveAttribute("href", "/p/netops/actions/act-42");
    expect(within(unnamed!).queryByRole("link")).toBeNull();
    // Where the action is not named, the page says so and where to find it.
    const note = screen.getByTestId("proposal-not-linked");
    expect(note).toHaveTextContent("does not record which action a script's proposal created");
    expect(within(note).getByRole("link", { name: "project's actions" })).toHaveAttribute("href", "/p/netops/actions");
    expect(screen.getByTestId("scripts-summary")).toHaveTextContent("2 proposed, not executed");
  });

  it("never puts a journal value that is not an action id into a link", () => {
    show([textA, call(3, "pfsense__add_firewall_rule", 202, A, { action: "https://evil.example/x" })]);
    expect(screen.queryByRole("link", { name: "Open the proposal" })).toBeNull();
  });

  it("a script whose text was never posted says so, and so do the calls of a script piped on standard input", () => {
    show([call(3, "itsm__get_incident", 200, B), call(4, "itsm__list_incidents", 200, null)]);
    const known = screen.getByRole("region", { name: "Script bbbbbbbbbbbb" });
    expect(within(known).getByTestId("script-text-absent")).toHaveTextContent("Text not recorded: the script's text never reached the bridge");
    expect(within(known).queryByTestId("script-text")).toBeNull();
    const piped = screen.getByRole("region", { name: "Scripts piped on standard input" });
    expect(within(piped).getByTestId("script-text-absent")).toHaveTextContent(
      "Text not recorded: a script piped on standard input has no file",
    );
    expect(within(piped).getByRole("table")).toHaveTextContent("itsm__list_incidents");
  });

  it("says when the run was offered scripts but its client could not be written", () => {
    show([event(1, "tools.client", { written: false, error: "read-only file system" })]);
    expect(screen.getByTestId("tools-client")).toHaveTextContent("Client not written");
    expect(screen.getByTestId("tools-client")).toHaveTextContent("read-only file system");
  });
});

describe("the run page", () => {
  it("shows the scripts under their own card, and the model's own calls stay in the activity", async () => {
    vi.mocked(api.run).mockResolvedValue({ ...runs[0]!, status: "succeeded" });
    vi.mocked(api.workItem).mockResolvedValue({ tracker_key: "NET-7" } as never);
    vi.mocked(api.runAccess).mockResolvedValue(runAccess as never);
    vi.mocked(api.runDiff).mockResolvedValue(runDiff as never);
    vi.mocked(api.transcript).mockResolvedValue({ url: null } as never);
    vi.mocked(api.runEvents).mockResolvedValue([
      event(1, "session/update", {
        sessionId: "s",
        update: { sessionUpdate: "tool_call", toolCallId: "m1", title: "itsm__get_incident", kind: "fetch", status: "completed" },
      }),
      textA,
      call(3, "pfsense__list_firewall_rules", 200, A),
    ]);
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Suspense fallback={null}>
            <RunPage params={Promise.resolve({ slug: "netops", id: "r1" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    const scripts = await screen.findByTestId("run-scripts");
    expect(within(scripts).getByRole("region", { name: "Script aaaaaaaaaaaa" })).toHaveTextContent("pfsense__list_firewall_rules");
    expect(within(scripts).queryByText("itsm__get_incident")).toBeNull();
    expect(within(screen.getByTestId("run-activity")).getByText("itsm__get_incident")).toBeInTheDocument();
  });
});
