import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Findings, GitImport, importable, RiskBadge, sortFindings, toneOfRisk } from "@/components/skills/gate";
import { api } from "@/lib/api";
import type { SkillCandidate, SkillGitPreview } from "@/lib/types";

const SHA = "a".repeat(40);

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, previewSkillsFromGit: vi.fn(), importSkillsFromGit: vi.fn() } };
});

const candidate = (over: Partial<SkillCandidate>): SkillCandidate => ({
  path: "skills/x",
  name: "x",
  files: 1,
  bytes: 10,
  risk: "low",
  findings: [],
  ...over,
});

describe("the gate of a skill, in the console (S32-06)", () => {
  it("a risk reads as a word and a tone; a high-risk version says it waits", () => {
    expect([toneOfRisk("low"), toneOfRisk("medium"), toneOfRisk("high"), toneOfRisk(null)]).toEqual([
      "ok",
      "warn",
      "danger",
      "neutral",
    ]);
    render(<RiskBadge risk="high" reviewStatus="pending_review" />);
    expect(screen.getByTestId("risque").textContent).toContain("High risk");
    expect(screen.getByTestId("revue").textContent).toContain("Pending review");
  });

  it("the most severe finding comes first", () => {
    const sorted = sortFindings([
      { code: "licence_missing", severity: "warning", detail: "no licence" },
      { code: "prompt_injection", severity: "critical", detail: "ignore previous instructions", file: "SKILL.md", line: 4 },
    ]);
    expect(sorted.map((f) => f.code)).toEqual(["prompt_injection", "licence_missing"]);
    render(<Findings findings={sorted} />);
    expect(screen.getByTestId("constats").textContent).toContain("SKILL.md:4");
  });

  it("a refused candidate cannot be picked", () => {
    expect(importable(candidate({}))).toBe(true);
    expect(importable(candidate({ problem: "`a.ttf` is not UTF-8 text" }))).toBe(false);
    expect(importable(candidate({ name: null }))).toBe(false);
  });

  it("the import pins the commit the preview resolved, never the branch typed", async () => {
    const preview: SkillGitPreview = {
      repo: "https://github.com/anthropics/skills",
      ref: "main",
      sha: SHA,
      path: "skills",
      allowed: true,
      skills: [candidate({ name: "pdf", path: "skills/pdf", risk: "high" }), candidate({ name: "fonts", path: "skills/fonts", problem: "binary" })],
    };
    vi.mocked(api.previewSkillsFromGit).mockResolvedValue(preview);
    vi.mocked(api.importSkillsFromGit).mockResolvedValue([
      { slug: "pdf", status: "active", latest_version: 1, risk: "high", review_status: "pending_review" },
    ]);
    render(<GitImport org="varga" />);
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(screen.getByTestId("apercu")).toBeTruthy());
    expect(api.previewSkillsFromGit).toHaveBeenCalledWith("varga", {
      repo_url: "https://github.com/anthropics/skills",
      ref: "main",
      path: "skills",
    });
    const cases = screen.getAllByRole("checkbox");
    expect((cases[1] as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(cases[0]!);
    fireEvent.click(screen.getByRole("button", { name: /^Import 1/ }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("second reviewer"));
    expect(api.importSkillsFromGit).toHaveBeenCalledWith("varga", {
      repo_url: "https://github.com/anthropics/skills",
      sha: SHA,
      path: "skills",
      skills: ["pdf"],
    });
  });
});
