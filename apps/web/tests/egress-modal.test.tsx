import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EgressModal, HONEST_SENTENCE } from "@/components/egress/egress-modal";
import { EgressProvider, egressFlag, useEgressGuard } from "@/components/egress/egress-provider";
import { GitImport } from "@/components/skills/gate";
import { ApiError, api } from "@/lib/api";
import { EgressCancelled, EgressRequested, egressRequired, outsideCeiling } from "@/lib/egress";
import type { EgressOverview, EgressVerdict } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      requestOrgEgress: vi.fn().mockResolvedValue({ id: "eg-9", status: "requested" }),
      previewSkillsFromGit: vi.fn(),
      importSkillsFromGit: vi.fn(),
    },
  };
});

const verdict = (over: Partial<EgressVerdict> = {}): EgressVerdict => ({
  host: "mcp.example.test",
  port: 443,
  used_by: "api",
  org: "absent",
  ceiling: "open",
  can_allow: true,
  approvers: [{ display_name: "Augustin", email: "augustin@acme.dev" }],
  platform_change: { needed: false },
  ...over,
});

const required = (verdicts: EgressVerdict[]) =>
  new ApiError(409, { title: "Network access required", code: "egress_required", verdicts });

/** A gesture that needs network access: the call, and what the guard did with it. */
function Harness({ call }: { call: (allow: boolean) => Promise<string> }) {
  const guard = useEgressGuard();
  const [outcome, setOutcome] = useState("idle");
  return (
    <>
      <button
        onClick={() => {
          guard(call, { gesture: "Declare the mcp connector vendor", origin: "org-connector:vendor", reason: "the vendor connector" })
            .then((value) => setOutcome(`done:${value}`))
            .catch((cause: Error) => setOutcome(`${cause.name}:${cause.message}`));
        }}
      >
        go
      </button>
      <output data-testid="outcome">{outcome}</output>
    </>
  );
}

function view(call: (allow: boolean) => Promise<string>) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EgressProvider>
        <Harness call={call} />
      </EgressProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "go" }));
}

/**
 * The modal, once its `<dialog>` is open. Its content renders with the state, but the `open`
 * attribute comes from an effect: until then the dialog is hidden, every role query inside it misses,
 * and a `queryByRole(...)).toBeNull()` passes for the wrong reason (#686).
 */
const openModal = () => screen.findByRole("dialog", { name: "Network access needed" });

beforeEach(() => vi.clearAllMocks());

describe("the network access modal (S39-22)", () => {
  it("an administrator sees 'Allow and continue', and the retry carries allow_egress", async () => {
    const call = vi.fn().mockRejectedValueOnce(required([verdict()])).mockResolvedValueOnce("ok");
    view(call);
    expect(await openModal()).toBeInTheDocument();
    expect(screen.getByTestId("org-mcp.example.test").textContent).toBe("not allowed");
    expect(screen.getByTestId("ceiling-mcp.example.test").textContent).toBe("declared open");
    expect(screen.queryByRole("button", { name: "Ask an administrator" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Allow and continue" }));
    await waitFor(() => expect(screen.getByTestId("outcome").textContent).toBe("done:ok"));
    expect(call.mock.calls.map((c) => c[0])).toEqual([false, true]);
    expect(egressFlag(true)).toEqual({ allow_egress: true });
    expect(egressFlag(false)).toEqual({});
  });

  it("anyone else sees who can allow it, and asks: a request with the prefilled reason and origin", async () => {
    const call = vi.fn().mockRejectedValueOnce(required([verdict({ can_allow: false })]));
    view(call);
    await openModal();
    const approvers = screen.getByTestId("egress-approvers");
    expect(approvers.textContent).toContain("Augustin · augustin@acme.dev");
    expect(screen.queryByRole("button", { name: "Allow and continue" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ask an administrator" }));
    await waitFor(() => expect(screen.getByTestId("outcome").textContent).toMatch(/^EgressRequested:/));
    expect(api.requestOrgEgress).toHaveBeenCalledWith("acme", {
      host: "mcp.example.test",
      ports: [443],
      used_by: ["api"],
      reason: "the vendor connector",
      origin: "org-connector:vendor",
    });
    expect(call).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("outcome").textContent).toContain("retry once it is approved");
  });

  it("closing the modal abandons the add without retrying it", async () => {
    const call = vi.fn().mockRejectedValue(required([verdict()]));
    view(call);
    await openModal();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.getByTestId("outcome").textContent).toMatch(/^EgressCancelled:/));
    expect(call).toHaveBeenCalledTimes(1);
  });

  it("an error that is not egress_required passes through untouched", async () => {
    const call = vi.fn().mockRejectedValue(new ApiError(409, { title: "State conflict", detail: "already exists" }));
    view(call);
    await waitFor(() => expect(screen.getByTestId("outcome").textContent).toBe("ApiError:already exists"));
    expect(screen.queryByTestId("egress-modal")).toBeNull();
  });

  it("without a provider the add just runs", async () => {
    const call = vi.fn().mockResolvedValue("ok");
    render(<Harness call={call} />);
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    await waitFor(() => expect(screen.getByTestId("outcome").textContent).toBe("done:ok"));
    expect(call).toHaveBeenCalledWith(false);
  });
});

describe("the platform change block", () => {
  const props = { open: true, gesture: "Declare it", onAllow: vi.fn(), onRequest: vi.fn(), onClose: vi.fn() };
  const outside = verdict({
    ceiling: "outside",
    platform_change: { needed: true, format: "text", snippet: "add mcp.example.test to the api entry\n", owner: "the platform team" },
  });

  it("shows the snippet, a copy button and the honest sentence — and no issue link without issue_url", () => {
    render(<EgressModal {...props} verdicts={[outside]} />);
    expect(screen.getByTestId("ceiling-mcp.example.test").textContent).toBe("outside the declared ceiling");
    expect(screen.getByTestId("egress-snippet").textContent).toContain("add mcp.example.test to the api entry");
    expect(screen.getByRole("button", { name: "Copy the change" })).toBeInTheDocument();
    expect(screen.getByTestId("egress-platform").textContent).toContain(HONEST_SENTENCE);
    expect(screen.getByTestId("egress-platform").textContent).toContain("calls to mcp.example.test will be refused by the network");
    expect(screen.getByTestId("egress-platform").textContent).toContain("the platform team");
    expect(screen.queryByRole("link", { name: "Open a prefilled issue" })).toBeNull();
  });

  it("offers the prefilled issue when the deployment configures one", () => {
    const url = "https://git.example.test/platform/issues/new?title=egress";
    render(<EgressModal {...props} verdicts={[{ ...outside, platform_change: { ...outside.platform_change, issue_url: url } }]} />);
    expect(screen.getByRole("link", { name: "Open a prefilled issue" })).toHaveAttribute("href", url);
  });

  it("an open host shows no platform block, and an unknown ceiling is said as such", () => {
    const { rerender } = render(<EgressModal {...props} verdicts={[verdict()]} />);
    expect(screen.queryByTestId("egress-platform")).toBeNull();
    rerender(<EgressModal {...props} verdicts={[verdict({ ceiling: "unknown", platform_change: { needed: true, format: "text", snippet: "x\n" } })]} />);
    expect(screen.getByTestId("ceiling-mcp.example.test").textContent).toBe("not declared by this deployment");
    expect(screen.getByTestId("egress-platform")).toBeInTheDocument();
  });

  it("a request already waiting says who asked and when", () => {
    render(<EgressModal {...props} verdicts={[verdict({ org: "requested", can_allow: false, requested_by: "lea@acme.dev", requested_at: "2026-10-16T08:00:00Z" })]} />);
    expect(screen.getByTestId("org-mcp.example.test").textContent).toBe("requested by lea@acme.dev on 2026-10-16, waiting for an administrator");
  });
});

describe("reading the 409", () => {
  it("only an egress_required 409 with verdicts opens the modal", () => {
    expect(egressRequired(required([verdict()]))).toHaveLength(1);
    expect(egressRequired(new ApiError(409, { title: "State conflict" }))).toBeNull();
    expect(egressRequired(new ApiError(422, { code: "egress_required", verdicts: [verdict()] }))).toBeNull();
    expect(egressRequired(new Error("x"))).toBeNull();
    expect(new EgressCancelled().name).toBe("EgressCancelled");
    expect(new EgressRequested(["a.test"]).message).toContain("a.test");
  });

  it("lists the allowed hosts the deployment's ceiling does not cover, per family", () => {
    const overview = {
      mode: "enforce",
      ceiling: { api: ["api.github.com", "*.example.test"], workers: [] },
      rules: [
        { id: "1", host: "api.github.com", ports: [443], used_by: ["api"], status: "allowed" },
        { id: "2", host: "mcp.other.test", ports: [443], used_by: ["api", "workers"], status: "allowed" },
        { id: "3", host: "x.example.test", ports: [443], used_by: ["api"], status: "allowed" },
        { id: "4", host: "gone.other.test", ports: [443], used_by: ["api"], status: "revoked" },
      ],
      requests: [],
    } as EgressOverview;
    expect(outsideCeiling(overview).map(({ rule, family }) => `${rule.host}/${family}`)).toEqual(["mcp.other.test/api"]);
  });
});

describe("a git skill import goes through the modal", () => {
  it("the preview is retried with allow_egress once an administrator allows the hosts", async () => {
    const preview = { repo: "https://github.com/acme/skills", ref: "main", sha: "a".repeat(40), path: "skills", allowed: true, skills: [] };
    vi.mocked(api.previewSkillsFromGit)
      .mockRejectedValueOnce(required([verdict({ host: "codeload.github.com" })]))
      .mockResolvedValueOnce(preview);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EgressProvider>
          <GitImport org="acme" />
        </EgressProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /preview|look|list/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Allow and continue" }));
    await waitFor(() => expect(api.previewSkillsFromGit).toHaveBeenCalledTimes(2));
    const [first, second] = vi.mocked(api.previewSkillsFromGit).mock.calls.map((c) => c[1]);
    expect(first).not.toHaveProperty("allow_egress");
    expect(second).toMatchObject({ allow_egress: true, repo_url: "https://github.com/anthropics/skills" });
  });
});
