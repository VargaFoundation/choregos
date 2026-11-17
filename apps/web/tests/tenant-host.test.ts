import { describe, expect, it } from "vitest";
import { validHost } from "@/lib/tenant-host";

describe("the host a visitor reached the console on (ADR 0175)", () => {
  it.each([
    ["agentic.client.com", "agentic.client.com"],
    ["Agentic.Client.COM:8443", "agentic.client.com"],
    ["agentic.client.com.", "agentic.client.com"],
    ["agentic.client.com, edge.internal", "agentic.client.com"],
    ["xn--caf-dma.example.com", "xn--caf-dma.example.com"],
  ])("reads %s", (raw, expected) => expect(validHost(raw)).toBe(expected));

  it.each(["localhost", "10.1.2.3", "[::1]:3000", "a..b.com", "-a.example.com", "café.example.com", "a b.example.com", "a.example.com/x", "", null, undefined])(
    "refuses %s",
    (raw) => expect(validHost(raw)).toBeNull(),
  );

  it("refuses a name over 253 characters or a label over 63", () => {
    expect(validHost(Array(30).fill("abcdefghi").join("."))).toBeNull();
    expect(validHost(`${"a".repeat(64)}.example.com`)).toBeNull();
  });
});
