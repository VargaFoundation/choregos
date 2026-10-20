import { describe, expect, it } from "vitest";
import { readSandboxNetwork, withSandboxNetwork, type SandboxNetwork } from "@/lib/policy-network";

const NETWORK: SandboxNetwork = {
  allow_domains: ["pypi.org", "*.githubusercontent.com"],
  deny_domains: [],
  per_host_per_minute: 30,
  builtin_web_tools: "deny",
};

function written(yaml: string, network: SandboxNetwork = NETWORK): string {
  const out = withSandboxNetwork(yaml, network);
  if (!out.ok) throw new Error(out.reason);
  return out.yaml;
}

describe("the project's network, written into its policy YAML (S41-22)", () => {
  it("replaces sandbox.network and keeps every other line, comments included", () => {
    const yaml = [
      "# the team's policy",
      "budgets:",
      "  per_ticket_usd: 25 # per ticket",
      "sandbox:",
      "  runtime: gvisor",
      "  network:",
      "    allow_domains:",
      "      - github.com",
      "    deny_by_default: true",
      "  # the rest is the default",
      "  prompt_injection: block",
      "release_train:",
      "  prod: {mode: train}",
      "",
    ].join("\n");
    const out = written(yaml, { ...NETWORK, deny_by_default: true });
    expect(out).toBe(
      [
        "# the team's policy",
        "budgets:",
        "  per_ticket_usd: 25 # per ticket",
        "sandbox:",
        "  runtime: gvisor",
        "  network:",
        "    allow_domains:",
        "      - pypi.org",
        '      - "*.githubusercontent.com"',
        "    deny_domains: []",
        "    per_host_per_minute: 30",
        "    builtin_web_tools: deny",
        "    deny_by_default: true",
        "  # the rest is the default",
        "  prompt_injection: block",
        "release_train:",
        "  prod: {mode: train}",
        "",
      ].join("\n"),
    );
  });

  it("adds network to an existing sandbox, at its indentation", () => {
    const out = written("sandbox:\n    runtime: gvisor\nbudgets:\n    per_ticket_usd: 5\n", { allow_domains: ["pypi.org"] });
    expect(out).toBe("sandbox:\n    runtime: gvisor\n    network:\n      allow_domains:\n        - pypi.org\nbudgets:\n    per_ticket_usd: 5\n");
  });

  it("adds sandbox when the policy has none, and writes a policy that was empty", () => {
    expect(written("budgets:\n  per_ticket_usd: 25\n", { allow_domains: [] })).toBe(
      "budgets:\n  per_ticket_usd: 25\nsandbox:\n  network:\n    allow_domains: []\n",
    );
    expect(written("", { allow_domains: ["*"] })).toBe('sandbox:\n  network:\n    allow_domains:\n      - "*"\n');
    expect(written("# nothing yet\n", { allow_domains: ["pypi.org"] })).toBe("# nothing yet\nsandbox:\n  network:\n    allow_domains:\n      - pypi.org\n");
  });

  it("fills a sandbox key that has no value", () => {
    expect(written("sandbox:\nbudgets: {per_ticket_usd: 3}\n", { allow_domains: ["pypi.org"] })).toBe(
      "sandbox:\n  network:\n    allow_domains:\n      - pypi.org\nbudgets: {per_ticket_usd: 3}\n",
    );
  });

  it("reads back what it wrote, flow or block, quoted or not", () => {
    expect(readSandboxNetwork(written("sandbox:\n  runtime: gvisor\n"))).toEqual(NETWORK);
    expect(readSandboxNetwork("sandbox:\n  network: {allow_domains: [pypi.org, '*.x.com'], per_run_per_minute: 9}\n")).toEqual({
      allow_domains: ["pypi.org", "*.x.com"],
      per_run_per_minute: 9,
    });
    expect(readSandboxNetwork("budgets: {}\n")).toEqual({});
  });

  it("quotes a host YAML would read as something else", () => {
    expect(written("", { allow_domains: ["yes", "null", "*.example.com"] })).toContain('- "yes"\n      - "null"\n      - "*.example.com"');
    expect(readSandboxNetwork(written("", { allow_domains: ["yes", "null"] }))).toEqual({ allow_domains: ["yes", "null"] });
  });

  it("refuses rather than guess", () => {
    expect(withSandboxNetwork("budgets: [\n", NETWORK)).toEqual({ ok: false, reason: expect.stringMatching(/does not parse/) });
    expect(withSandboxNetwork("sandbox: {runtime: gvisor}\n", NETWORK)).toEqual({ ok: false, reason: expect.stringMatching(/block mapping/) });
    expect(withSandboxNetwork("a: 1\n---\nb: 2\n", NETWORK)).toEqual({ ok: false, reason: expect.stringMatching(/several YAML documents/) });
    expect(withSandboxNetwork("- a\n- b\n", NETWORK)).toEqual({ ok: false, reason: expect.stringMatching(/not a YAML mapping/) });
  });
});
