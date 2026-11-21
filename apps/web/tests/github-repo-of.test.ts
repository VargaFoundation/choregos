import { describe, expect, it } from "vitest";
import { githubRepoOf } from "@/components/github-repo-field";

describe("the repository a GitHub-served connector reaches (ADR 0073, #2033)", () => {
  it("reads `repo` for the forge connectors and `gitops_repo` for Argo CD", () => {
    expect(githubRepoOf("github", { repo: "acme/app" })).toBe("acme/app");
    expect(githubRepoOf("github-actions", { repo: "acme/ci" })).toBe("acme/ci");
    expect(
      githubRepoOf("argocd", { gitops_repo: "acme/gitops", repo: "x/y" }),
    ).toBe("acme/gitops");
  });

  it("names nothing for a type the App does not serve, or an empty value", () => {
    expect(githubRepoOf("gitlab", { repo: "acme/app" })).toBeNull();
    expect(githubRepoOf("argocd", {})).toBeNull();
    expect(githubRepoOf("github", undefined)).toBeNull();
  });
});
