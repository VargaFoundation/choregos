import { describe, expect, it } from "vitest";
import { connectorIconOf } from "@/components/connector-icon";
import { BRAND_PATHS } from "@/components/brand-icon-paths";
import registry from "./fixtures/connector-types.json";

describe("the icon of a connector (S40-03b)", () => {
  it("every (kind, type) of the registry resolves to an icon", () => {
    expect(registry.length).toBeGreaterThan(20);
    for (const { kind, type } of registry) {
      const choice = connectorIconOf(kind, type);
      if (choice.kind === "brand") expect(BRAND_PATHS[choice.brand], `${kind}/${type}`).toMatch(/^[Mm]/);
      else expect(choice.icon, `${kind}/${type}`).toBeTruthy();
    }
  });

  it("brand types resolve to their brand", () => {
    const brand = (kind: string, type: string) => {
      const choice = connectorIconOf(kind, type);
      return choice.kind === "brand" ? choice.brand : null;
    };
    expect(brand("scm", "github")).toBe("github");
    expect(brand("tracker", "github-issues")).toBe("github");
    expect(brand("tracker", "gitlab-issues")).toBe("gitlab");
    expect(brand("tracker", "jira")).toBe("jira");
    expect(brand("documents", "confluence")).toBe("confluence");
    expect(brand("ci", "tekton")).toBe("tekton");
    expect(brand("runtime", "tekton")).toBe("tekton");
    expect(brand("cd", "argocd")).toBe("argo");
    expect(brand("runtime", "agent_sandbox")).toBe("kubernetes");
    expect(brand("runtime", "k8s_job")).toBe("kubernetes");
    expect(brand("runtime", "local_docker")).toBe("docker");
    expect(brand("mcp", "mcp")).toBe("modelcontextprotocol");
    expect(brand("firewall", "pfsense")).toBe("pfsense");
    expect(brand("network_policy", "cilium")).toBe("cilium");
    expect(brand("web_server", "nginx")).toBe("nginx");
  });

  it("demo and fake types are the demo icon", () => {
    for (const [kind, type] of [["cd", "demo"], ["mdm", "demo"], ["tracker", "fake"]]) {
      expect(connectorIconOf(kind!, type!)).toMatchObject({ kind: "lucide", demo: true });
    }
  });

  it("a type without a mark takes the icon of its kind, an unknown kind a plug", () => {
    expect(connectorIconOf("notify", "slack").kind).toBe("lucide");
    expect(connectorIconOf("tracker", "plugin").kind).toBe("lucide");
    expect(connectorIconOf("gateway", "litellm")).toEqual(connectorIconOf("gateway", "anything"));
    expect(connectorIconOf("brand-new-kind", "x").kind).toBe("lucide");
    expect(connectorIconOf("documents", "web")).not.toEqual(connectorIconOf("documents", "anything"));
  });
});
