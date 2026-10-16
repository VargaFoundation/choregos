import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentBuilder, cleanSpec, openByDefault, sectionSummaries } from "@/components/agents/builder";
import { api } from "@/lib/api";
import {
  flaggedByCeiling,
  isDefaultNetwork,
  networkMode,
  networkSummary,
  parseHosts,
  patternWithin,
  validatePattern,
} from "@/lib/network";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      createAgent: vi.fn().mockResolvedValue({ slug: "triage", display_name: "Triage", latest_version: 1 }),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      platformModels: vi.fn().mockResolvedValue([]),
      backends: vi.fn().mockResolvedValue([]),
      orgConnectors: vi.fn().mockResolvedValue([]),
      skills: vi.fn().mockResolvedValue([]),
      knowledgeBases: vi.fn().mockResolvedValue([]),
      policyCeiling: vi.fn(),
    },
  };
});

describe("host patterns (a mirror of the server's grammar)", () => {
  it("accepts a host, a wildcard of subdomains and `*`, normalised", () => {
    expect(validatePattern(" GitHub.com. ")).toEqual({ ok: true, value: "github.com" });
    expect(validatePattern("*.example.com")).toEqual({ ok: true, value: "*.example.com" });
    expect(validatePattern("*")).toEqual({ ok: true, value: "*" });
  });

  it.each(["https://x.example", "x/y", "x:443", "user@x", "1.2.3.4", "*x.com", "a.*.com", "a..com", "*.com", ""])(
    "refuses %j",
    (bad) => expect(validatePattern(bad).ok).toBe(false),
  );

  it("parses a typed list: one per line or comma, deduplicated, with a line per refusal", () => {
    expect(parseHosts("pypi.org, API.github.com\npypi.org\n  ")).toEqual({ hosts: ["pypi.org", "api.github.com"], problems: [] });
    const bad = parseHosts("pypi.org\nhttps://x.example");
    expect(bad.hosts).toEqual(["pypi.org"]);
    expect(bad.problems).toHaveLength(1);
  });

  it("decides what narrows what", () => {
    expect(patternWithin("api.github.com", "github.com")).toBe(true);
    expect(patternWithin("*.github.com", "github.com")).toBe(true);
    expect(patternWithin("github.com", "*.github.com")).toBe(false);
    expect(patternWithin("anything.io", "*")).toBe(true);
    expect(patternWithin("*", "github.com")).toBe(false);
  });

  it("flags the hosts the organisation's ceiling refuses, with the reason", () => {
    const ceiling = { allow_domains: ["github.com", "pypi.org"], deny_domains: ["gist.github.com"] };
    expect(flaggedByCeiling(["api.github.com", "api.stripe.com", "gist.github.com"], ceiling)).toEqual([
      { host: "api.stripe.com", reason: "outside the organisation's allowed hosts" },
      { host: "gist.github.com", reason: "denied by the organisation (gist.github.com)" },
    ]);
    expect(flaggedByCeiling(["api.stripe.com"], { allow_domains: null })).toEqual([]);
    expect(flaggedByCeiling(["api.stripe.com"], null)).toEqual([]);
  });
});

describe("the agent's network in the builder's rules", () => {
  it("has three modes and a one-line summary", () => {
    expect(networkMode(undefined)).toBe("project");
    expect(networkMode({ domains: null })).toBe("project");
    expect(networkMode({ domains: [] })).toBe("none");
    expect(networkMode({ domains: ["pypi.org"] })).toBe("listed");
    expect(networkSummary(undefined)).toBe("the project's hosts");
    expect(networkSummary({ domains: [] })).toBe("no internet");
    expect(networkSummary({ domains: ["a.io", "b.io", "c.io"], per_host_per_minute: 10, builtin_web_tools: false })).toBe(
      "3 hosts · 10/min per host · built-in web tools off",
    );
    expect(networkSummary({ domains: ["a.io"] })).toBe("1 host");
  });

  it("opens its section when it holds something, and is dropped from a version that holds nothing", () => {
    expect(openByDefault({}).network).toBe(false);
    expect(openByDefault({ network: { domains: null, per_host_per_minute: null } }).network).toBe(false);
    expect(openByDefault({ network: { domains: [] } }).network).toBe(true);
    expect(sectionSummaries({ network: { domains: [] } }).network).toBe("no internet");
    expect(isDefaultNetwork({ domains: null, per_host_per_minute: null, builtin_web_tools: null })).toBe(true);
    expect(cleanSpec({ network: { domains: null } }).network).toBeUndefined();
    expect(cleanSpec({ network: { domains: [] } }).network).toEqual({ domains: [] });
  });
});

describe("the builder's Network access section", () => {
  beforeEach(() => {
    vi.mocked(api.policyCeiling).mockResolvedValue({
      ceiling: { network: { allow_domains: ["github.com", "pypi.org"], deny_domains: ["gist.github.com"] } } as never,
    });
    vi.mocked(api.createAgent).mockClear();
  });

  function open() {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentBuilder org="acme" mode={{ kind: "create" }} />
      </QueryClientProvider>,
    );
    return screen.getByTestId("agent-builder");
  }

  async function publish(builder: HTMLElement) {
    fireEvent.change(within(builder).getByLabelText("Slug"), { target: { value: "triage" } });
    fireEvent.click(within(builder).getByRole("button", { name: "Create and activate" }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    return vi.mocked(api.createAgent).mock.calls[0]![1].spec!;
  }

  it("by default writes nothing: the project's policy decides", async () => {
    const builder = open();
    expect(within(builder).getByLabelText("Same as the project's policy")).toBeChecked();
    expect((await publish(builder)).network).toBeUndefined();
  });

  it("“No internet” writes an empty list", async () => {
    const builder = open();
    fireEvent.click(within(builder).getByLabelText("No internet"));
    expect((await publish(builder)).network).toEqual({ domains: [] });
  });

  it("“Only these hosts” writes the typed list, and the politeness and web-tool choices", async () => {
    const builder = open();
    fireEvent.click(within(builder).getByLabelText("Only these hosts"));
    expect(within(builder).getByText(/Name at least one host/)).toBeInTheDocument();
    fireEvent.change(within(builder).getByLabelText("Allowed hosts"), { target: { value: "pypi.org\napi.github.com" } });
    fireEvent.change(within(builder).getByLabelText("Requests per minute, per host"), { target: { value: "10" } });
    fireEvent.change(within(builder).getByLabelText("Built-in web tools"), { target: { value: "off" } });
    expect((await publish(builder)).network).toEqual({
      domains: ["pypi.org", "api.github.com"],
      per_host_per_minute: 10,
      builtin_web_tools: false,
    });
  });

  it("flags a host outside the organisation's allowed set, or denied by it, before publishing", async () => {
    const builder = open();
    await waitFor(() => expect(api.policyCeiling).toHaveBeenCalled());
    fireEvent.click(within(builder).getByLabelText("Only these hosts"));
    fireEvent.change(within(builder).getByLabelText("Allowed hosts"), { target: { value: "api.stripe.com\ngist.github.com\npypi.org" } });
    const flagged = await within(builder).findByTestId("agent-network-flagged");
    expect(flagged).toHaveTextContent("api.stripe.com: outside the organisation's allowed hosts");
    expect(flagged).toHaveTextContent("gist.github.com: denied by the organisation");
    expect(flagged).not.toHaveTextContent("pypi.org");
  });

  it("flags a malformed pattern", () => {
    const builder = open();
    fireEvent.click(within(builder).getByLabelText("Only these hosts"));
    fireEvent.change(within(builder).getByLabelText("Allowed hosts"), { target: { value: "https://x.example" } });
    expect(within(builder).getByTestId("agent-network-problems")).toHaveTextContent("scheme");
  });

  it("summarises the choice on the folded section", () => {
    const builder = open();
    fireEvent.click(within(builder).getByLabelText("No internet"));
    expect(within(builder).getByText("Network access").closest("summary")).toHaveTextContent("no internet");
  });
});
