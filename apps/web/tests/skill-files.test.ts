import { describe, expect, it } from "vitest";
import { isBinarySkillFile, skillFileLabel } from "@/lib/skill-files";

describe("skill files", () => {
  it("decides binary by the extension alone", () => {
    expect(isBinarySkillFile("assets/logo.PNG")).toBe(true);
    expect(isBinarySkillFile("fonts/a.woff2")).toBe(true);
    expect(isBinarySkillFile("assets/logo.svg")).toBe(false);
    expect(isBinarySkillFile("SKILL.md")).toBe(false);
    expect(isBinarySkillFile("png")).toBe(false);
  });

  it("shows the decoded size of a binary, never its base64", () => {
    // 4 bytes -> "AAECAw==" ; 3 bytes -> "AAEC"
    expect(skillFileLabel("a.png", "AAECAw==")).toBe("binary, 4 bytes");
    expect(skillFileLabel("a.png", "AAEC")).toBe("binary, 3 bytes");
    expect(skillFileLabel("a.png", undefined)).toBe("binary, 0 bytes");
    expect(skillFileLabel("a.md", "AAEC")).toBeNull();
  });
});
