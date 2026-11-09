import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CsvTable, JsonTree, KeyValueViewer, ValueViewer } from "@/components/typed-viewers";
import { viewerOf } from "@/lib/artifacts";
import { parseCsv } from "@/lib/csv-parse";
import type { Artifact } from "@/lib/types";

const base = { media_type: "text/plain", kind: "value" as const };

describe("the viewers of typed artifacts (ADR 0129)", () => {
  it("picks a viewer from what the list could vouch for", () => {
    expect(viewerOf({ ...base, type: "level", display: { type: "level" }, value: "S" })).toBe("value");
    expect(viewerOf({ ...base, type: "markdown", display: { type: "level" }, value: "S" })).toBe("value");
    expect(viewerOf({ ...base, type: "level", display: null, value: null })).toBe("text");
    expect(viewerOf({ ...base, type: "keyvalue" })).toBe("keyvalue");
    expect(viewerOf({ ...base, type: "csv" })).toBe("csv");
  });

  it("parses quotes, doubled quotes, newlines in a field and a semicolon file", () => {
    expect(parseCsv('a,b\n"x, y","say ""hi"""\n"two\nlines",3\n', 10)).toEqual({
      header: ["a", "b"],
      rows: [
        ["x, y", 'say "hi"'],
        ["two\nlines", "3"],
      ],
      truncated: false,
      total: 2,
    });
    expect(parseCsv("﻿a;b\r\n1;2\r\n", 10).rows).toEqual([["1", "2"]]);
  });

  it("draws a CSV as a table, a formula as text, and bounds the rows", () => {
    const rows = Array.from({ length: 501 }, (_, i) => `${i},=SUM(A1)`).join("\n");
    render(<CsvTable text={`id,formula\n${rows}\n`} />);
    const table = screen.getByRole("table", { name: "csv rows" });
    expect(within(table).getAllByRole("row")).toHaveLength(501);
    expect(within(table).getAllByText("=SUM(A1)")[0]).toBeInTheDocument();
    expect(screen.getByTestId("artifact-csv-truncated")).toHaveTextContent("first 500 rows of 501");
  });

  it("says an empty CSV is empty", () => {
    render(<CsvTable text={"\n"} />);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("draws key/value pairs as a list, and refuses what is not JSON", () => {
    const { rerender } = render(<KeyValueViewer text={'{"region":"eu-west","retries":3,"ok":true}'} />);
    expect(screen.getByTestId("artifact-keyvalue")).toHaveTextContent("regioneu-westretries3oktrue");
    rerender(<KeyValueViewer text="nope" />);
    expect(screen.queryByTestId("artifact-keyvalue")).toBeNull();
  });

  it("folds JSON and draws what does not parse as it came", () => {
    const { rerender } = render(<JsonTree text={'{"a":{"b":{"c":[1,2]}}}'} />);
    const tree = screen.getByTestId("artifact-json");
    expect(tree).not.toHaveTextContent("c:");
    fireEvent.click(within(tree).getByRole("button", { name: /b:\s*\{/ }));
    expect(tree).toHaveTextContent("c: [");
    rerender(<JsonTree text="not json {" />);
    expect(screen.getByTestId("artifact-text")).toHaveTextContent("not json {");
  });

  function metric(name: string, value: number): Artifact {
    return {
      name,
      type: "metric",
      kind: "value",
      media_type: "application/json",
      version: 1,
      digest: `sha256:${"a".repeat(64)}`,
      size: 2,
      produced_by: { run_id: "r", agent: "a", agent_version: 1 },
      value,
      display: { type: "metric", show: "tab", unit: "percent", min: 0, max: 100 },
    };
  }

  it("shows a bounded metric's position", () => {
    render(<ValueViewer artifact={metric("coverage", 87)} />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "87");
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuetext", "87%");
  });

  it("draws no gauge for a confidence: nobody measured its calibration", () => {
    render(<ValueViewer artifact={metric("confidence", 0.8)} />);
    expect(screen.queryByRole("meter")).toBeNull();
  });
});
